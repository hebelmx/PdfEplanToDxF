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


def _is_nondevice_signal(tag: str, description: str) -> bool:
    """True when a TIA channel is NOT a field device itself and so must stay a
    generic terminal — never get a matched device symbol (Abel, 2026-06-17).

    Two confirmed non-device classes in the TIA tag descriptions:
      * supply-voltage MONITORING of a device — tag prefix ``VS_`` /
        description starting ``Vsupply …`` (e.g. ``VS_buv_ema`` "Vsupply
        Emergency Stop"); it monitors the device's supply, it is not the device.
      * a PERMIT / interlock signal — description starting ``Permission to …``
        (e.g. ``buv_p2open`` "Permission to Open UV Door"); a logic permit, not
        a physical switch.

    Deliberately narrow (matches the START of the description) so a genuine
    device whose description merely CONTAINS the word — e.g. ``uv_slpermission``
    "Light Signal Permission Door" (a real pilot light) — is NOT suppressed.
    Suppression only ever DROPS a symbol (→ generic), so it can never invent."""
    t = (tag or "").strip().lower()
    d = (description or "").strip().lower()
    return (t.startswith("vs_")
            or d.startswith("vsupply")
            or d.startswith("permission to"))


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
    xml_path: str,
    tag_table: dict[str, dict] | None = None,
    aml_path: str | None = None,
):
    """Parse IO_Channels.xml into (station_name, modules, io_mods, points, skipped).

    modules:  dict[name -> Module]  (split per direction; suffixed name when a
              physical module carries >1 (direction,kind) part)
    io_mods:  ordered list of the same Module objects (rack-ordered)
    points:   list[IoPoint] for TAGGED channels (spares are not bound to a tag,
              mirroring Rockwell where unmapped points are not emitted as points)
    skipped:  list of (tag-or-address, raw-address, reason) — spares + any
              channel whose address could not be parsed.

    When `aml_path` is given, the TIA CAx/AML hardware map (order number +
    PROFINET NetworkAddress) is joined onto each Module by its physical name
    (the rack-child <Module Name> matches the .aml rack-child module Name). The
    join fills Module.catalog and Module.network_address. A module with no .aml
    match keeps catalog "" / network_address None — NEVER invented. Split IR
    modules (e.g. "F-DQ1500 [DI]") strip the kind suffix back to the physical
    name before looking up, so both halves of a split share the same hardware.
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
                    no_symbol=_is_nondevice_signal(tag, description),
                )
            )

    # --- Optional .aml hardware join: fill catalog (order#) + network_address.
    # Join on the physical module name. IR module names for a split physical
    # module carry a " [KIND]" suffix (e.g. "F-DQ1500 [DI]"); strip it to recover
    # the physical name the .aml uses. NEVER invent: no match leaves catalog ""
    # and network_address None.
    if aml_path:
        import tia_aml

        hw = tia_aml.hardware_for_station(tia_aml.parse_aml(aml_path), station_name)
        for mod in io_mods:
            phys = _physical_name(mod.name)
            info = hw.get(phys)
            if not info:
                continue
            order = info.get("order_number", "")
            if order:
                mod.catalog = order  # masked '?' digits kept verbatim
            addr = info.get("network_address")
            if addr:
                mod.network_address = addr
            # physical slot from the .aml PositionNumber — fixes the I/O folio
            # titles' "Slot None". Both halves of a split physical module share
            # the same slot. NEVER invented: no PositionNumber => slot stays None.
            slot = info.get("slot")
            if slot is not None:
                mod.slot = slot

    return station_name, modules, io_mods, points, skipped


_SPLIT_SUFFIX_RE = re.compile(r"\s*\[(?:DI|DO|AI|AO)\]\s*$")


def _physical_name(ir_name: str) -> str:
    """Recover the physical module name from an IR module name by stripping a
    trailing split-kind suffix ('F-DQ1500 [DI]' -> 'F-DQ1500'). Names without a
    suffix pass through unchanged."""
    return _SPLIT_SUFFIX_RE.sub("", ir_name).strip()


# ==========================================================================
# E6: full-plant DISTRIBUTED I/O front-end (NEW path; the single-station
# build_modules_and_points above is left UNTOUCHED).
#
# This path synthesizes each module's real channel addresses from the FULL
# .aml hardware map (parse_aml) — there is NO per-station IO_Channels.xml for
# the drops — then joins channels -> tags by parsed address against the
# owning PLC's tag table. The output mirrors the approved Q100 floor exactly
# (88 ch / 48 mapped / 40 RESERVA), so it reuses every convention of
# build_modules_and_points: lowest-byte/word base, index math, raw address in
# logix_address, comment-or-"" description, _is_nondevice_signal, RESERVA
# spares appended to skipped, and the " [KIND]" split-name suffix.
# ==========================================================================


def index_tag_table_by_address(tag_table: dict[str, dict]) -> dict[tuple, tuple]:
    """Pre-index a {Name: {address, comment}} tag table by PARSED address.

    Returns {key -> (name, comment)} where key is the parse_address-normalized
    identity of the tag's address:
        digital -> (direction, False, byte, bit)
        analog  -> (direction, True,  word, None)
    Tags whose address does not parse as an I/O channel (merker, datablock,
    %ID/%QD double-words, blank) are dropped from the index — they can never
    match a synthesized channel. On a (rare) duplicate address the FIRST tag in
    iteration order wins (deterministic for a given table); never invented.
    """
    index: dict[tuple, tuple] = {}
    for name, meta in tag_table.items():
        key = _addr_key(parse_address((meta or {}).get("address", "")))
        if key is None:
            continue
        if key not in index:
            index[key] = (name, (meta or {}).get("comment", ""))
    return index


def _addr_key(parsed: dict | None) -> tuple | None:
    """Normalize a parse_address result to a hashable identity key, or None."""
    if parsed is None:
        return None
    if parsed["analog"]:
        return (parsed["direction"], True, parsed["word"], None)
    return (parsed["direction"], False, parsed["byte"], parsed["bit"])


def _synthesize_channels(type_name: str, addresses: list, channels: int,
                         module_name: str) -> list[tuple[str, int, int, bool]]:
    """Synthesize a module's (split-key, raw-address) channel list from its
    .aml type_name + address ranges.

    Returns a list of (skey, raw_address, _ord, _ord2) tuples — actually
    (skey, raw_address) pairs carried as (skey, raw, 0, 0) for a uniform shape;
    callers only use skey + raw. Each entry is ONE synthesized channel slot at a
    REAL address; mapping/spare is decided later by the tag join. NEVER invents
    an address outside the declared ranges.

    Layout rules (VALIDATED against Abel's approved Q100 — see module header):
      * Standard digital  (DI/DQ, not F-): one part, capacity = channels (== Length
        bits), addresses %{I|Q}{start + i//8}.{i%8} from the matching io_type range.
      * F-DI…             : one DI part, capacity = 2*channels (value + value-status
        byte), ALL %I{Instart + i//8}.{i%8}; the Output (PROFIsafe control) range
        is ignored.
      * F-DQ…             : DO part (capacity = channels, %Q from the Output range)
        PLUS a DI-readback part (capacity = channels, %I from the Input range).
      * Analog            : per range, capacity = Length//16 words,
        %{I|Q}W{start + 2*j}.
    """
    out: list[tuple[str, int, int, bool]] = []
    inputs = [(s, ln) for (io, s, ln) in addresses if io == "Input"]
    outputs = [(s, ln) for (io, s, ln) in addresses if io == "Output"]
    tn = (type_name or "").strip()

    def digital(skey: str, start: int, cap: int):
        for i in range(cap):
            area = "I" if skey == "DI" else "Q"
            out.append((skey, f"%{area}{start + i // 8}.{i % 8}", 0, 0))

    def analog(skey: str, start: int, words: int):
        for j in range(words):
            area = "I" if skey == "AI" else "Q"
            out.append((skey, f"%{area}W{start + 2 * j}", 0, 0))

    # Analog detection must NOT rely on the type_name prefix alone: a real
    # SM 1232 AQ2 analog-output module is named "SM 1232 AQ2" (the "AQ2" is at the
    # END), so a prefix test misclassifies it as digital and drops its %QW tag.
    # An analog channel is a 16-bit WORD, so the module's total declared Length is
    # 16*channels (AQ2: 32==16*2; AI 4x: 64==16*4) — whereas a standard digital
    # module has Length==channels (ratio 1) and an F-module (caught above by the
    # "F-" prefix) has a PROFIsafe-inflated ratio. Structure, not naming.
    total_len = sum(ln for (_io, _s, ln) in addresses)
    is_analog_type = (
        tn[:2] in ("AI", "AQ")
        or tn.startswith(("AI-", "AQ-"))
        or (channels > 0 and total_len == 16 * channels)
    )

    if tn.startswith("F-DI"):
        # value byte + value-status byte => 2*channels DI, all in the Input area
        if inputs:
            digital("DI", inputs[0][0], 2 * channels)
    elif tn.startswith("F-DQ"):
        # DO part from the Output range + a DI readback part from the Input range
        if outputs:
            digital("DO", outputs[0][0], channels)
        if inputs:
            digital("DI", inputs[0][0], channels)
    elif is_analog_type:
        for (s, ln) in inputs:
            analog("AI", s, ln // 16)
        for (s, ln) in outputs:
            analog("AO", s, ln // 16)
    else:
        # standard digital: direction from the single declared range's io_type;
        # capacity = channels (== Length bits for these ST modules).
        if inputs:
            digital("DI", inputs[0][0], channels)
        if outputs:
            digital("DO", outputs[0][0], channels)
    return out


def _synthesize_cpu_onboard(addresses: list) -> list[tuple[str, str, int, int]]:
    """Synthesize ONLY the standard low-address onboard I/O of a 1200-class CPU
    (the 1214C "PLC_1"). Conservative by design — see brief.

    Enumerated ranges:
      * Input  0/16  -> %I0.0..%I1.7   (16 DI)
      * Output 0/16  -> %Q0.0..%Q1.7   (16 DO)
      * Input  64/32 -> %IW64, %IW66   (2 AI words)
    ALL other ranges (start >= 1000 — HSC/pulse %ID/%QD double-words that
    parse_address returns None for) yield NO synthesized digital channels here;
    real tags at such addresses are instead picked up by the tag-sweep in
    build_distributed_stations. NEVER invents a channel.
    """
    out: list[tuple[str, str, int, int]] = []
    for (io, start, length) in addresses:
        if io == "Input" and start == 0 and length == 16:
            for i in range(16):
                out.append(("DI", f"%I{start + i // 8}.{i % 8}", 0, 0))
        elif io == "Output" and start == 0 and length == 16:
            for i in range(16):
                out.append(("DO", f"%Q{start + i // 8}.{i % 8}", 0, 0))
        elif io == "Input" and start == 64 and length == 32:
            for j in range(2):
                out.append(("AI", f"%IW{start + 2 * j}", 0, 0))
        # else: HSC/pulse %ID/%QD ranges (start >= 1000) -> nothing synthesized.
    return out


def _direction_of(skey: str) -> str:
    return "I" if skey in ("DI", "AI") else "O"


def _module_coverage(synth_addrs: list[str], addr_index: dict[tuple, tuple]) -> int:
    """How many of these synthesized channel addresses a given pre-indexed tag
    table covers (used for owning-table selection)."""
    n = 0
    for raw in synth_addrs:
        if _addr_key(parse_address(raw)) in addr_index:
            n += 1
    return n


def build_distributed_stations(aml_path: str, tag_tables: dict[str, dict]) -> list:
    """Build the vendor-neutral IR for ALL stations of the plant from the FULL
    .aml hardware map joined to the per-PLC tag tables. PURE (tables passed in).

    Args:
      aml_path:   the full CAx/AML export (parse_aml + profinet_nodes source).
      tag_tables: {label -> {Name: {address, comment}}} — one entry per sibling
                  PLCTags*.xlsx (label is the xlsx stem, e.g. "S71500"/"S71200").

    Returns an ORDERED list of dicts (heaviest-PLC-first; see brief), one per
    station, each:
        station_name      str
        owning_plc_label  str   the tag-table label that best covers the station
        modules           dict[name -> Module]
        io_mods           list[Module]   (rack/document ordered)
        points            list[IoPoint]  (tagged channels only)
        skipped           list[tuple]    (RESERVA spares + unparsable)
        ambiguous_owner   bool   True only on a genuine coverage TIE (raise it)

    NEVER invents: a channel address comes from the real .aml enumeration; a
    description from the real comment (or ""); an unmatched channel is RESERVA.
    Returns [] on a missing/!aml (parse_aml raises only on a true parse error,
    which the public plc_ir wrapper guards).
    """
    import tia_aml

    hw = tia_aml.parse_aml(aml_path)
    nodes = tia_aml.profinet_nodes(aml_path)

    # Pre-index every candidate tag table by parsed address for fast coverage +
    # join. Deterministic table order (sorted by label) for tie auditing.
    indexed = {lbl: index_tag_table_by_address(tbl)
               for lbl, tbl in tag_tables.items()}

    # --- group hw modules by station, preserving .aml document order (slot) ---
    stations: dict[str, list[tuple[str, dict]]] = {}
    for (st, mod), info in hw.items():
        stations.setdefault(st, []).append((mod, info))
    for st in stations:
        stations[st].sort(key=lambda mi: (mi[1].get("slot") is None,
                                          mi[1].get("slot") or 0))

    # --- synthesize each station's channel list (skip CPU/head/server) -------
    # station -> list of (phys_name, info, [(skey, raw, _, _), ...])
    synth_by_station: dict[str, list[tuple[str, dict, list]]] = {}
    for st, mods in stations.items():
        per_mod: list[tuple[str, dict, list]] = []
        for mod, info in mods:
            addresses = info.get("addresses") or []
            if info.get("device_item_type") == "CPU":
                # Only the 1200-class onboard CPU carries real low-address I/O.
                ch = _synthesize_cpu_onboard(addresses)
                if ch:
                    per_mod.append((mod, info, ch))
                continue
            if not addresses:
                continue  # head/server module — no I/O
            ch = _synthesize_channels(info.get("type_name", ""), addresses,
                                      info.get("channels", 0), mod)
            if ch:
                per_mod.append((mod, info, ch))
        synth_by_station[st] = per_mod

    # --- choose the owning tag table per station (highest coverage) ----------
    owner: dict[str, str] = {}
    ambiguous: dict[str, bool] = {}
    for st, per_mod in synth_by_station.items():
        all_addrs = [raw for (_m, _i, chans) in per_mod for (_sk, raw, _a, _b) in chans]
        best_lbl, best_cov, tie = None, -1, False
        for lbl in sorted(indexed):
            cov = _module_coverage(all_addrs, indexed[lbl])
            if cov > best_cov:
                best_cov, best_lbl, tie = cov, lbl, False
            elif cov == best_cov and best_cov > 0:
                tie = True
        owner[st] = best_lbl
        # a genuine tie among >0-coverage tables is ambiguous — flag, never guess
        ambiguous[st] = tie and best_cov > 0

    # --- order stations: heaviest PLC first, CPU-local station first within ---
    ordered_names = _order_stations(stations, owner, nodes)

    # --- build IR per station (in order) -------------------------------------
    result: list = []
    for st in ordered_names:
        addr_index = indexed.get(owner.get(st), {})
        modules, io_mods, points, skipped = _build_station_ir(
            st, synth_by_station[st], addr_index)
        # join the .aml hardware (catalog/order#, network_address, slot)
        station_hw = tia_aml.hardware_for_station(hw, st)
        for mod in io_mods:
            info = station_hw.get(_physical_name(mod.name))
            if not info:
                continue
            if info.get("order_number"):
                mod.catalog = info["order_number"]
            if info.get("network_address"):
                mod.network_address = info["network_address"]
            if info.get("slot") is not None:
                mod.slot = info["slot"]
        result.append({
            "station_name": st,
            "owning_plc_label": owner.get(st),
            "modules": modules,
            "io_mods": io_mods,
            "points": points,
            "skipped": skipped,
            "ambiguous_owner": ambiguous.get(st, False),
        })
    return result


def _build_station_ir(station_name: str,
                      per_mod: list[tuple[str, dict, list]],
                      addr_index: dict[tuple, tuple]):
    """Turn one station's synthesized channels into Module/IoPoint IR, mirroring
    build_modules_and_points' conventions (split per (direction,analog), lowest
    byte/word base, index math, raw address, comment-or-"", _is_nondevice_signal,
    RESERVA spares -> skipped). Returns (modules, io_mods, points, skipped)."""
    # group synthesized channels per (phys, skey) into the IR-module split parts
    groups: dict[tuple, dict] = {}
    order: list[tuple] = []
    for phys, _info, chans in per_mod:
        for (skey, raw, _a, _b) in chans:
            parsed = parse_address(raw)
            if parsed is None:
                continue  # synthesized addresses always parse; guard anyway
            gkey = (phys, skey)
            if gkey not in groups:
                groups[gkey] = {
                    "phys": phys,
                    "direction": parsed["direction"],
                    "analog": parsed["analog"],
                    "kind": skey,
                    "channels": [],
                }
                order.append(gkey)
            groups[gkey]["channels"].append((parsed, raw))

    phys_split_count: dict[str, int] = {}
    for (phys, _skey) in order:
        phys_split_count[phys] = phys_split_count.get(phys, 0) + 1

    modules: dict[str, Module] = {}
    io_mods: list[Module] = []
    points: list[IoPoint] = []
    skipped: list[tuple] = []

    for gkey in order:
        g = groups[gkey]
        phys, skey = gkey
        analog = g["analog"]
        direction = g["direction"]
        chans = g["channels"]

        if analog:
            base = min(p["word"] for p, _ in chans)
        else:
            base = min(p["byte"] for p, _ in chans)

        split = phys_split_count[phys] > 1
        ir_name = f"{phys} [{g['kind']}]" if split else phys
        mod = Module(
            name=ir_name,
            catalog="",
            parent=station_name,
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

        for parsed, raw in chans:
            if analog:
                index = (parsed["word"] - base) // 2
            else:
                index = (parsed["byte"] - base) * 8 + parsed["bit"]

            match = addr_index.get(_addr_key(parsed))
            if match is None:
                # no tag at this synthesized address -> RESERVA spare
                skipped.append(("RESERVA", raw, "spare"))
                continue
            tag, comment = match
            description = comment or ""  # NEVER invent
            points.append(
                IoPoint(
                    tag=tag,
                    module=mod,
                    direction=direction,
                    index=index,
                    analog=analog,
                    radix="",
                    description=description,
                    logix_address=raw,
                    scope=station_name,
                    no_symbol=_is_nondevice_signal(tag, description),
                )
            )

    return modules, io_mods, points, skipped


def _order_stations(stations: dict, owner: dict, nodes: list) -> list[str]:
    """Order stations heaviest-PLC-first (1500-class CPU before 1200-class),
    and within a PLC put the CPU-local station first then drops by ascending
    station IP / name. Derives CPU class from each station's controller node via
    the shared profinet_nodes list. Deterministic; never invented."""
    # map ip -> controller type for controller nodes
    ctrl_type_by_ip = {ip: typ for (ip, _n, typ, _m, is_ctrl) in nodes if is_ctrl}

    def station_ip(st: str) -> str | None:
        for _mod, info in stations[st]:
            if info.get("network_address"):
                return info["network_address"]
        return None

    def cpu_rank(typ: str | None) -> int:
        """Lower rank = heavier PLC = sorts first."""
        t = (typ or "")
        if "1500" in t or "1512" in t or "151" in t:
            return 0
        if "1200" in t or "1214" in t or "121" in t:
            return 1
        return 2  # unknown class sorts last (deterministic), never invented

    # for each station: does its IP host a controller node (CPU-local)? and the
    # owning-PLC class rank (derived from the station's own CPU module if it has
    # one, else from any controller at the station IP).
    def station_cpu_type(st: str) -> str | None:
        # a station that itself contains a CPU module names that CPU type
        for _mod, info in stations[st]:
            if info.get("device_item_type") == "CPU":
                return info.get("type_name") or None
        # else: the controller node sharing this station's IP (drops behind a CPU
        # share the controller's IP) — but ET200SP drops each have their own IP,
        # so fall back to the owning PLC group's CPU below.
        ip = station_ip(st)
        return ctrl_type_by_ip.get(ip) if ip else None

    # group stations by owning PLC label so all drops of a PLC share its class
    plc_of = owner  # owning tag-table label is the PLC identity
    # class rank per PLC label = the heaviest (min) CPU rank among its stations
    plc_rank: dict[str, int] = {}
    for st in stations:
        lbl = plc_of.get(st)
        r = cpu_rank(station_cpu_type(st))
        if lbl not in plc_rank or r < plc_rank[lbl]:
            plc_rank[lbl] = r

    def is_cpu_local(st: str) -> bool:
        # CPU-local == the station physically contains the controller CPU module
        return any(info.get("device_item_type") == "CPU"
                   for _mod, info in stations[st])

    def sort_key(st: str):
        lbl = plc_of.get(st)
        return (
            plc_rank.get(lbl, 99),          # heaviest PLC first
            str(lbl),                       # stable PLC grouping
            0 if is_cpu_local(st) else 1,   # CPU-local station first
            _ip_sort_tuple(station_ip(st)), # then by ascending station IP
            st,                             # then name (final tie-break)
        )

    return sorted(stations.keys(), key=sort_key)


def _ip_sort_tuple(ip: str | None) -> tuple:
    """Numeric IPv4 sort tuple; None / non-numeric sorts last (deterministic)."""
    if not ip:
        return (1,)
    try:
        return (0,) + tuple(int(x) for x in ip.split("."))
    except (ValueError, AttributeError):
        return (1,)
