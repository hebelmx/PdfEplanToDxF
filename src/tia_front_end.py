#!/usr/bin/env python3
"""tia_front_end.py — Siemens TIA Portal front-end (parser core).

Mirrors the Rockwell front-end (logix_to_eplan_csv.load_l5x + collectors) but
reads a TIA Portal export, producing the SAME vendor-neutral elements
(`Module` / `IoPoint` from logix_to_eplan_csv) so the renderer needs no vendor
branches. `build_tia_project` (in plc_ir.py) wraps this and emits a `PlcProject`
with `source_vendor="siemens"`.

Inputs (all stdlib-parsed; STANDARD LIBRARY ONLY):
  * <project>_IO_Channels.xml — THE primary point source.
      Stations > Station[Name] > Rack[Name] > Module[Name]
        > IOChannel[Number] > {Address, Tag}
    `Address` is the REAL absolute Siemens address (%I150.0, %Q1500.0, %IW64,
    %QW64). Empty/whitespace <Tag> == a SPARE (RESERVA), mirroring Rockwell.
  * PLCTags*.xlsx — optional tag table for descriptions/comments. Joined on
    Tag == xlsx.Name. Parsed with zipfile + xml.etree only (shared strings
    resolved). Missing/empty Comment => description "" (NEVER invent).

Design decisions (see TIA-1 item):
  * REAL absolute addresses are used directly — never synthesized. Each module's
    in_byte_base/out_byte_base (digital) or an_in_word_base/an_out_word_base
    (analog) is set to the module's LOWEST byte/word for that direction, and
    each IoPoint.index is set so logix_to_eplan_csv.eplan_address reproduces the
    EXACT real address:
        digital: index = (byte - base) * 8 + bit
                 -> eplan_address = I{base + index//8}.{index%8} == byte.bit
        analog:  index = (word - base) // 2
                 -> eplan_address = IW{base + index*2} == word
    The raw address string is also stored in IoPoint.logix_address for
    cross-check.
  * kind inference: %I.bit->DI, %Q.bit->DO, %IW->AI, %QW->AO.
  * Mixed DI/DQ physical modules (e.g. F-DQ1500 here carries both %Q1500.x
    outputs AND %I1500.x inputs): Module.kind is singular, so a physical module
    carrying both directions is SPLIT into two IR Module entries by direction
    (a DI/AI part + a DO/AO part) sharing the physical name via the `name` and a
    common `parent` (the rack), each with its own kind and own byte/word base.
    The single-kind renderer therefore still works. (Reported as a surprise.)
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET

from logix_to_eplan_csv import Module, IoPoint


# --------------------------------------------------------------------------
# Address parsing
# --------------------------------------------------------------------------
# %I150.0  %Q1500.3  %IW64  %QW128  (also tolerant of lowercase / missing %)
_ADDR_RE = re.compile(
    r"^\s*%?\s*"
    r"(?P<area>[IQ])"
    r"(?P<word>W)?"
    r"(?P<num>\d+)"
    r"(?:\.(?P<bit>\d+))?"
    r"\s*$",
    re.IGNORECASE,
)


def parse_address(addr: str):
    """Parse a real absolute Siemens I/O address.

    Returns a dict {direction, analog, byte, bit} for digital
    (%I150.0 -> {'I', False, 150, 0}) or {direction, analog, word} for analog
    (%IW64 -> {'I', True, 64}). Returns None if it isn't an I/O address
    (e.g. %ID1000 double-word, %MD, merker/datablock addresses).
    """
    if not addr:
        return None
    m = _ADDR_RE.match(addr)
    if not m:
        return None
    area = m.group("area").upper()
    direction = "I" if area == "I" else "O"
    if m.group("word"):
        if m.group("bit") is not None:
            return None  # %IW64.0 is malformed
        return {"direction": direction, "analog": True, "word": int(m.group("num"))}
    # digital: a bit index is required for a channel address
    if m.group("bit") is None:
        return None
    return {
        "direction": direction,
        "analog": False,
        "byte": int(m.group("num")),
        "bit": int(m.group("bit")),
    }


def infer_kind(parsed: dict) -> str:
    """%I.bit->DI, %Q.bit->DO, %IW->AI, %QW->AO."""
    if parsed["analog"]:
        return "AI" if parsed["direction"] == "I" else "AO"
    return "DI" if parsed["direction"] == "I" else "DO"


def is_spare(tag_text: str | None) -> bool:
    """Empty/whitespace Tag == a SPARE (RESERVA). Mirrors Rockwell spare semantics."""
    return not (tag_text or "").strip()


# --------------------------------------------------------------------------
# xlsx tag table (stdlib only: zipfile + xml.etree)
# --------------------------------------------------------------------------
_X_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _cell_text(cell, strings: list[str]) -> str:
    """Resolve a worksheet <c> cell to text.

    Handles shared strings (t="s" -> sharedStrings si[N]), inline strings
    (t="inlineStr" / t="str"), and plain numeric/value cells. NEVER invents:
    an absent value yields "".
    """
    t = cell.get("t")
    if t == "s":
        v = cell.find(_X_NS + "v")
        if v is None or v.text is None:
            return ""
        try:
            return strings[int(v.text)]
        except (ValueError, IndexError):
            return ""
    if t == "inlineStr":
        is_ = cell.find(_X_NS + "is")
        if is_ is None:
            return ""
        return "".join(x.text or "" for x in is_.iter(_X_NS + "t"))
    v = cell.find(_X_NS + "v")
    return v.text if (v is not None and v.text is not None) else ""


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    """Resolve sharedStrings.xml into a list indexed by si position.

    Each <si> may hold a single <t> or several <r><t> runs — concatenate all
    descendant <t> text (the project memory claiming this file is empty is
    WRONG; it has real entries)."""
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(raw)
    return ["".join(t.text or "" for t in si.iter(_X_NS + "t"))
            for si in root.findall(_X_NS + "si")]


def _worksheet_name(zf: zipfile.ZipFile) -> str:
    """Find the single/first worksheet part path inside the workbook zip."""
    for cand in ("xl/worksheets/sheet.xml", "xl/worksheets/sheet1.xml"):
        if cand in zf.namelist():
            return cand
    for n in zf.namelist():
        if n.startswith("xl/worksheets/") and n.endswith(".xml"):
            return n
    raise KeyError("no worksheet part in xlsx")


def load_tag_table(path: str) -> dict[str, dict]:
    """Parse a PLCTags*.xlsx into {Name -> {address, comment}}.

    Columns are taken from the header row by NAME ("Name", "Logical Address",
    "Comment") so column order is not hardcoded. Returns {} on any structural
    problem (never raises into the caller; never invents data).
    """
    table: dict[str, dict] = {}
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        return table
    with zf:
        strings = _read_shared_strings(zf)
        try:
            sheet = ET.fromstring(zf.read(_worksheet_name(zf)))
        except (KeyError, ET.ParseError):
            return table
        data = sheet.find(_X_NS + "sheetData")
        if data is None:
            return table
        rows = data.findall(_X_NS + "row")
        if not rows:
            return table
        header = [_cell_text(c, strings) for c in rows[0].findall(_X_NS + "c")]
        idx = {h.strip().lower(): i for i, h in enumerate(header)}
        i_name = idx.get("name")
        i_addr = idx.get("logical address")
        i_comment = idx.get("comment")
        if i_name is None:
            return table
        for row in rows[1:]:
            vals = [_cell_text(c, strings) for c in row.findall(_X_NS + "c")]

            def get(i):
                return vals[i] if (i is not None and i < len(vals)) else ""

            name = get(i_name).strip()
            if not name:
                continue
            table[name] = {
                "address": get(i_addr).strip(),
                "comment": get(i_comment).strip(),
            }
    return table


# --------------------------------------------------------------------------
# IO_Channels.xml -> Module / IoPoint IR
# --------------------------------------------------------------------------
def _split_key(direction: str, analog: bool) -> str:
    """A physical module is split into IR modules per (direction, analog)."""
    if analog:
        return "AI" if direction == "I" else "AO"
    return "DI" if direction == "I" else "DO"


def build_modules_and_points(
    xml_path: str, tag_table: dict[str, dict] | None = None
):
    """Parse IO_Channels.xml into (station_name, modules, io_mods, points, skipped).

    modules:  dict[name -> Module]  (split per direction; suffixed name when a
              physical module carries >1 (direction,kind) part)
    io_mods:  ordered list of the same Module objects (rack-ordered)
    points:   list[IoPoint] for TAGGED channels (spares are not bound to a tag,
              mirroring Rockwell where unmapped points are not emitted as points)
    skipped:  list of (tag-or-address, raw-address, reason) — spares + any
              channel whose address could not be parsed.
    """
    tag_table = tag_table or {}
    tree = ET.parse(xml_path)
    root = tree.getroot()
    station = root.find("Station")
    station_name = station.get("Name", "") if station is not None else ""

    # First pass: collect parsed channels grouped per physical module + split key,
    # so we can compute each IR module's lowest byte/word base before emitting.
    # group key -> {rack, phys, direction, analog, kind, channels:[...], lowest}
    groups: dict[tuple, dict] = {}
    order: list[tuple] = []  # preserves first-seen order for stable io_mods
    skipped: list[tuple] = []

    for rack in (station.findall("Rack") if station is not None else []):
        rack_name = rack.get("Name", "")
        for me in rack.findall("Module"):
            phys = me.get("Name", "")
            for ch in me.findall("IOChannel"):
                addr_el = ch.find("Address")
                raw = (addr_el.text or "").strip() if addr_el is not None else ""
                tag_el = ch.find("Tag")
                tag_text = (tag_el.text or "") if tag_el is not None else ""
                parsed = parse_address(raw)
                if parsed is None:
                    skipped.append((tag_text.strip() or phys, raw, "unparsable-address"))
                    continue
                skey = _split_key(parsed["direction"], parsed["analog"])
                gkey = (rack_name, phys, skey)
                if gkey not in groups:
                    groups[gkey] = {
                        "rack_name": rack_name,
                        "phys": phys,
                        "direction": parsed["direction"],
                        "analog": parsed["analog"],
                        "kind": infer_kind(parsed),
                        "channels": [],
                    }
                    order.append(gkey)
                groups[gkey]["channels"].append((parsed, raw, tag_text))

    # Decide IR module names: if a physical module produced >1 split-part,
    # suffix the IR module name with the kind so names stay unique while the
    # `parent` (rack) ties them back to the same physical slot.
    phys_split_count: dict[tuple, int] = {}
    for (rack_name, phys, skey) in order:
        phys_split_count[(rack_name, phys)] = phys_split_count.get((rack_name, phys), 0) + 1

    modules: dict[str, Module] = {}
    io_mods: list[Module] = []
    points: list[IoPoint] = []

    for gkey in order:
        g = groups[gkey]
        rack_name, phys, skey = gkey
        analog = g["analog"]
        direction = g["direction"]
        chans = g["channels"]

        # module base = lowest byte (digital) / word (analog) for this direction
        if analog:
            base = min(p["word"] for p, _, _ in chans)
        else:
            base = min(p["byte"] for p, _, _ in chans)

        split = phys_split_count[(rack_name, phys)] > 1
        ir_name = f"{phys} [{g['kind']}]" if split else phys
        mod = Module(
            name=ir_name,
            catalog="",
            parent=rack_name,
            slot=None,
            kind=g["kind"],
            points=len(chans),
            rack=0,
        )
        if analog:
            if direction == "I":
                mod.an_in_word_base = base
            else:
                mod.an_out_word_base = base
        else:
            if direction == "I":
                mod.in_byte_base = base
            else:
                mod.out_byte_base = base

        modules[ir_name] = mod
        io_mods.append(mod)

        for parsed, raw, tag_text in chans:
            if analog:
                index = (parsed["word"] - base) // 2
            else:
                index = (parsed["byte"] - base) * 8 + parsed["bit"]

            if is_spare(tag_text):
                # spare channel: RESERVA, not bound to a tag — mirror Rockwell
                skipped.append(("RESERVA", raw, "spare"))
                continue

            tag = tag_text.strip()
            meta = tag_table.get(tag, {})
            description = meta.get("comment", "")  # NEVER invent
            points.append(
                IoPoint(
                    tag=tag,
                    module=mod,
                    direction=direction,
                    index=index,
                    analog=analog,
                    radix="",
                    description=description,
                    logix_address=raw,  # raw real address for cross-check
                    scope=station_name,
                )
            )

    return station_name, modules, io_mods, points, skipped
