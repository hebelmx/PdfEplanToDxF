#!/usr/bin/env python3
"""
logix_to_qet.py — ControlLogix L5X -> QElectroTech (.qet) project.

Generates one folio (diagram) per I/O card found in a Studio 5000 L5X export.
Each used point is drawn as a terminal element (connectable later in QET) with
its connection-point number as label, next to a text line with the EPLAN-style
address, the PLC tag and the humanized function text.

Digital points are additionally matched against the plain-JSON symbol database
in symbol_db/ (keyword + tag-suffix fuzzy matching over the humanized tag and
the description); a matched field device (limit switch, push button, solenoid
valve, ...) is drawn at the end of the row and wired to the point's terminal.

Reuses the L5X parsing/classification from logix_to_eplan_csv.py.

Usage:
  python logix_to_qet.py PROJECT.L5X -o project.qet
  python logix_to_qet.py PROJECT.L5X --include-hmi
"""

from __future__ import annotations

import argparse
import difflib
import itertools
import json
import re
import sys
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

import logix_to_eplan_csv as l2e

MODULE_DB_DIR = Path(__file__).resolve().parent / "module_db"
SYMBOL_DB_DIR = Path(__file__).resolve().parent / "symbol_db"


ORIENT_CODE = {"n": 0, "e": 1, "s": 2, "w": 3}

# semantic symbol matching (digital points only)
SYM_FUZZ = 0.82        # min difflib ratio for a fuzzy word hit
SYM_MIN_SCORE = 0.95   # below this the point keeps the generic terminal
SYM_X_OFF = 290        # device symbol center, right of the tag/function texts


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if not unicodedata.combining(c))


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", _strip_accents(s).lower()) if t]


def load_symbol_db() -> list[dict]:
    """Load symbol_db/*.json + their .elmt definitions, sorted by id."""
    entries = []
    for path in sorted(SYMBOL_DB_DIR.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"warning: ignoring {path.name}: {exc}", file=sys.stderr)
            continue
        elmt_path = SYMBOL_DB_DIR / "elements" / entry.get("element", "")
        if not elmt_path.is_file():
            print(f"warning: {path.name}: missing {elmt_path.name}", file=sys.stderr)
            continue
        try:
            definition = ET.fromstring(elmt_path.read_text(encoding="utf-8"))
        except ET.ParseError as exc:
            print(f"warning: {elmt_path.name}: {exc}", file=sys.stderr)
            continue
        entry["_definition"] = definition
        entry["_terminals"] = [
            (round(float(t.get("x"))), round(float(t.get("y"))),
             ORIENT_CODE.get(t.get("orientation"), 0))
            for t in definition.find("description").iter("terminal")]
        entries.append(entry)
    return entries


def _suffix_hit(suffix: str, raw_tokens: list[str]) -> bool:
    """LS matches the tag tokens LS, LS2, 2LS and LS08A (suffix bounded by
    a digit), but not LSH or FLASH."""
    s = suffix.upper()
    for t in raw_tokens:
        t = t.upper()
        if (t == s
                or (t.startswith(s) and t[len(s)].isdigit())
                or (t.endswith(s) and t[-len(s) - 1].isdigit())):
            return True
    return False


def _phrase_score(words: list[str], text_tokens: set[str]) -> float:
    """All words of the phrase must appear (fuzzily) somewhere in the text;
    longer phrases are more specific and score higher."""
    worst = 1.0
    for w in words:
        if w in text_tokens:
            continue
        if len(w) <= 3:        # short words fuzz badly: exact only
            return 0.0
        best = max((difflib.SequenceMatcher(None, w, t).ratio()
                    for t in text_tokens), default=0.0)
        if best < SYM_FUZZ:
            return 0.0
        worst = min(worst, best)
    return (1.0 + 0.6 * (len(words) - 1)) * worst


def match_symbol(symbols: list[dict], tag: str, description: str,
                 direction: str) -> dict | None:
    """Pick the field-device symbol for a digital point, or None.

    Inverse of the humanizer: the tag is expanded through l2e.humanize()
    (abbreviation dictionary) and pooled with the description, then each
    symbol's keyword phrases and tag-suffix conventions are fuzzy-matched
    against that text.
    """
    raw_tokens = [t for t in re.split(r"[^A-Za-z0-9]+", tag) if t]
    # words the engineer actually wrote (tag + description, no expansion):
    # a multi-word match here is the strongest evidence and beats suffixes
    raw_set = set(t.lower() for t in raw_tokens) | set(_tokens(description or ""))
    text_tokens = raw_set | set(_tokens(l2e.humanize(tag)))
    best_key, best = (SYM_MIN_SCORE, 0.0, 0), None
    for entry in symbols:
        want = entry.get("direction", "any")
        if want not in ("any", direction):
            continue
        # whole words (PARO, EMERGENCIA) are stronger evidence than 2-letter
        # codes (PB, LS) when both kinds of suffix hit on the same tag
        suffix = max((2.8 if len(s) >= 4 else 2.5
                      for s in entry.get("suffixes", [])
                      if _suffix_hit(s, raw_tokens)), default=0.0)
        phrase = 0.0
        for kw in entry.get("keywords", []):
            words = _tokens(kw)
            if not words:
                continue
            ps = _phrase_score(words, text_tokens)
            if len(words) >= 2 and all(w in raw_set for w in words):
                ps = max(ps, 3.0)
            phrase = max(phrase, ps)
        key = (max(suffix, phrase), phrase, entry.get("priority", 0))
        if key > best_key:
            best_key, best = key, entry
    return best


def load_module_db(catalog: str) -> dict | None:
    """Load module_db/<catalog-base>.json (e.g. 1756-IB32/B -> 1756-IB32)."""
    base = catalog.split("/")[0].strip()
    path = MODULE_DB_DIR / f"{base}.json"
    if not path.is_file():
        return None
    try:
        db = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: ignoring {path.name}: {exc}", file=sys.stderr)
        return None
    db["_wiring_by_point"] = {w.get("point"): w for w in db.get("wiring", [])}
    return db

# Terminal element embedded into the generated project (official QET
# collection element 10_electric/10_allpole/130_terminals_terminal_strips/
# borne_2.elmt, license: see http://qelectrotech.org/wiki/doc/elements_license)
TERMINAL_ELMT = """<definition version="0.100.0" type="element" link_type="terminal" width="30" height="40" hotspot_x="10" hotspot_y="20">
    <uuid uuid="{3f1985d3-104c-d0fe-e025-de0b19429826}"/>
    <names>
        <name lang="en">Terminal block</name>
        <name lang="es">Terminal de union</name>
        <name lang="fr">Borne continuite</name>
    </names>
    <kindInformations>
        <kindInformation name="type">generic</kindInformation>
        <kindInformation name="function">generic</kindInformation>
    </kindInformations>
    <informations>Author: The QElectroTech team
License: see http://qelectrotech.org/wiki/doc/elements_license</informations>
    <description>
        <line x1="0" y1="10" x2="0" y2="3" end1="none" end2="none" length1="1.5" length2="1.5" style="line-style:normal;line-weight:normal;filling:none;color:black" antialias="false"/>
        <line x1="0" y1="-10" x2="0" y2="-3" end1="none" end2="none" length1="1.5" length2="1.5" style="line-style:normal;line-weight:normal;filling:none;color:black" antialias="false"/>
        <ellipse x="-2.5" y="-2.5" width="5" height="5" style="line-style:normal;line-weight:normal;filling:none;color:black" antialias="true"/>
        <line x1="10" y1="0" x2="3" y2="0" end1="none" end2="none" length1="1.5" length2="1.5" style="line-style:normal;line-weight:normal;filling:none;color:black" antialias="false"/>
        <dynamic_text x="3" y="-24.5" z="5" text_width="-1" Halignment="AlignLeft" Valignment="AlignTop" frame="false" rotation="0" keep_visual_rotation="false" text_from="ElementInfo" uuid="{8e248a1f-fe85-48a3-8940-9a66b8032f11}" font="Liberation Sans,9,-1,5,50,0,0,0,0,0,Regular">
            <text></text>
            <info_name>label</info_name>
        </dynamic_text>
        <terminal uuid="{323e68e1-514f-4e03-8ece-f22744a2d325}" name="" x="0" y="-10" orientation="n" type="Generic"/>
        <terminal uuid="{c81262f0-711b-451d-a333-9039f4de25b6}" name="" x="0" y="10" orientation="s" type="Generic"/>
        <terminal uuid="{a33d1699-88d4-4071-a651-977d380e6e8c}" name="" x="10" y="0" orientation="e" type="Generic"/>
    </description>
</definition>"""

TERMINAL_TYPE = ("embed://import/10_electric/10_allpole/"
                 "130_terminals&terminal_strips/borne_2.elmt")
# (x, y, orientation-code) of the definition's terminals: n=0, e=1, s=2, w=3
TERMINAL_PINS = [(0, -10, 0), (0, 10, 2), (10, 0, 1)]

FONT_TEXT = "Sans Serif,8,-1,5,50,0,0,0,0,0,Normal"
FONT_SMALL = "Sans Serif,7,-1,5,50,0,0,0,0,0,Normal"
FONT_HEADER = "Sans Serif,10,-1,5,75,0,0,0,0,0,Bold"

# Folio geometry (QET defaults: 10 px grid)
COL_X = (110, 590)          # left/right column x for the terminal symbols
ROW_Y0, ROW_DY = 100, 35    # first row y and per-point pitch
POINTS_PER_COL = 16
BOX_LEFT, BOX_RIGHT = 60, 10   # card box extents relative to terminal x
PIN_PLACEHOLDER = "__"


def new_uuid() -> str:
    return "{%s}" % uuid.uuid4()


def add_text(inputs: ET.Element, x: int, y: int, text: str, font: str = FONT_TEXT):
    ET.SubElement(inputs, "input", {
        "x": str(x), "y": str(y), "rotation": "0", "font": font, "text": text,
    })


def add_rect(shapes: ET.Element, x1: int, y1: int, x2: int, y2: int,
             width: str = "1"):
    shape = ET.SubElement(shapes, "shape", {
        "type": "Rectangle", "x1": str(x1), "y1": str(y1),
        "x2": str(x2), "y2": str(y2), "rx": "0", "ry": "0",
        "z": "0", "closed": "0", "is_movable": "1",
    })
    ET.SubElement(shape, "pen", {"color": "#000000", "widthF": width,
                                 "style": "SolidLine"})
    ET.SubElement(shape, "brush", {"color": "#000000", "style": "NoBrush"})


def _add_element(elements: ET.Element, type_path: str, x: int, y: int,
                 orientation: int, pins, infos: dict, ids) -> list[int]:
    """Place an element instance; allocate one diagram-unique id per pin
    (conductors reference those ids) and return them in pin order."""
    el = ET.SubElement(elements, "element", {
        "type": type_path,
        "x": str(x), "y": str(y), "z": "10",
        "orientation": str(orientation), "prefix": "X", "freezeLabel": "false",
        "uuid": new_uuid(),
    })
    terms = ET.SubElement(el, "terminals")
    pin_ids = []
    for tx, ty, to in pins:
        pid = next(ids)
        pin_ids.append(pid)
        ET.SubElement(terms, "terminal", {
            "id": str(pid), "x": str(tx), "y": str(ty),
            "orientation": str(to), "name": "_", "number": "_",
            "nameHidden": "0",
        })
    infos_el = ET.SubElement(el, "elementInformations")
    for name, (value, show) in infos.items():
        info = ET.SubElement(infos_el, "elementInformation",
                             {"name": name, "show": show})
        info.text = value
    return pin_ids


def add_terminal_element(elements: ET.Element, x: int, y: int,
                         label: str, function: str, ids) -> list[int]:
    return _add_element(elements, TERMINAL_TYPE, x, y, 0, TERMINAL_PINS,
                        {"label": (label, "1"), "function": (function, "1")},
                        ids)


def add_symbol_element(elements: ET.Element, entry: dict, x: int, y: int,
                       label: str, ids) -> tuple[list[int], int]:
    """Place a symbol_db device rotated 90° CW (horizontal in the row).

    Instance terminals are stored already transformed: (x,y) -> (-y,x),
    orientation code +1 mod 4. Returns (pin ids, index of the west pin —
    the one facing the I/O terminal)."""
    pins = [(-ty, tx, (to + 1) % 4) for tx, ty, to in entry["_terminals"]]
    west = min(range(len(pins)),
               key=lambda i: (pins[i][2] != 3, pins[i][0]))
    type_path = f"embed://import/symbols/{entry['element']}"
    pin_ids = _add_element(elements, type_path, x, y, 1, pins,
                           {"label": (label, "1")}, ids)
    return pin_ids, west


def next_designation(entry: dict, counters: dict, page: int) -> str | None:
    """IEC 81346 device tag (e.g. -K3.1) for a matched symbol_db entry.

    The class letter is the entry's "dt" field; the folio's page number is the
    prefix and a per-(page, letter) counter assigns the sequence, so every page
    numbers its own devices from 1 (-K3.1, -K3.2, ...). The page prefix keeps
    designations unambiguous across folios without relying on project-wide
    numbering continuity (no convention requires it). Called in the
    deterministic folio/point traversal so repeat runs of the same L5X produce
    identical designations. Returns None when the entry carries no usable single
    A–Z "dt" letter (graceful degradation — caller falls back to the PLC tag)."""
    dt = entry.get("dt")
    if not isinstance(dt, str):
        return None
    dt = dt.strip().upper()
    if len(dt) != 1 or not ("A" <= dt <= "Z"):
        return None
    key = (page, dt)
    n = counters.get(key, 0) + 1
    counters[key] = n
    return f"-{dt}{page}.{n}"


def add_conductor(conductors: ET.Element, terminal1: int, terminal2: int):
    ET.SubElement(conductors, "conductor", {
        "terminal1": str(terminal1), "terminal2": str(terminal2),
        "type": "multi", "num": "", "x": "0", "y": "0",
        "condsize": "1", "numsize": "9", "displaytext": "1",
        "onetextperfolio": "0", "freezeLabel": "false",
    })


def build_folio(project: ET.Element, order: int, mod, points,
                symbols: list[dict], sym_counts: dict, designations: dict):
    """One diagram per I/O card; points already sorted."""
    title = f"R{mod.rack}.S{mod.slot} {mod.name} ({mod.catalog} {mod.kind}{mod.points})"
    diagram = ET.SubElement(project, "diagram", {
        "order": str(order), "title": title,
        "cols": "17", "colsize": "60", "rows": "8", "rowsize": "80",
        "height": "660", "displaycols": "true", "displayrows": "true",
        "author": "logix_to_qet", "folio": "%id/%total",
        "version": "0.100",
    })
    ET.SubElement(diagram, "defaultconductor", {
        "type": "multi", "num": "", "condsize": "1", "numsize": "9",
        "displaytext": "1", "onetextperfolio": "0",
    })
    elements = ET.SubElement(diagram, "elements")
    conductors = ET.SubElement(diagram, "conductors")
    shapes = ET.SubElement(diagram, "shapes")
    inputs = ET.SubElement(diagram, "inputs")
    ids = itertools.count(1)  # terminal ids must be unique per diagram

    db = load_module_db(mod.catalog)
    header = (f"{mod.name}   |   {mod.catalog}   |   Rack {mod.rack}"
              f"  Slot {mod.slot}   |   {mod.kind}{mod.points}")
    add_text(inputs, 40, 30, header, FONT_HEADER)
    if db:
        sub = " — ".join(s for s in (db.get("vendor"), db.get("description"),
                                     db.get("rtb")) if s)
        add_text(inputs, 40, 44, sub, FONT_SMALL)
    wiring = db["_wiring_by_point"] if db else {}

    # classical card box: one per column of points, card name on top
    n_cols = (mod.points + POINTS_PER_COL - 1) // POINTS_PER_COL
    for col in range(min(n_cols, len(COL_X))):
        x = COL_X[col]
        pts_in_col = min(POINTS_PER_COL, mod.points - col * POINTS_PER_COL)
        y1 = ROW_Y0 - 20
        y2 = ROW_Y0 + (pts_in_col - 1) * ROW_DY + 20
        add_rect(shapes, x - BOX_LEFT, y1, x + BOX_RIGHT, y2)
        box_title = f"-{mod.name}" if n_cols == 1 else \
                    f"-{mod.name}  ({col * POINTS_PER_COL}…{col * POINTS_PER_COL + pts_in_col - 1})"
        add_text(inputs, x - BOX_LEFT, y1 - 14, box_title, FONT_TEXT)

    for pt in points:
        cp = pt.index + 1
        col = (cp - 1) // POINTS_PER_COL
        row = (cp - 1) % POINTS_PER_COL
        x = COL_X[min(col, len(COL_X) - 1)]
        y = ROW_Y0 + row * ROW_DY
        function = pt.description or l2e.humanize(pt.tag)
        address = l2e.eplan_address(pt.module, pt.direction, pt.index, pt.analog)
        w = wiring.get(pt.index, {})
        pin = w.get("pin") or PIN_PLACEHOLDER
        if pin.upper() == "TBD":
            pin = PIN_PLACEHOLDER
        point_name = w.get("name") or f"{'IN' if pt.direction == 'I' else 'OUT'}-{pt.index}"
        # inside the card box: point name and physical pin (placeholder until
        # filled in module_db)
        add_text(inputs, x - BOX_LEFT + 4, y - 8, point_name, FONT_SMALL)
        add_text(inputs, x - BOX_LEFT + 4, y + 3, f"pin {pin}", FONT_SMALL)
        term_ids = add_terminal_element(elements, x, y, str(cp), function, ids)
        add_text(inputs, x + 20, y - 8,
                 f"{cp:>2}  {address:<7} {pt.tag}")
        add_text(inputs, x + 20, y + 4, function)
        # field-device symbol from the semantic match, wired to the terminal
        sym = None if pt.analog else match_symbol(symbols, pt.tag,
                                                  pt.description, pt.direction)
        if sym:
            # IEC 81346 designation (e.g. -K3.1, page-prefixed) becomes the
            # symbol label; if the matched entry has no usable "dt" we fall back
            # to the PLC tag so a placed device never carries an empty/garbage
            # label. The PLC tag and function text stay visible in the row texts
            # above regardless.
            designation = next_designation(sym, designations, order) or pt.tag
            pin_ids, west = add_symbol_element(elements, sym, x + SYM_X_OFF, y,
                                               designation, ids)
            add_conductor(conductors, term_ids[2], pin_ids[west])
            sym_counts[sym["id"]] = sym_counts.get(sym["id"], 0) + 1

    return diagram


def build_collection(project: ET.Element, used_symbols: list[dict]):
    """Embed the terminal element plus every symbol the folios used."""
    collection = ET.SubElement(project, "collection")
    imp = ET.SubElement(collection, "category", {"name": "import"})
    cat = imp
    for name in ("10_electric", "10_allpole", "130_terminals&terminal_strips"):
        cat = ET.SubElement(cat, "category", {"name": name})
    el = ET.SubElement(cat, "element", {"name": "borne_2.elmt"})
    el.append(ET.fromstring(TERMINAL_ELMT))
    if used_symbols:
        sym_cat = ET.SubElement(imp, "category", {"name": "symbols"})
        for entry in used_symbols:
            el = ET.SubElement(sym_cat, "element", {"name": entry["element"]})
            el.append(entry["_definition"])


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Convert a ControlLogix L5X export to a QElectroTech project.")
    ap.add_argument("l5x", help="path to the .L5X project export")
    ap.add_argument("-o", "--output", help="output .qet path (default: <l5x>.qet)")
    ap.add_argument("--include-hmi", action="store_true",
                    help="include PanelView/HMI-mapped points")
    ap.add_argument("--no-symbols", action="store_true",
                    help="skip field-device symbol matching (terminals only)")
    args = ap.parse_args(argv)

    out_path = args.output or re.sub(r"\.l5x$", "", args.l5x, flags=re.I) + ".qet"

    controller, modules, ctrl_tags, program_tags = l2e.load_l5x(args.l5x)
    io_mods = l2e.assign_racks_and_addresses(modules)
    points, skipped = l2e.collect_points(modules, ctrl_tags, program_tags,
                                         include_hmi=args.include_hmi)

    # group points per module, first tag wins on duplicates
    per_module: dict[str, list] = {}
    seen = set()
    for pt in sorted(points, key=lambda p: (p.module.rack, p.module.slot or 0,
                                            p.direction, p.analog, p.index, p.tag)):
        key = (pt.module.name, pt.direction, pt.index, pt.analog)
        if key in seen:
            continue
        seen.add(key)
        per_module.setdefault(pt.module.name, []).append(pt)

    symbols = [] if args.no_symbols else load_symbol_db()
    sym_counts: dict[str, int] = {}
    # IEC 81346 counter keyed by (page, class letter) -> last sequential number,
    # so each folio numbers its own devices from 1; filled in the deterministic
    # folio/point traversal below
    designations: dict[tuple[int, str], int] = {}

    project = ET.Element("project", {"title": f"{controller} I/O", "version": "0.80"})
    order = 1
    folios = 0
    for mod in io_mods:
        pts = per_module.get(mod.name)
        if not pts:
            continue
        build_folio(project, order, mod, pts, symbols, sym_counts, designations)
        order += 1
        folios += 1
    used = [e for e in symbols if e["id"] in sym_counts]
    build_collection(project, used)

    pretty = minidom.parseString(ET.tostring(project, encoding="unicode")) \
        .toprettyxml(indent="    ")
    # drop blank lines minidom likes to add around preserved text nodes
    pretty = "\n".join(l for l in pretty.splitlines() if l.strip())
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(pretty + "\n")

    err = sys.stderr
    print(f"controller : {controller}", file=err)
    print(f"folios     : {folios} (one per I/O card with mapped tags)", file=err)
    n_points = sum(len(v) for v in per_module.values())
    print(f"points     : {n_points} drawn, {len(skipped)} skipped", file=err)
    if symbols:
        matched = sum(sym_counts.values())
        detail = ", ".join(f"{k} {v}" for k, v in
                           sorted(sym_counts.items(), key=lambda kv: -kv[1]))
        print(f"symbols    : {matched} matched ({detail}), "
              f"{n_points - matched} generic terminal", file=err)
    print(f"output     : {out_path}", file=err)
    return 0


if __name__ == "__main__":
    sys.exit(main())
