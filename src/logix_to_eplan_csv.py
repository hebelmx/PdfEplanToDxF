#!/usr/bin/env python3
"""
logix_to_eplan_csv.py — ControlLogix L5X -> EPLAN P8 PLC import CSV.

Parses a Rockwell Studio 5000 / RSLogix 5000 project export (.L5X) and
produces a CSV that EPLAN Electric P8's "PLC bulk data" / PLC navigator
import can read for schematic generation.

Why L5X (vs ACD / L5K / RDF / AML):
  * ACD is a closed binary format.
  * L5K is a custom text grammar that needs a hand-written parser.
  * RDF/AML carry the hardware tree but NOT the tag/alias database.
  * L5X is plain XML with both the module tree (catalog numbers, slots,
    chassis hierarchy) and every controller- and program-scoped tag,
    including alias tags that bind symbolic names to physical I/O points.

Output header (fixed):
  DeviceTag,Rack,Slot,ConnectionPoint,Address,DataType,SymbolicName,FunctionText

Usage:
  python logix_to_eplan_csv.py WADDING_1.L5X -o WADDING_1_eplan.csv
  python logix_to_eplan_csv.py WADDING_1.L5X --spares        # add unused points
  python logix_to_eplan_csv.py WADDING_1.L5X --include-hmi   # PanelView points too
  python logix_to_eplan_csv.py WADDING_1.L5X --logix-address # raw Logix addresses
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Module catalog: catalog number -> (kind, points)
#   kind: DI / DO / AI / AO
# Covers the common 1756 (ControlLogix), 1769 (CompactLogix) and 5069
# (Compact 5000) I/O families. Unknown catalogs fall back to a heuristic.
# --------------------------------------------------------------------------
CATALOG = {
    # 1756 digital inputs
    "1756-IA8D": ("DI", 8), "1756-IA16": ("DI", 16), "1756-IA16I": ("DI", 16),
    "1756-IA32": ("DI", 32), "1756-IB16": ("DI", 16), "1756-IB16D": ("DI", 16),
    "1756-IB16I": ("DI", 16), "1756-IB32": ("DI", 32), "1756-IC16": ("DI", 16),
    "1756-IH16I": ("DI", 16), "1756-IM16I": ("DI", 16), "1756-IN16": ("DI", 16),
    "1756-IV16": ("DI", 16), "1756-IV32": ("DI", 32),
    # 1756 digital outputs
    "1756-OA8": ("DO", 8), "1756-OA8D": ("DO", 8), "1756-OA8E": ("DO", 8),
    "1756-OA16": ("DO", 16), "1756-OA16I": ("DO", 16),
    "1756-OB8": ("DO", 8), "1756-OB8EI": ("DO", 8), "1756-OB8I": ("DO", 8),
    "1756-OB16D": ("DO", 16), "1756-OB16E": ("DO", 16), "1756-OB16I": ("DO", 16),
    "1756-OB32": ("DO", 32), "1756-OC8": ("DO", 8), "1756-OH8I": ("DO", 8),
    "1756-ON8": ("DO", 8), "1756-OV16E": ("DO", 16), "1756-OV32E": ("DO", 32),
    "1756-OW16I": ("DO", 16), "1756-OX8I": ("DO", 8),
    # 1756 analog
    "1756-IF4FXOF2F": ("AI", 4), "1756-IF6CIS": ("AI", 6), "1756-IF6I": ("AI", 6),
    "1756-IF8": ("AI", 8), "1756-IF16": ("AI", 16), "1756-IR6I": ("AI", 6),
    "1756-IT6I": ("AI", 6), "1756-IT6I2": ("AI", 6),
    "1756-OF4": ("AO", 4), "1756-OF6CI": ("AO", 6), "1756-OF6VI": ("AO", 6),
    "1756-OF8": ("AO", 8),
    # 1769 digital
    "1769-IA8I": ("DI", 8), "1769-IA16": ("DI", 16), "1769-IM12": ("DI", 12),
    "1769-IQ16": ("DI", 16), "1769-IQ16F": ("DI", 16), "1769-IQ32": ("DI", 32),
    "1769-OA8": ("DO", 8), "1769-OA16": ("DO", 16), "1769-OB8": ("DO", 8),
    "1769-OB16": ("DO", 16), "1769-OB16P": ("DO", 16), "1769-OB32": ("DO", 32),
    "1769-OV16": ("DO", 16), "1769-OW8": ("DO", 8), "1769-OW8I": ("DO", 8),
    "1769-OW16": ("DO", 16),
    # 1769 analog
    "1769-IF4": ("AI", 4), "1769-IF4I": ("AI", 4), "1769-IF8": ("AI", 8),
    "1769-IR6": ("AI", 6), "1769-IT6": ("AI", 6),
    "1769-OF2": ("AO", 2), "1769-OF4": ("AO", 4), "1769-OF8C": ("AO", 8),
    "1769-OF8V": ("AO", 8),
    # 5069 digital / analog
    "5069-IA16": ("DI", 16), "5069-IB16": ("DI", 16), "5069-IB16F": ("DI", 16),
    "5069-IB32": ("DI", 32), "5069-OA16": ("DO", 16), "5069-OB16": ("DO", 16),
    "5069-OB16F": ("DO", 16), "5069-OW16": ("DO", 16), "5069-OX4I": ("DO", 4),
    "5069-IF8": ("AI", 8), "5069-IF16": ("AI", 16), "5069-IY4": ("AI", 4),
    "5069-OF4": ("AO", 4), "5069-OF8": ("AO", 8),
}

# Heuristic for catalogs not in the table, e.g. "1756-IB16/B" base part.
_CAT_RE = re.compile(r"^\d{4}-(I|O)([A-Z]+)(\d+)", re.IGNORECASE)
_ANALOG_LETTERS = {"F", "R", "T", "Y"}  # voltage/current, RTD, thermocouple, mixed


def classify_catalog(catalog_number: str):
    """Return (kind, points) for an I/O catalog number, or None."""
    base = catalog_number.split("/")[0].strip().upper()
    if base in CATALOG:
        return CATALOG[base]
    m = _CAT_RE.match(base)
    if not m:
        return None
    direction, letters, points = m.group(1).upper(), m.group(2).upper(), int(m.group(3))
    analog = letters[0] in _ANALOG_LETTERS
    kind = ("AI" if direction == "I" else "AO") if analog else \
           ("DI" if direction == "I" else "DO")
    return (kind, points)


# --------------------------------------------------------------------------
# Abbreviation dictionary used to humanize tag names into FunctionText.
# English and Spanish plant vocabulary (sample project is a Spanish-language
# tissue/wadding machine).
# --------------------------------------------------------------------------
ABBREVIATIONS = {
    # instruments / devices
    "PB": "Push Button", "LS": "Limit Switch", "SV": "Solenoid Valve",
    "PT": "Pressure Transmitter", "TT": "Temperature Transmitter",
    "FT": "Flow Transmitter", "LT": "Level Transmitter", "ZS": "Position Switch",
    "PS": "Pressure Switch", "TS": "Temperature Switch", "FS": "Flow Switch",
    "VFD": "Variable Frequency Drive", "MTR": "Motor", "MOT": "Motor",
    "SOL": "Solenoid", "CYL": "Cylinder", "HTR": "Heater", "FAN": "Fan",
    "PMP": "Pump", "VLV": "Valve", "ENC": "Encoder", "PRX": "Proximity Sensor",
    "PE": "Photo Eye", "CR": "Control Relay", "HS": "Hand Switch",
    # qualifiers
    "FWD": "Forward", "REV": "Reverse", "RUN": "Running", "STP": "Stop",
    "STRT": "Start", "FLT": "Fault", "ALM": "Alarm", "IND": "Indicator",
    "CMD": "Command", "FB": "Feedback", "POS": "Position", "SPD": "Speed",
    "TEMP": "Temperature", "PRESS": "Pressure", "LVL": "Level", "AUX": "Auxiliary",
    "EMERG": "Emergency", "ESTOP": "Emergency Stop", "E_STOP": "Emergency Stop",
    "OPN": "Open", "CLS": "Closed", "CLSD": "Closed", "ACT": "Active",
    "EN": "Enable", "DIS": "Disable", "MAN": "Manual", "SEL": "Selector",
    "HI": "High", "LO": "Low", "DRV": "Drive", "TDR": "Tender",
    "CTRL": "Control", "HU": "Hydraulic Unit", "AC": "Air Cap",
    # Spanish
    "ARRANQUE": "Arranque", "PARO": "Paro", "EMERGENCIA": "Emergencia",
    "REM": "Remoto", "SEG": "Seguridad", "ENT": "Entrada", "SAL": "Salida",
    "ACTI": "Activar", "DESAC": "Desactivar", "BBA": "Bomba",
    "TRAN": "Transmision", "PRIM": "Primario", "SECU": "Secundario",
    "TERC": "Terciario", "BRAZ": "Brazo", "BOB": "Bobina",
    "CAMB": "Cambio", "POSIC": "Posicion", "VEL": "Velocidad",
}

_ILLEGAL = re.compile(r"[^A-Za-z0-9_]")


def sanitize_symbol(name: str) -> str:
    """Make a tag name programming-compliant: word chars and underscores only."""
    s = _ILLEGAL.sub("_", name.strip().replace(" ", "_"))
    s = re.sub(r"_{2,}", "_", s).strip("_")
    if s and s[0].isdigit():
        s = "_" + s
    return s


def humanize(name: str) -> str:
    """Translate a raw tag name into readable schematic function text."""
    words = [w for w in re.split(r"[_\W]+", name) if w]
    out = []
    for w in words:
        key = w.upper()
        if key in ABBREVIATIONS:
            out.append(ABBREVIATIONS[key])
        elif any(c.isdigit() for c in w):
            out.append(w.upper())  # keep device codes like E120, SV604WE1A, 602AS1
        else:
            out.append(w.capitalize())
    return " ".join(out)


# --------------------------------------------------------------------------
# L5X model
# --------------------------------------------------------------------------
@dataclass
class Module:
    name: str
    catalog: str
    parent: str
    slot: int | None          # ICP backplane address
    kind: str | None = None   # DI / DO / AI / AO / None (CPU, comm, HMI...)
    points: int = 0
    rack: int = 0
    in_byte_base: int = 0     # EPLAN-style byte/word base addresses
    out_byte_base: int = 0
    an_in_word_base: int = 0
    an_out_word_base: int = 0
    network_address: str | None = None  # node addr from the non-ICP port (ControlNet/Ethernet/...); raw string, never invented


@dataclass
class IoPoint:
    tag: str
    module: Module
    direction: str            # 'I' or 'O'
    index: int                # bit (digital) or channel (analog)
    analog: bool
    radix: str = ""
    description: str = ""
    logix_address: str = ""
    scope: str = ""           # controller or program name
    no_symbol: bool = False   # force a generic terminal (never a device symbol):
                              # set for non-device signals (e.g. a TIA supply
                              # monitor / permit) so the matcher can't mis-assign


# AliasFor forms handled:
#   Local:2:I.Data.3          rack-optimized digital, scalar Data
#   RIO_RCP:1:I.Data.16       remote drop, digital
#   RIO_RCP:5:I.Ch0Data       analog channel
#   PV_PUPITRE:I.Data[2].9    direct connection, Data array (HMI/rack-opt adapter)
#   MyModule:I.Data.4         direct connection, no slot
_ALIAS_RE = re.compile(
    r"^(?P<head>[A-Za-z0-9_]+)"
    r"(?::(?P<slot>\d+))?"
    r":(?P<dir>[IO])"
    r"\.(?:"
    r"Ch(?P<ch>\d+)Data"
    r"|Data(?:\[(?P<word>\d+)\])?(?:\.(?P<bit>\d+))?"
    r")$"
)


def parse_alias(alias_for: str):
    """Parse an AliasFor physical address. Returns dict or None."""
    m = _ALIAS_RE.match(alias_for.strip())
    if not m:
        return None
    d = m.groupdict()
    if d["ch"] is not None:
        index, analog = int(d["ch"]), True
    else:
        word = int(d["word"]) if d["word"] is not None else 0
        bit = int(d["bit"]) if d["bit"] is not None else 0
        index, analog = word * 32 + bit if d["word"] is not None else bit, False
    return {
        "head": d["head"],
        "slot": int(d["slot"]) if d["slot"] is not None else None,
        "dir": d["dir"],
        "index": index,
        "analog": analog,
    }


def text_of(elem, child):
    e = elem.find(child)
    return (e.text or "").strip() if e is not None and e.text else ""


def load_l5x(path: str):
    tree = ET.parse(path)
    root = tree.getroot()
    controller = root.find("Controller")
    if controller is None:
        sys.exit(f"error: {path} has no <Controller> element (not a project L5X?)")

    # ---- modules ----
    modules: dict[str, Module] = {}
    for me in controller.findall("./Modules/Module"):
        name = me.get("Name", "")
        slot = None
        network_address = None
        for pe in me.findall("./Ports/Port"):
            ptype = pe.get("Type")
            if ptype == "ICP":
                try:
                    slot = int(pe.get("Address", ""))
                except ValueError:
                    pass
            elif network_address is None:
                # first NON-ICP port carrying a node address (ControlNet node
                # number, DeviceNet MAC, Ethernet IP...). Raw string, never
                # coerced; absent/empty Address -> stays None (never invented).
                addr = pe.get("Address")
                if addr:
                    network_address = addr
        mod = Module(
            name=name,
            catalog=me.get("CatalogNumber", ""),
            parent=me.get("ParentModule", ""),
            slot=slot,
            network_address=network_address,
        )
        cls = classify_catalog(mod.catalog)
        if cls:
            mod.kind, mod.points = cls
        modules[name] = mod

    # ---- tags (controller + program scope) ----
    def read_tags(tags_elem, scope):
        result = {}
        if tags_elem is None:
            return result
        for te in tags_elem.findall("Tag"):
            result[te.get("Name", "")] = {
                "alias_for": te.get("AliasFor", ""),
                "tag_type": te.get("TagType", "Base"),
                "radix": te.get("Radix", ""),
                "description": text_of(te, "Description"),
                "scope": scope,
            }
        return result

    ctrl_tags = read_tags(controller.find("Tags"), "Controller")
    program_tags = {}
    for pe in controller.findall("./Programs/Program"):
        program_tags[pe.get("Name", "")] = read_tags(pe.find("Tags"), pe.get("Name", ""))

    return controller.get("Name", "PLC1"), modules, ctrl_tags, program_tags


def resolve_alias(alias_for: str, scope_tags: dict, ctrl_tags: dict, depth=0):
    """Follow alias-of-alias chains until a physical address is reached."""
    if depth > 8:
        return None
    parsed = parse_alias(alias_for)
    if parsed:
        return alias_for
    # alias to another tag, possibly with a member suffix we can't map -> only
    # follow plain tag-name references
    target = alias_for.strip()
    info = scope_tags.get(target) or ctrl_tags.get(target)
    if info and info["alias_for"]:
        return resolve_alias(info["alias_for"], scope_tags, ctrl_tags, depth + 1)
    return None


def assign_racks_and_addresses(modules: dict[str, Module]):
    """Rack 1 = local chassis; each remote drop (distinct I/O parent that is a
    comm adapter) gets the next rack number. Then allocate sequential
    EPLAN-style byte/word bases per module ordered by (rack, slot)."""
    io_parents = []
    for m in modules.values():
        if m.kind and m.parent not in io_parents:
            io_parents.append(m.parent)
    io_parents.sort(key=lambda p: (p != "Local", p))  # Local first, then A-Z
    rack_of = {p: i + 1 for i, p in enumerate(io_parents)}

    io_mods = sorted(
        (m for m in modules.values() if m.kind),
        key=lambda m: (rack_of[m.parent], m.slot if m.slot is not None else 99),
    )
    in_byte = out_byte = 0
    an_in_word, an_out_word = 256, 256  # conventional analog word area
    for m in io_mods:
        m.rack = rack_of[m.parent]
        if m.kind == "DI":
            m.in_byte_base = in_byte
            in_byte += math.ceil(m.points / 8)
        elif m.kind == "DO":
            m.out_byte_base = out_byte
            out_byte += math.ceil(m.points / 8)
        elif m.kind == "AI":
            m.an_in_word_base = an_in_word
            an_in_word += m.points * 2
        elif m.kind == "AO":
            m.an_out_word_base = an_out_word
            an_out_word += m.points * 2
    return io_mods


def eplan_address(point_module: Module, direction: str, index: int, analog: bool) -> str:
    if analog:
        base = point_module.an_in_word_base if direction == "I" else point_module.an_out_word_base
        prefix = "IW" if direction == "I" else "QW"
        return f"{prefix}{base + index * 2}"
    base = point_module.in_byte_base if direction == "I" else point_module.out_byte_base
    prefix = "I" if direction == "I" else "Q"
    return f"{prefix}{base + index // 8}.{index % 8}"


def collect_points(modules, ctrl_tags, program_tags, include_hmi=False):
    """Walk every alias tag and bind it to a physical module point."""
    points: list[IoPoint] = []
    skipped: list[tuple[str, str, str]] = []

    # lookup: (chassis-head, slot) -> module, and module-name -> module
    by_parent_slot = {}
    for m in modules.values():
        if m.slot is not None:
            by_parent_slot[(m.parent, m.slot)] = m

    def find_module(head, slot):
        if slot is not None:
            # head is 'Local' or the comm adapter name of a remote chassis;
            # I/O modules in that chassis have parent == head... except local
            # chassis where parent of the adapter is also 'Local'.
            mod = by_parent_slot.get((head, slot))
            if mod:
                return mod
            # remote drop: adapter named `head` sits in chassis X; siblings
            # share the same parent as the alias addressing goes through it
            adapter = modules.get(head)
            if adapter is not None:
                return by_parent_slot.get((adapter.name, slot))
            return None
        return modules.get(head)  # direct connection: head is the module name

    def handle(tag_name, info, scope_tags):
        if info["tag_type"] != "Alias" or not info["alias_for"]:
            return
        resolved = resolve_alias(info["alias_for"], scope_tags, ctrl_tags)
        if not resolved:
            skipped.append((tag_name, info["alias_for"], "unresolvable alias"))
            return
        p = parse_alias(resolved)
        mod = find_module(p["head"], p["slot"])
        if mod is None:
            skipped.append((tag_name, resolved, f"module not found for '{p['head']}'"))
            return
        if not mod.kind:
            if not include_hmi:
                skipped.append((tag_name, resolved, f"non-I/O device {mod.catalog}"))
                return
            # HMI/comm points get a pseudo classification so they can be emitted
            mod.kind = "DI" if p["dir"] == "I" else "DO"
            mod.points = max(mod.points, p["index"] + 1)
        points.append(IoPoint(
            tag=tag_name,
            module=mod,
            direction=p["dir"],
            index=p["index"],
            analog=p["analog"],
            radix=info["radix"],
            description=info["description"],
            logix_address=resolved,
            scope=info["scope"],
        ))

    for name, info in ctrl_tags.items():
        handle(name, info, ctrl_tags)
    for prog, tags in program_tags.items():
        for name, info in tags.items():
            handle(name, info, tags)

    return points, skipped


def build_rows(controller_name, points, include_spares=False, use_logix_address=False,
               keep_duplicates=False):
    """One row per physical connection point. When several tags alias the same
    point (legal in Logix, illegal in an EPLAN import), the first tag wins and
    the others are folded into its FunctionText unless keep_duplicates is set."""
    rows = []
    used: dict[tuple, IoPoint] = {}
    duplicates: list[tuple[IoPoint, IoPoint]] = []
    deduped = []
    for pt in sorted(points, key=lambda p: (p.module.rack, p.module.slot or 0,
                                            p.direction, p.analog, p.index, p.tag)):
        key = (pt.module.name, pt.direction, pt.index, pt.analog)
        if key in used and not keep_duplicates:
            duplicates.append((used[key], pt))
            continue
        used.setdefault(key, pt)
        deduped.append(pt)
    points = deduped

    def device_tag(mod: Module) -> str:
        return f"={sanitize_symbol(controller_name) or 'PLC1'}+A{mod.rack}-KF{mod.slot}"

    def datatype(pt_analog: bool, radix: str) -> str:
        if not pt_analog:
            return "BOOL"
        return "REAL" if radix.lower() == "float" else "INT"

    extra_tags = {}
    for first, dup in duplicates:
        extra_tags.setdefault(id(first), []).append(dup.tag)

    for pt in points:
        function_text = pt.description or humanize(pt.tag)
        extras = extra_tags.get(id(pt))
        if extras:
            function_text += " / " + " / ".join(humanize(t) for t in extras)
        rows.append({
            "DeviceTag": device_tag(pt.module),
            "Rack": pt.module.rack,
            "Slot": pt.module.slot,
            "ConnectionPoint": pt.index + 1,
            "Address": pt.logix_address if use_logix_address
                       else eplan_address(pt.module, pt.direction, pt.index, pt.analog),
            "DataType": datatype(pt.analog, pt.radix),
            "SymbolicName": sanitize_symbol(pt.tag),
            "FunctionText": function_text,
        })

    if include_spares:
        mods = {pt.module.name: pt.module for pt in points}
        for mod in sorted(mods.values(), key=lambda m: (m.rack, m.slot or 0)):
            if not mod.kind:
                continue
            direction = "I" if mod.kind in ("DI", "AI") else "O"
            analog = mod.kind in ("AI", "AO")
            for idx in range(mod.points):
                if (mod.name, direction, idx, analog) in used:
                    continue
                rows.append({
                    "DeviceTag": f"={sanitize_symbol(controller_name) or 'PLC1'}+A{mod.rack}-KF{mod.slot}",
                    "Rack": mod.rack,
                    "Slot": mod.slot,
                    "ConnectionPoint": idx + 1,
                    "Address": eplan_address(mod, direction, idx, analog),
                    "DataType": "BOOL" if not analog else "INT",
                    "SymbolicName": "",
                    "FunctionText": "Spare",
                })
        rows.sort(key=lambda r: (r["Rack"], r["Slot"], r["ConnectionPoint"],
                                 r["DataType"] != "BOOL"))
    return rows, duplicates


HEADER = ["DeviceTag", "Rack", "Slot", "ConnectionPoint",
          "Address", "DataType", "SymbolicName", "FunctionText"]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Convert a ControlLogix L5X export to an EPLAN P8 PLC import CSV.")
    ap.add_argument("l5x", help="path to the .L5X project export")
    ap.add_argument("-o", "--output", help="output CSV path (default: <l5x>_eplan.csv)")
    ap.add_argument("--spares", action="store_true",
                    help="also emit unused points of every referenced I/O card")
    ap.add_argument("--include-hmi", action="store_true",
                    help="include PanelView/HMI-mapped points (not hardwired I/O)")
    ap.add_argument("--logix-address", action="store_true",
                    help="put the raw Logix address (Local:2:I.Data.3) in the "
                         "Address column instead of EPLAN-style I/Q byte.bit")
    ap.add_argument("--keep-duplicates", action="store_true",
                    help="emit one row per tag even when several tags alias the "
                         "same physical point (default: first tag wins)")
    args = ap.parse_args(argv)

    out_path = args.output or re.sub(r"\.l5x$", "", args.l5x, flags=re.I) + "_eplan.csv"

    controller_name, modules, ctrl_tags, program_tags = load_l5x(args.l5x)
    io_mods = assign_racks_and_addresses(modules)
    points, skipped = collect_points(modules, ctrl_tags, program_tags,
                                     include_hmi=args.include_hmi)
    rows, duplicates = build_rows(controller_name, points,
                                  include_spares=args.spares,
                                  use_logix_address=args.logix_address,
                                  keep_duplicates=args.keep_duplicates)

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)

    # ---- summary to stderr so stdout stays clean ----
    err = sys.stderr
    print(f"controller : {controller_name}", file=err)
    print(f"I/O modules: {len(io_mods)}", file=err)
    for m in io_mods:
        print(f"  rack {m.rack} slot {m.slot}: {m.catalog:<14} {m.kind}{m.points:<3}"
              f" ({m.name})", file=err)
    print(f"I/O points : {len(points)} mapped, {len(skipped)} skipped, "
          f"{len(duplicates)} duplicate aliases folded", file=err)
    for first, dup in duplicates:
        print(f"  duplicate {dup.tag} -> {dup.logix_address} (kept {first.tag})", file=err)
    for tag, addr, why in skipped:
        print(f"  skipped {tag} -> {addr}  [{why}]", file=err)
    print(f"rows       : {len(rows)} -> {out_path}", file=err)
    return 0


if __name__ == "__main__":
    sys.exit(main())
