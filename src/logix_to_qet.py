#!/usr/bin/env python3
"""
logix_to_qet.py — ControlLogix L5X -> QElectroTech (.qet) project.

Generates one folio (diagram) per I/O card found in a Studio 5000 L5X export.
Each used point is drawn as a terminal element (connectable later in QET) with
its connection-point number as label, next to a text line with the EPLAN-style
address, the PLC tag and the humanized function text.

Reuses the L5X parsing/classification from logix_to_eplan_csv.py.

Usage:
  python logix_to_qet.py PROJECT.L5X -o project.qet
  python logix_to_qet.py PROJECT.L5X --include-hmi
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

import logix_to_eplan_csv as l2e

MODULE_DB_DIR = Path(__file__).resolve().parent / "module_db"


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


def add_terminal_element(elements: ET.Element, x: int, y: int,
                         label: str, function: str):
    el = ET.SubElement(elements, "element", {
        "type": TERMINAL_TYPE,
        "x": str(x), "y": str(y), "z": "10",
        "orientation": "0", "prefix": "X", "freezeLabel": "false",
        "uuid": new_uuid(),
    })
    terms = ET.SubElement(el, "terminals")
    for i, (tx, ty, to) in enumerate(TERMINAL_PINS):
        ET.SubElement(terms, "terminal", {
            "id": str(i), "x": str(tx), "y": str(ty),
            "orientation": str(to), "name": "_", "number": "_",
            "nameHidden": "0",
        })
    infos = ET.SubElement(el, "elementInformations")
    for name, value in (("label", label), ("function", function)):
        info = ET.SubElement(infos, "elementInformation",
                             {"name": name, "show": "1"})
        info.text = value


def build_folio(project: ET.Element, order: int, mod, points):
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
    conductors = ET.SubElement(diagram, "conductors")  # none yet
    shapes = ET.SubElement(diagram, "shapes")
    inputs = ET.SubElement(diagram, "inputs")

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
        add_terminal_element(elements, x, y, str(cp), function)
        add_text(inputs, x + 20, y - 8,
                 f"{cp:>2}  {address:<7} {pt.tag}")
        add_text(inputs, x + 20, y + 4, function)

    return diagram


def build_collection(project: ET.Element):
    """Embed the terminal element definition used by every folio."""
    collection = ET.SubElement(project, "collection")
    cat = ET.SubElement(collection, "category", {"name": "import"})
    for name in ("10_electric", "10_allpole", "130_terminals&terminal_strips"):
        cat = ET.SubElement(cat, "category", {"name": name})
    el = ET.SubElement(cat, "element", {"name": "borne_2.elmt"})
    el.append(ET.fromstring(TERMINAL_ELMT))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Convert a ControlLogix L5X export to a QElectroTech project.")
    ap.add_argument("l5x", help="path to the .L5X project export")
    ap.add_argument("-o", "--output", help="output .qet path (default: <l5x>.qet)")
    ap.add_argument("--include-hmi", action="store_true",
                    help="include PanelView/HMI-mapped points")
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

    project = ET.Element("project", {"title": f"{controller} I/O", "version": "0.80"})
    order = 1
    folios = 0
    for mod in io_mods:
        pts = per_module.get(mod.name)
        if not pts:
            continue
        build_folio(project, order, mod, pts)
        order += 1
        folios += 1
    build_collection(project)

    pretty = minidom.parseString(ET.tostring(project, encoding="unicode")) \
        .toprettyxml(indent="    ")
    # drop blank lines minidom likes to add around preserved text nodes
    pretty = "\n".join(l for l in pretty.splitlines() if l.strip())
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(pretty + "\n")

    err = sys.stderr
    print(f"controller : {controller}", file=err)
    print(f"folios     : {folios} (one per I/O card with mapped tags)", file=err)
    print(f"points     : {sum(len(v) for v in per_module.values())} drawn, "
          f"{len(skipped)} skipped", file=err)
    print(f"output     : {out_path}", file=err)
    return 0


if __name__ == "__main__":
    sys.exit(main())
