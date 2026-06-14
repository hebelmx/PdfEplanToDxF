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
import csv
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


def parse_power_block(power) -> list[dict]:
    """Normalize an OPTIONAL module_db "power" block into a flat list of power
    groups, gracefully. Each returned group is a dict:

        {"points": [int, ...], "supply": str, "common": str,
         "supply_pin": str, "common_pin": str}

    where `points` are the zero-based I/O point indices the group powers,
    `supply`/`common` are POTENTIAL NAMES (e.g. 'L1', 'N', 'L+', '24V', '0V')
    and `supply_pin`/`common_pin` are the physical RTB pins (kept "TBD" until an
    engineer fills them in from the module's installation instructions).

    Pure and stdlib-only. A missing OR malformed block — `power` not a dict, no
    'groups', 'groups' not a list, a group not a dict, a bad/empty point list —
    yields NO power groups (an empty list), never garbage and never an
    exception. Groups with an empty point list are dropped (nothing to power),
    and so is a group whose supply AND common are both blank/non-string (nothing
    to label or reference — it would otherwise render as a guessed "?" terminal).
    Pins are coerced to clean strings; a missing pin defaults to 'TBD' so it
    renders as the __ placeholder exactly like wiring[] pins."""
    if not isinstance(power, dict):
        return []
    raw_groups = power.get("groups")
    if not isinstance(raw_groups, list):
        return []
    groups = []
    for raw in raw_groups:
        if not isinstance(raw, dict):
            continue
        raw_points = raw.get("points")
        if not isinstance(raw_points, list):
            continue
        points = [p for p in raw_points if isinstance(p, int)
                  and not isinstance(p, bool)]
        if not points:
            continue
        group = {
            "points": points,
            "supply": raw.get("supply") if isinstance(raw.get("supply"), str)
                      else "",
            "common": raw.get("common") if isinstance(raw.get("common"), str)
                      else "",
            "supply_pin": raw.get("supply_pin")
                          if isinstance(raw.get("supply_pin"), str) else "TBD",
            "common_pin": raw.get("common_pin")
                          if isinstance(raw.get("common_pin"), str) else "TBD",
        }
        # A group with NO usable potential name (both supply and common blank or
        # non-string) has nothing to label or cross-reference, so drop it rather
        # than render a guessed "?" terminal — graceful degradation, never garbage.
        if not group["supply"] and not group["common"]:
            continue
        groups.append(group)
    return groups


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
    # OPTIONAL power block, normalized as gracefully as _wiring_by_point: a
    # missing/malformed block yields an empty list (no power terminals drawn).
    db["power_groups"] = parse_power_block(db.get("power"))
    return db


PROJECT_TEMPLATE_PATH = Path(__file__).resolve().parent / "project_template.json"


def load_project_template(path=PROJECT_TEMPLATE_PATH) -> dict:
    """Load the cajetín (title-block) config, merged over the blank built-in
    defaults. Mirrors load_module_db/load_symbol_db: stdlib json, utf-8-sig, and
    a graceful fallback so a missing OR malformed file yields all-default fields
    (every string value clean, `revisions` an empty list — never garbage). Only
    string values for known field keys are taken, plus a `revisions` list for the
    changelog; unknown keys are ignored and missing keys keep their default."""
    tmpl = dict(PROJECT_TEMPLATE_DEFAULTS)
    tmpl["revisions"] = []
    p = Path(path)
    if not p.is_file():
        return tmpl
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: ignoring {p.name}: {exc}", file=sys.stderr)
        return tmpl
    if isinstance(data, dict):
        for key in PROJECT_TEMPLATE_DEFAULTS:
            value = data.get(key)
            if isinstance(value, str):
                tmpl[key] = value
        if isinstance(data.get("revisions"), list):
            tmpl["revisions"] = data["revisions"]
    return tmpl

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

# Inline terminal-strip (bornero) geometry. Each field conductor is broken by a
# numbered strip terminal that sits in the clean slot BETWEEN the row text band
# (which runs ~x+20…x+200) and the device symbol (x+SYM_X_OFF = x+290). The
# device's WEST pin is NOT a fixed x+280: it varies per symbol — the closest one
# in the symbol DB is the photocell at x+260, the rest sit at ≥ x+269. The strip
# terminal centre is at x+STRIP_X_OFF; its borne_2 pin extent spans
# x+STRIP_X_OFF … x+STRIP_X_OFF+10 (east pin) and y ± 10. STRIP_X_OFF=235 keeps
# that full extent (…+245) clear of the row text (<x+200, margin 35), clear of
# the CLOSEST device west pin (x+260, margin 15 — not the optimistic x+280),
# well clear of the card box (right edge x+10), and on-sheet (smallest column
# x=110 -> 345 ≥ 0). Every card's strip designation is "-X1" (resets per card);
# the terminal number is the I/O channel = pt.index (0-based), so the strip reads
# 1:1 against the drawn points (-X1:0 … -X1:15 on a single-column card).
STRIP_X_OFF = 235
STRIP_DESIGNATION = "-X1"

# Inline power/common terminals: a horizontal strip ABOVE the card box, in the
# free band between the sub-header (y≈44) and the box top edge (ROW_Y0-20 = 80).
# A group's supply and common terminals sit side by side on ONE lane, so the
# full borne_2 pin extent (y ± 10) stays clear of the box top and the strip
# never runs off the left sheet edge or into the I/O-point rows below. Stacking
# supply OVER common (a second lane) does not fit this 36-px band, hence the
# side-by-side layout. One supply + one common terminal per power group.
POWER_BAND_Y = 60              # the single lane y; pins span 50..70, box top = 80
POWER_X0 = 150                 # first terminal x (positive — on-sheet, clear left)
POWER_PAIR_DX = 80             # supply -> common spacing within one group
POWER_GROUP_DX = 180           # start-to-start spacing between successive groups

# Spanish supply-rail label and the compact text-annotation prefix that
# references the rail folio ('Alimentación') from each inline power terminal.
# It is a LABEL, not a navigable QET cross-reference. Data, not logic.
SUPPLY_FOLIO_TITLE = "Alimentación"
POWER_XREF_PREFIX = "→ /Alim "


# ── Cajetín / title block (native QElectroTech ISO 7200) ─────────────────────
# Instead of hand-drawing the title block, we use QElectroTech's own title-block
# template mechanism. The project embeds the Exxerpro ISO 7200 template
# (assets/exxerpro.titleblock — built by build_titleblock.py with the fitted SVG
# logo) and every folio references it (titleblocktemplate=… displayAt="bottom"),
# exactly the way QET writes it (cf. examples/iso_sfc_example.qet). QET then
# renders the framed block, the SVG logo and the auto sheet number
# %{folio-id}/%{folio-total} itself — no reserved band, the folio keeps its
# height. Per-project values (company, drawing no., rev, date…) are supplied as
# <property> entries on each diagram, keyed by the template's %{token} names.
# Pure ISO 7200: no separate CLIENTE/REVISÓ cells. A missing template file
# degrades gracefully to a valid project with no title block.
TITLEBLOCK_PATH = (Path(__file__).resolve().parent.parent
                   / "assets" / "exxerpro.titleblock")
TITLEBLOCK_NAME = "exxerpro"

# Built-in defaults used when project_template.json is absent or a field is
# missing. Every value a clean string ("" -> a blank cell, never garbage).
# 'project'/'machine' fall back to the parsed controller name at render time
# (see resolve_title_block_fields), so they stay "" here.
PROJECT_TEMPLATE_DEFAULTS = {
    "company": "",
    "company_logo": "",
    "client": "",
    "client_logo": "",
    "project": "",
    "machine": "",
    "drawn_by": "",
    "revised_by": "",
    "approved_by": "",
    "date": "",
    "drawing_number": "",
    "revision": "",
}


# ── BOM / device-index folio ────────────────────────────────────────────────
# Unified flat schema: every emitted row uses exactly these 10 columns, in this
# order. The CSV sidecar carries all 10 (it is the complete record); the summary
# folio renders only a legible subset (SUMMARY_FOLIO_COLUMNS below). No category
# invents columns: each fills the subset the data supports and leaves the rest
# blank (""). NOTE: 'folio' is the source DRAWING page (diagram order) a row
# belongs to — NOT the rendered QET folio number of the summary sheet itself.
BOM_COLUMNS = ("category", "folio", "designation", "catalog_or_type", "tag",
               "address", "vendor", "description", "rack", "slot")

SUMMARY_ROW_Y0 = 70         # y of the header row
SUMMARY_ROW_DY = 14         # per-row pitch
SUMMARY_HEIGHT = 660        # matches the drawing folios' page height
SUMMARY_BOTTOM_MARGIN = 30  # keep the last row clear of the bottom frame (descent)
SUMMARY_PAGE_WIDTH = 1010   # cols*colsize = 17*60 = 1020; stay just inside it
# Rows per summary folio, DERIVED from the geometry so the page-fit invariant
# (last data row + descent stays inside the frame) cannot silently drift if the
# pitch/height is retuned. Deterministic, so repeat runs paginate identically.
SUMMARY_ROWS_PER_PAGE = (SUMMARY_HEIGHT - SUMMARY_ROW_Y0
                         - SUMMARY_BOTTOM_MARGIN) // SUMMARY_ROW_DY

# The summary folio renders a LEGIBLE SUBSET of the schema (the CSV keeps all 10
# columns). Each entry is (column-key, left-x, max-chars); a value longer than
# max-chars is ellipsized so it never overruns into the next column. Widths fit
# inside SUMMARY_PAGE_WIDTH; 'description' is last and gets the widest budget.
SUMMARY_FOLIO_COLUMNS = (
    ("folio", 10, 5),
    ("designation", 70, 12),
    ("catalog_or_type", 180, 18),
    ("tag", 320, 22),
    ("address", 480, 10),
    ("description", 560, 88),
)
# header labels that differ from the upper-cased column key
SUMMARY_FOLIO_LABELS = {"catalog_or_type": "TYPE"}


def _ellipsize(text: str, max_chars: int) -> str:
    """Truncate `text` to at most max_chars, marking truncation with a single
    ellipsis so a too-long cell never overruns its folio column. Pure; the CSV
    sidecar is unaffected (it keeps the full value)."""
    if not isinstance(text, str) or max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars == 1:
        return "…"
    return text[:max_chars - 1] + "…"


def _bom_row(category: str, folio: int, *, designation: str = "",
             catalog_or_type: str = "", tag: str = "", address: str = "",
             vendor: str = "", description: str = "", rack: str = "",
             slot: str = "") -> dict:
    """Build one schema row as a dict keyed by BOM_COLUMNS.

    Pure and deterministic (stdlib only, no I/O). Every column is present;
    callers fill only the columns their category supports and the rest stay
    "" (blank). `folio` (an int page/order number) is coerced to str so CSV
    and folio rendering see a single canonical string form."""
    return {
        "category": category,
        "folio": str(folio),
        "designation": designation,
        "catalog_or_type": catalog_or_type,
        "tag": tag,
        "address": address,
        "vendor": vendor,
        "description": description,
        "rack": rack,
        "slot": slot,
    }


def module_bom_row(folio: int, *, catalog: str, vendor: str, description: str,
                   rack: str, slot: str) -> dict:
    """(module) one row per I/O card: catalog_or_type/vendor/description/rack/
    slot filled; designation/tag/address left blank (a card is not a device and
    has no single tag or address)."""
    return _bom_row("module", folio, catalog_or_type=catalog, vendor=vendor,
                    description=description, rack=rack, slot=slot)


def device_bom_row(folio: int, *, designation: str, type_id: str,
                   description: str, tag: str, address: str) -> dict:
    """(device) one row per MATCHED field device: designation is the IEC 81346
    tag that was actually emitted (from next_designation, or the documented PLC-
    tag fallback — never fabricated), catalog_or_type is the matched symbol
    entry's type id, description is the symbol's human description, tag/address
    come from the point; vendor/rack/slot stay blank."""
    return _bom_row("device", folio, designation=designation,
                    catalog_or_type=type_id, description=description,
                    tag=tag, address=address)


def generic_bom_row(folio: int, *, tag: str, address: str) -> dict:
    """(generic) one row per UNMATCHED point (and EVERY analog point): only
    tag/address filled. Guardrail — designation and catalog_or_type MUST stay
    blank so an unmatched point is never assigned a device identity."""
    return _bom_row("generic", folio, tag=tag, address=address)


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


def resolve_title_block_fields(tmpl: dict, controller: str) -> dict:
    """Fill the render-time fallbacks the static template can't know: an unset
    project title becomes '<controller> I/O' and an unset machine becomes the
    controller name (both real, parsed data — never invented). Everything else
    is taken verbatim from the loaded template."""
    fields = dict(tmpl)
    fields["project"] = tmpl.get("project") or f"{controller} I/O"
    fields["machine"] = tmpl.get("machine") or controller
    return fields


def load_titleblock_template(path=TITLEBLOCK_PATH):
    """Return the title-block template XML TEXT verbatim, or None if the file is
    absent or not well-formed (graceful: the project then carries no title
    block). We keep the raw text rather than an ElementTree element because the
    embedded SVG logo declares many namespace prefixes (inkscape/sodipodi/xlink
    …); round-tripping it through ElementTree would rewrite those and risk a
    broken logo. The text is embedded verbatim by embed_titleblock_templates."""
    p = Path(path)
    if not p.is_file():
        return None
    text = p.read_text(encoding="utf-8")
    try:
        ET.fromstring(text)        # validate well-formed; keep the original text
    except ET.ParseError as exc:
        print(f"warning: ignoring {p.name}: {exc}", file=sys.stderr)
        return None
    return text


def _yyyymmdd(date_str: str) -> str:
    """'2026-06-13' -> '20260613' (QET diagram `date` attribute). Anything that
    is not exactly eight digits degrades to 'null' (QET's empty-date sentinel),
    never a malformed date."""
    digits = re.sub(r"\D", "", date_str or "")
    return digits if len(digits) == 8 else "null"


# QET resolves these title-block tokens itself (from the diagram attributes or
# the project), so they must NOT be emitted as custom <property> entries.
TITLEBLOCK_BUILTIN_TOKENS = frozenset({
    "author", "title", "date", "filename", "folio", "folio-id", "folio-total",
    "projecttitle", "machine", "locmach", "plant", "id", "total", "version",
    "indexrev", "date-creation",
})


# DA.5b: the title-block page cell ships with QET's built-in %{folio-id}, which
# numbers folios by DOCUMENT POSITION (1..N) and so ignores our section page
# scheme (Portada 000, drawings 101.., BOM 300..). Rewriting that token to a
# custom %{page} — populated per diagram from its order attribute — makes the
# cajetín display the section page instead. Applied to the EMBEDDED copy only;
# the committed asset stays standard ISO 7200 (re-syncable from QET).
PAGE_TOKEN = "page"


def sectionize_titleblock_page(template_text):
    """Rewrite the title block's page-number field to show the SECTION page (the
    diagram order) instead of QET's position counter: replace
    %{folio-id}/%{folio-total} (and a bare %{folio-id}) with the custom %{page}
    token. No-op when the template is None or carries no folio-id token (graceful
    — the page cell then keeps whatever it had)."""
    if template_text is None:
        return None
    text = template_text.replace("%{folio-id}/%{folio-total}", "%{" + PAGE_TOKEN + "}")
    return text.replace("%{folio-id}", "%{" + PAGE_TOKEN + "}")


def titleblock_custom_tokens(template_text: str) -> list:
    """Ordered, unique list of the template's CUSTOM %{token} names — every
    %{...} a field references, minus the QET built-ins. EVERY one must get a
    <property> on the diagram (blank when we have no value) so QET never renders
    the bare %{token} placeholder for an unfilled field."""
    seen, out = set(), []
    for tok in re.findall(r"%\{([^}]+)\}", template_text):
        if tok in TITLEBLOCK_BUILTIN_TOKENS or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def titleblock_properties(fields: dict) -> dict:
    """Map our config fields onto the ISO 7200 template's CUSTOM %{token}
    property names (pure ISO 7200 — no client/revised cells). Built-in tokens
    (%{author}/%{title}/%{date}/%{filename}, %{folio-id}/%{folio-total}) are NOT
    here: they come from the diagram attributes / QET itself. Blank values are
    dropped here; apply_titleblock still emits an empty property for any custom
    token without data, so it renders blank rather than as a raw placeholder."""
    raw = {
        "owner": fields.get("company", ""),         # EMPRESA / legal owner
        "name": fields.get("project", ""),          # drawing name (project id)
        "ref": fields.get("drawing_number", ""),    # PLANO N.º / reference
        "rev": fields.get("revision", ""),          # REV
        "approval": fields.get("approved_by", ""),  # APROBÓ
    }
    return {k: v for k, v in raw.items() if v}


def apply_titleblock(diagram: ET.Element, fields: dict, custom_tokens: list,
                     *, filename: str = ""):
    """Point one diagram at the embedded template and supply its values the way
    QElectroTech writes them: title-block attributes on the <diagram> (built-in
    tokens) plus a <properties> block. EVERY custom token gets a property — our
    value if we have one, else empty — so an unfilled ISO 7200 cell renders
    blank, never as a raw %{token}. The date is the static config/release date,
    deterministic, not 'today'."""
    diagram.set("titleblocktemplate", TITLEBLOCK_NAME)
    diagram.set("titleblocktemplateCollection", "embedded")
    diagram.set("displayAt", "bottom")
    diagram.set("date", _yyyymmdd(fields.get("date", "")))
    diagram.set("author", fields.get("drawn_by", ""))
    if filename:
        diagram.set("filename", filename)
    values = titleblock_properties(fields)
    props = ET.Element("properties")
    for token in custom_tokens:
        text = values.get(token, "")
        if token == PAGE_TOKEN:
            # the section page (DA.5b): this diagram's order, zero-padded to the
            # gated 3-digit scheme (Portada 000, Simbología 001, drawings 101…)
            order = diagram.get("order", "")
            try:
                text = f"{int(order):03d}"
            except (TypeError, ValueError):
                text = order
        ET.SubElement(props, "property",
                      {"name": token, "show": "1"}).text = text
    diagram.insert(0, props)   # QET writes <properties> as the first diagram child


def attach_titleblocks(project: ET.Element, fields: dict, template_text,
                       *, filename: str = "") -> int:
    """Reference the embedded template from EVERY folio (per-diagram attributes
    + <properties>). The template element itself is injected verbatim as text
    afterwards (embed_titleblock_templates), to preserve the SVG. Returns the
    number of folios stamped, or 0 when no template is available (graceful
    degradation — a valid project with no title block)."""
    if template_text is None:
        return 0
    custom_tokens = titleblock_custom_tokens(template_text)
    diagrams = project.findall("diagram")
    for diagram in diagrams:
        apply_titleblock(diagram, fields, custom_tokens, filename=filename)
    return len(diagrams)


def embed_titleblock_templates(project_xml: str, template_text: str) -> str:
    """Inject <titleblocktemplates>…</titleblocktemplates> verbatim as the first
    child of <project> (right after the serialized open tag), so the embedded
    SVG logo is preserved exactly. String surgery, not ElementTree, on purpose
    (see load_titleblock_template)."""
    m = re.search(r"<project\b[^>]*>", project_xml)
    if not m:
        return project_xml
    i = m.end()
    block = ("\n  <titleblocktemplates>\n" + template_text
             + "  </titleblocktemplates>")
    return project_xml[:i] + block + project_xml[i:]


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


def wire_number(address: str | None, page: int, scheme: str,
                counters: dict) -> str | None:
    """Wire number for a field conductor (terminal pin -> field-device pin).

    Pure and deterministic, mirroring next_designation: no I/O, no globals, and
    repeat calls with identical inputs and an equivalent counter state produce
    identical output. Returns None when there is no defined source point so the
    caller can leave num="" rather than invent a placeholder number.

    Schemes:
      'address'    — the default; returns the EPLAN address VERBATIM (e.g.
                     'Q0.0' -> 'Q0.0', 'IW100' -> 'IW100'), no page prefix and
                     no transformation.
      'sequential' — returns 'W<page>.<n>' where <page> is the folio order and
                     <n> is a per-folio running count that starts at 1 and
                     increments per conductor on the same page, resetting to 1
                     on a new page. The count is carried via `counters` (same
                     mechanism as next_designation), keyed by page.

    Guardrail — no invented numbers: when `address` is None or empty there is no
    defined source point, so both schemes return None."""
    if not isinstance(address, str) or not address.strip():
        return None
    if scheme == "sequential":
        n = counters.get(page, 0) + 1
        counters[page] = n
        return f"W{page}.{n}"
    return address.strip()


def strip_terminal_label(channel: int, designation: str = STRIP_DESIGNATION) -> str:
    """The numbered terminal label for the inline strip / bornero folio.

    Pure and deterministic: the per-card strip designation (always '-X1', reset
    per card) joined by ':' to the I/O channel number, which IS the point index
    (0-based) — so the strip reads 1:1 against the card's drawn points
    (-X1:0 … -X1:15). The channel is point-mirrored, never a fabricated sequence,
    and the designation resets per card so every card's strip is '-X1'."""
    return f"{designation}:{channel}"


def add_conductor(conductors: ET.Element, terminal1: int, terminal2: int,
                  num: str = ""):
    ET.SubElement(conductors, "conductor", {
        "terminal1": str(terminal1), "terminal2": str(terminal2),
        "type": "multi", "num": num, "x": "0", "y": "0",
        "condsize": "1", "numsize": "9", "displaytext": "1",
        "onetextperfolio": "0", "freezeLabel": "false",
    })


def _power_pin_label(pin) -> str:
    """The 'pin __' text for a power terminal: a physical pin renders verbatim,
    but the un-filled "TBD" sentinel (case-insensitive) renders as the __
    PIN_PLACEHOLDER, exactly like wiring[] pins. A blank/missing pin is also __
    (never guessed)."""
    if not isinstance(pin, str) or not pin.strip() or pin.strip().upper() == "TBD":
        return PIN_PLACEHOLDER
    return pin.strip()


def add_power_terminals(elements, inputs, power_groups: list, ids) -> list:
    """Draw the inline power/common terminals for a card's power groups.

    For each group, places ONE supply terminal and ONE common terminal side by
    side on a single horizontal lane above the card box (reusing
    add_terminal_element / add_text and the borne_2 definition already embedded
    in the collection — no new element type). Each terminal is labelled with its
    POTENTIAL NAME (e.g. L1, N, L+, 0V), shows 'pin __' until the physical pin is
    filled (TBD -> __), and carries a compact text annotation referencing the
    rail folio ('→ /Alim <potential>') — a LABEL only, NOT a drawn conductor and
    NOT a navigable QET cross-reference. When the card has more than one group
    the annotation carries a '(G<n>)' suffix so electrically-isolated groups that
    share a potential name (e.g. the 1756-OA16's two L1/N output groups) stay
    distinguishable in the drawing instead of collapsing into one reference.

    Terminals sit in the free band between the sub-header and the box top, so the
    full borne_2 pin extent stays clear of the card box and the I/O-point rows
    (see POWER_* geometry) and never runs off the left sheet edge. A group whose
    potential is blank draws NOTHING for that role (graceful — never a guessed
    "?" terminal). Returns the list of (x, y) center positions of the placed
    terminals so an integration test can assert they clear the box / sheet
    bounds. Cards with no/omitted/malformed power block draw NOTHING (empty)."""
    positions = []
    multi = len(power_groups) > 1
    for gi, group in enumerate(power_groups):
        gx0 = POWER_X0 + gi * POWER_GROUP_DX
        gsuffix = f" (G{gi + 1})" if multi else ""
        for role, dx in (("supply", 0), ("common", POWER_PAIR_DX)):
            potential = group.get(role) or ""
            if not potential:
                continue   # never draw a blank/"?" power terminal (graceful)
            x = gx0 + dx
            y = POWER_BAND_Y
            pin = _power_pin_label(group.get(f"{role}_pin"))
            # the terminal symbol itself (reuses borne_2 / add_terminal_element)
            add_terminal_element(elements, x, y, potential, potential, ids)
            # potential name, physical-pin placeholder, and the compact rail-folio
            # text annotation — three short lines that stay inside the band
            add_text(inputs, x + 11, y - 9, potential, FONT_SMALL)
            add_text(inputs, x + 11, y + 2, f"pin {pin}", FONT_SMALL)
            add_text(inputs, x + 11, y + 12,
                     f"{POWER_XREF_PREFIX}{potential}{gsuffix}", FONT_SMALL)
            positions.append((x, y))
    return positions


def build_folio(project: ET.Element, order: int, mod, points,
                symbols: list[dict], sym_counts: dict, designations: dict,
                wire_scheme: str = "address", wire_counters: dict | None = None,
                bom_rows: list | None = None):
    """One diagram per I/O card; points already sorted.

    If `bom_rows` is given, schema rows are appended to it DURING this single
    traversal (no second pass, no recomputation): one (module) row for the
    card, then one (device) row per matched field device or one (generic) row
    per unmatched/analog point, in the deterministic folio/point order. The
    accumulator is data-only — appending to it does not touch the emitted XML
    (the BOM rows are a pure side-channel; a card's own elements, including the
    inline power terminals drawn from its module_db power block, are unaffected
    by the accumulator)."""
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
    if wire_counters is None:
        wire_counters = {}
    elements = ET.SubElement(diagram, "elements")
    conductors = ET.SubElement(diagram, "conductors")
    shapes = ET.SubElement(diagram, "shapes")
    inputs = ET.SubElement(diagram, "inputs")
    ids = itertools.count(1)  # terminal ids must be unique per diagram

    db = load_module_db(mod.catalog)
    header = (f"{mod.name}   |   {mod.catalog}   |   Rack {mod.rack}"
              f"  Slot {mod.slot}   |   {mod.kind}{mod.points}")
    # Header + sub-header sit tight to the top of the sheet. The sub-header is a
    # full-width line and the inline power band (POWER_BAND_Y, between the two
    # terminal columns) is wedged between it and the first I/O row's strip label
    # (~y 87); keeping the header high widens that lane so the power terminals'
    # glyph + labels clear the sub-header instead of overprinting it.
    add_text(inputs, 40, 20, header, FONT_HEADER)
    if db:
        sub = " — ".join(s for s in (db.get("vendor"), db.get("description"),
                                     db.get("rtb")) if s)
        add_text(inputs, 40, 32, sub, FONT_SMALL)
    wiring = db["_wiring_by_point"] if db else {}

    # (module) BOM row for this I/O card — only data already computed above:
    # the catalog, and the vendor/description the sub-header already rendered.
    if bom_rows is not None:
        bom_rows.append(module_bom_row(
            order, catalog=mod.catalog,
            vendor=(db.get("vendor") or "") if db else "",
            description=(db.get("description") or "") if db else "",
            rack=str(mod.rack),
            slot="" if mod.slot is None else str(mod.slot)))

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

    # inline power/common terminals from the (optional) module_db power block:
    # one supply + one common per group on a horizontal lane above the card box,
    # clear of the I/O rows and the box. Empty/omitted block -> nothing drawn.
    add_power_terminals(elements, inputs, (db.get("power_groups") if db else []),
                        ids)

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
        # inline strip / bornero terminal on the field conductor: ONE numbered
        # strip terminal per drawn point (matched AND generic), in the clean slot
        # between the row text and the device symbol. Its label is the per-card
        # strip designation joined to the I/O channel (-X1:<pt.index>), so the
        # strip reads 1:1 against the points. Reuses the borne_2 definition via
        # add_terminal_element (no new element type); ids stay diagram-unique.
        # north pin (index 0) = card side, east pin (index 2) = device side.
        strip_label = strip_terminal_label(pt.index)
        strip_ids = add_terminal_element(elements, x + STRIP_X_OFF, y,
                                         strip_label, function, ids)
        add_text(inputs, x + STRIP_X_OFF - 4, y - 13, strip_label, FONT_SMALL)
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
            # the field conductor is now BROKEN by the inline strip terminal:
            # card terminal east pin -> strip north pin, then strip east pin ->
            # field-device west pin. The wire number rides the card->strip segment
            # ONLY (it appears exactly once — not lost, not duplicated); the
            # strip->device segment carries no num (same physical wire continues
            # through the terminal, so a second number would be a false duplicate).
            num = wire_number(address, order, wire_scheme, wire_counters) or ""
            add_conductor(conductors, term_ids[2], strip_ids[0], num)
            add_conductor(conductors, strip_ids[2], pin_ids[west], "")
            sym_counts[sym["id"]] = sym_counts.get(sym["id"], 0) + 1
            # (device) BOM row — designation is exactly what we labelled the
            # placed symbol with (next_designation result or PLC-tag fallback),
            # never a fabricated value; type/description come from the matched
            # symbol entry already in hand.
            if bom_rows is not None:
                bom_rows.append(device_bom_row(
                    order, designation=designation, type_id=sym["id"],
                    description=sym.get("description") or "",
                    tag=pt.tag, address=address))
        else:
            # GENERIC / unmatched point (or any analog point): there is no device
            # beyond, so the field conductor runs only from the card terminal east
            # pin to the strip terminal north pin — the strip is where field wiring
            # leaves the card. No invented device. The address (if any) still
            # carries the wire number on this single segment.
            num = wire_number(address, order, wire_scheme, wire_counters) or ""
            add_conductor(conductors, term_ids[2], strip_ids[0], num)
            # (generic) BOM row — unmatched point (or any analog point): only
            # tag/address; designation and catalog_or_type stay blank so we
            # never invent a device for a point that matched no symbol.
            if bom_rows is not None:
                bom_rows.append(generic_bom_row(order, tag=pt.tag, address=address))

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


def _csv_safe(value: str) -> str:
    """Guard a CSV cell against spreadsheet formula injection.

    A cell whose first character is one of = + - @ is interpreted as a formula
    by Excel / LibreOffice Calc — and every device designation starts with '-'
    (e.g. -S1.1), so without a guard the BOM's primary cross-reference column
    shows #NAME? errors. Such a value is prefixed with a single apostrophe, the
    spreadsheet text marker, so it is never evaluated. Only the CSV sidecar is
    affected; the .qet folio label and the stored designation keep the raw
    value. Empty / non-str values pass through unchanged."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def write_bom_csv(path, bom_rows: list[dict]):
    """Write the BOM rows to a CSV sidecar using the stdlib csv module.

    Header is BOM_COLUMNS in order; one line per row across all three
    categories; blank columns are emitted as empty fields. Every cell is passed
    through _csv_safe so a formula-leading value can't be misread by a
    spreadsheet. newline="" is the documented csv contract so the module
    controls line endings."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(BOM_COLUMNS))
        writer.writeheader()
        for row in bom_rows:
            writer.writerow({k: _csv_safe(v) for k, v in row.items()})


def _add_summary_diagram(project: ET.Element, order: int, page_rows: list[dict],
                         page_no: int, page_total: int) -> ET.Element:
    """Render one summary folio: a legible text table (header + the given rows)
    drawn with ONLY text and shape primitives — no <element>/terminal instances
    and no <conductor>. Only the SUMMARY_FOLIO_COLUMNS subset is shown and each
    cell is ellipsized to its column width so columns never overlap (the CSV
    sidecar keeps all 10 columns at full width). Keeps empty <elements>/
    <conductors> containers so the project schema matches the drawing folios."""
    diagram = ET.SubElement(project, "diagram", {
        "order": str(order),
        "title": f"BOM / device index ({page_no}/{page_total})",
        "cols": "17", "colsize": "60", "rows": "8", "rowsize": "80",
        "height": str(SUMMARY_HEIGHT), "displaycols": "false",
        "displayrows": "false", "author": "logix_to_qet", "folio": "%id/%total",
        "version": "0.100",
    })
    ET.SubElement(diagram, "defaultconductor", {
        "type": "multi", "num": "", "condsize": "1", "numsize": "9",
        "displaytext": "1", "onetextperfolio": "0",
    })
    # empty containers — NO element/terminal instances, NO conductors
    ET.SubElement(diagram, "elements")
    ET.SubElement(diagram, "conductors")
    shapes = ET.SubElement(diagram, "shapes")
    inputs = ET.SubElement(diagram, "inputs")

    x0 = SUMMARY_FOLIO_COLUMNS[0][1]
    add_text(inputs, x0, 30,
             f"BOM / DEVICE INDEX   (page {page_no} of {page_total})",
             FONT_HEADER)
    # column header row (subset only)
    for key, x, _w in SUMMARY_FOLIO_COLUMNS:
        add_text(inputs, x, SUMMARY_ROW_Y0,
                 SUMMARY_FOLIO_LABELS.get(key, key.upper()), FONT_SMALL)
    # header rule: a thin (2 px tall) rectangle clamped inside the page frame —
    # a zero-height rectangle does not render as a line in QElectroTech.
    y_rule = SUMMARY_ROW_Y0 + 6
    add_rect(shapes, x0, y_rule, SUMMARY_PAGE_WIDTH, y_rule + 2)
    # one text line per row; each cell ellipsized to its column width
    for i, row in enumerate(page_rows):
        y = SUMMARY_ROW_Y0 + (i + 1) * SUMMARY_ROW_DY
        for key, x, w in SUMMARY_FOLIO_COLUMNS:
            value = _ellipsize(row.get(key, ""), w)
            if value:
                add_text(inputs, x, y, value, FONT_SMALL)
    return diagram


def build_summary_folios(project: ET.Element, start_order: int,
                         bom_rows: list[dict]) -> int:
    """Append paginated summary folio(s) AFTER the drawing folios (order numbers
    continue past them). Rows are split so no row is drawn past the page bottom
    (SUMMARY_ROWS_PER_PAGE per page, deterministic). Returns the number of
    summary folios appended."""
    if not bom_rows:
        return 0
    pages = [bom_rows[i:i + SUMMARY_ROWS_PER_PAGE]
             for i in range(0, len(bom_rows), SUMMARY_ROWS_PER_PAGE)]
    total = len(pages)
    for n, page_rows in enumerate(pages, start=1):
        _add_summary_diagram(project, start_order + n - 1, page_rows, n, total)
    return total


# ── Changelog / revision-history folio ───────────────────────────────────────
# A final traceability sheet listing the document's revision history: once the
# first version is released, every reissue adds a line so the record carries who
# changed what, when. Driven by a `revisions` array in project_template.json;
# when none is configured a single first-emission row is synthesised from the
# title-block fields, so every project still gets a traceability sheet. Rendered
# like the summary folios — text + shapes only — so it carries the title block.
REVISION_COLUMNS = ("rev", "date", "description", "drawn", "approved")
REVISION_FOLIO_COLUMNS = (
    ("rev", 10, 6),
    ("date", 70, 12),
    ("description", 180, 100),
    ("drawn", 760, 14),
    ("approved", 880, 14),
)
REVISION_FOLIO_LABELS = {
    "rev": "REV", "date": "FECHA", "description": "DESCRIPCIÓN",
    "drawn": "DIBUJÓ", "approved": "APROBÓ",
}


def normalize_revisions(tmpl_revisions, fields: dict) -> list:
    """Return the revision-history rows (each a dict over REVISION_COLUMNS).

    Configured `revisions` entries win — each coerced to the five columns with
    missing keys left blank (never fabricated). When none are configured, a
    single first-emission row is synthesised from the title-block fields (rev /
    date / drawn / approved as-is, description 'Primera emisión') so the sheet is
    never empty. Deterministic and pure."""
    rows = []
    for entry in tmpl_revisions or []:
        if isinstance(entry, dict):
            rows.append({k: str(entry.get(k, "") or "") for k in REVISION_COLUMNS})
    if rows:
        return rows
    return [{
        "rev": fields.get("revision", "") or "",
        "date": fields.get("date", "") or "",
        "description": "Primera emisión",
        "drawn": fields.get("drawn_by", "") or "",
        "approved": fields.get("approved_by", "") or "",
    }]


def _add_changelog_diagram(project: ET.Element, order: int, page_rows: list,
                           page_no: int, page_total: int) -> ET.Element:
    """Render one changelog folio: a legible revision-history table (REV / FECHA
    / DESCRIPCIÓN / DIBUJÓ / APROBÓ) drawn with ONLY text + shape primitives —
    no <element>/terminal instances, no <conductor> — mirroring the summary
    folios so it shares the page geometry and the title block."""
    diagram = ET.SubElement(project, "diagram", {
        "order": str(order),
        "title": f"Historial de revisiones ({page_no}/{page_total})",
        "cols": "17", "colsize": "60", "rows": "8", "rowsize": "80",
        "height": str(SUMMARY_HEIGHT), "displaycols": "false",
        "displayrows": "false", "author": "logix_to_qet", "folio": "%id/%total",
        "version": "0.100",
    })
    ET.SubElement(diagram, "defaultconductor", {
        "type": "multi", "num": "", "condsize": "1", "numsize": "9",
        "displaytext": "1", "onetextperfolio": "0",
    })
    ET.SubElement(diagram, "elements")
    ET.SubElement(diagram, "conductors")
    shapes = ET.SubElement(diagram, "shapes")
    inputs = ET.SubElement(diagram, "inputs")

    x0 = REVISION_FOLIO_COLUMNS[0][1]
    add_text(inputs, x0, 30, "HISTORIAL DE REVISIONES", FONT_HEADER)
    for key, x, _w in REVISION_FOLIO_COLUMNS:
        add_text(inputs, x, SUMMARY_ROW_Y0, REVISION_FOLIO_LABELS[key], FONT_SMALL)
    y_rule = SUMMARY_ROW_Y0 + 6
    add_rect(shapes, x0, y_rule, SUMMARY_PAGE_WIDTH, y_rule + 2)
    for i, row in enumerate(page_rows):
        y = SUMMARY_ROW_Y0 + (i + 1) * SUMMARY_ROW_DY
        for key, x, w in REVISION_FOLIO_COLUMNS:
            value = _ellipsize(row.get(key, ""), w)
            if value:
                add_text(inputs, x, y, value, FONT_SMALL)
    return diagram


def build_changelog_folios(project: ET.Element, start_order: int,
                           revisions: list) -> int:
    """Append the paginated changelog folio(s) AFTER the summary folios (order
    numbers continue past them). normalize_revisions guarantees at least one row,
    so at least one folio is always emitted. Returns the count appended."""
    if not revisions:
        return 0
    pages = [revisions[i:i + SUMMARY_ROWS_PER_PAGE]
             for i in range(0, len(revisions), SUMMARY_ROWS_PER_PAGE)]
    total = len(pages)
    for n, page_rows in enumerate(pages, start=1):
        _add_changelog_diagram(project, start_order + n - 1, page_rows, n, total)
    return total


# ── Supply-rail folio ('Alimentación') ───────────────────────────────────────
# A dedicated sheet drawing the project's supply rails/potentials as labelled
# horizontal lines, so the inline power terminals' '→ /Alimentación <potential>'
# cross-references have a target. Rendered like the summary/changelog folios —
# text + shape primitives ONLY, empty <elements>/<conductors> — so it inherits
# the ISO 7200 title block automatically and adds zero element/conductor
# instances. Spanish labels; the rail set defaults to the AC + DC potentials the
# cards use (L1, N, L+, 24V, 0V) plus the safety earth PE.
SUPPLY_DEFAULT_RAILS = ("L1", "N", "L+", "24V", "0V", "PE")
SUPPLY_RAIL_Y0 = 90          # y of the first rail
SUPPLY_RAIL_DY = 70          # pitch between rails
SUPPLY_RAIL_X1 = 80          # rail left x
SUPPLY_RAIL_X2 = 900         # rail right x


def collect_supply_rails(io_mods, *, default=SUPPLY_DEFAULT_RAILS) -> list:
    """Ordered, de-duplicated list of supply potentials to draw on the rail
    folio. Starts from the standard set (L1, N, L+, 24V, 0V, PE) and appends any
    additional supply/common potential a loaded card's power block declares that
    is not already present, so the rails always cover what the cards reference.
    Pure: card power blocks are read via load_module_db (graceful — a card with
    no/omitted power block contributes nothing)."""
    rails = list(default)
    seen = set(rails)
    for mod in io_mods or []:
        db = load_module_db(getattr(mod, "catalog", "") or "")
        if not db:
            continue
        for group in db.get("power_groups", []):
            for name in (group.get("supply"), group.get("common")):
                if isinstance(name, str) and name and name not in seen:
                    seen.add(name)
                    rails.append(name)
    return rails


def _add_supply_diagram(project: ET.Element, order: int, rails: list) -> ET.Element:
    """Render the single supply-rail folio: each potential drawn as a labelled
    horizontal rail (a thin rectangle + its name) using ONLY text + shape
    primitives. Empty <elements>/<conductors> containers keep the schema matching
    the other folios (zero element/terminal/conductor instances). Title is
    'Alimentación'."""
    diagram = ET.SubElement(project, "diagram", {
        "order": str(order), "title": SUPPLY_FOLIO_TITLE,
        "cols": "17", "colsize": "60", "rows": "8", "rowsize": "80",
        "height": str(SUMMARY_HEIGHT), "displaycols": "false",
        "displayrows": "false", "author": "logix_to_qet", "folio": "%id/%total",
        "version": "0.100",
    })
    ET.SubElement(diagram, "defaultconductor", {
        "type": "multi", "num": "", "condsize": "1", "numsize": "9",
        "displaytext": "1", "onetextperfolio": "0",
    })
    # empty containers — NO element/terminal instances, NO conductors
    ET.SubElement(diagram, "elements")
    ET.SubElement(diagram, "conductors")
    shapes = ET.SubElement(diagram, "shapes")
    inputs = ET.SubElement(diagram, "inputs")

    add_text(inputs, SUPPLY_RAIL_X1, 30, SUPPLY_FOLIO_TITLE.upper(), FONT_HEADER)
    for i, name in enumerate(rails):
        y = SUPPLY_RAIL_Y0 + i * SUPPLY_RAIL_DY
        # the rail itself: a thin (2 px tall) rectangle — a horizontal line
        add_rect(shapes, SUPPLY_RAIL_X1, y, SUPPLY_RAIL_X2, y + 2)
        # the potential label at the left end of the rail
        add_text(inputs, SUPPLY_RAIL_X1, y - 12, name, FONT_TEXT)
    return diagram


def build_supply_folios(project: ET.Element, start_order: int,
                        io_mods=None, rails=None) -> int:
    """Append the dedicated supply-rail folio AFTER the changelog folios (order
    numbers continue past them). Mirrors build_summary_folios /
    build_changelog_folios: a single folio drawn with text + shape primitives
    only (empty <elements>/<conductors>), titled 'Alimentación'. Returns the
    count appended (1). `rails` may be supplied directly (tests); otherwise it is
    collected from the cards via collect_supply_rails."""
    if rails is None:
        rails = collect_supply_rails(io_mods)
    if not rails:
        return 0
    _add_supply_diagram(project, start_order, rails)
    return 1


# ── Dedicated terminal-strip (bornero) folio, one per card ───────────────────
# A classic EPLAN strip sheet: one folio per I/O card listing that card's strip
# '-X1' and every terminal on it (-X1:0 … -X1:n) in drawn order, so the engineer
# no longer numbers/draws the regletero by hand. Rendered like the summary /
# changelog / supply folios — text + shape primitives ONLY, empty
# <elements>/<conductors> — so it inherits the ISO 7200 title block and adds zero
# element/conductor instances (the strip terminals themselves are the inline ones
# already drawn on the card folio; this sheet is the listing/overview).
BORNERO_TITLE_PREFIX = "Bornero"
BORNERO_ROW_Y0 = 90          # y of the first terminal row
BORNERO_ROW_DY = 24          # pitch between terminal rows
BORNERO_TERM_X = 90          # terminal-designation column x
BORNERO_FUNC_X = 220         # function/tag column x


def _add_bornero_diagram(project: ET.Element, order: int, mod, points) -> ET.Element:
    """Render one bornero folio for a card: a legible list of the card's strip
    '-X1' terminals (-X1:<channel> + the point's tag/function) drawn with ONLY
    text + shape primitives (empty <elements>/<conductors>), so it carries the
    title block and touches no element/conductor instance. Terminal order is the
    card's drawn-point order; channel is the point index (point-mirrored)."""
    title = f"{BORNERO_TITLE_PREFIX} -{mod.name} ({STRIP_DESIGNATION})"
    diagram = ET.SubElement(project, "diagram", {
        "order": str(order), "title": title,
        "cols": "17", "colsize": "60", "rows": "8", "rowsize": "80",
        "height": str(SUMMARY_HEIGHT), "displaycols": "false",
        "displayrows": "false", "author": "logix_to_qet", "folio": "%id/%total",
        "version": "0.100",
    })
    ET.SubElement(diagram, "defaultconductor", {
        "type": "multi", "num": "", "condsize": "1", "numsize": "9",
        "displaytext": "1", "onetextperfolio": "0",
    })
    # empty containers — NO element/terminal instances, NO conductors
    ET.SubElement(diagram, "elements")
    ET.SubElement(diagram, "conductors")
    shapes = ET.SubElement(diagram, "shapes")
    inputs = ET.SubElement(diagram, "inputs")

    add_text(inputs, BORNERO_TERM_X, 30,
             f"{BORNERO_TITLE_PREFIX.upper()} -{mod.name}   {STRIP_DESIGNATION}",
             FONT_HEADER)
    add_text(inputs, BORNERO_TERM_X, BORNERO_ROW_Y0 - 18, "BORNE", FONT_SMALL)
    add_text(inputs, BORNERO_FUNC_X, BORNERO_ROW_Y0 - 18, "FUNCIÓN / TAG",
             FONT_SMALL)
    y_rule = BORNERO_ROW_Y0 - 12
    add_rect(shapes, BORNERO_TERM_X, y_rule, BORNERO_FUNC_X + 300, y_rule + 2)
    for i, pt in enumerate(points):
        y = BORNERO_ROW_Y0 + i * BORNERO_ROW_DY
        add_text(inputs, BORNERO_TERM_X, y, strip_terminal_label(pt.index),
                 FONT_TEXT)
        function = pt.description or l2e.humanize(pt.tag)
        add_text(inputs, BORNERO_FUNC_X, y, f"{function}   ({pt.tag})", FONT_SMALL)
    return diagram


def build_bornero_folios(project: ET.Element, start_order: int,
                         cards) -> int:
    """Append one dedicated terminal-strip (bornero) folio per card AFTER the
    supply folio (order numbers continue past it). `cards` is an ordered list of
    (mod, points) pairs in the same deterministic order as the drawing folios, so
    the bornero folios mirror the drawing order. Mirrors build_supply_folios:
    text + shape primitives only (empty <elements>/<conductors>). Returns the
    count appended (one per non-empty card)."""
    n = 0
    for mod, pts in cards or []:
        if not pts:
            continue
        _add_bornero_diagram(project, start_order + n, mod, pts)
        n += 1
    return n


# ── Portada (cover) folio (DA.3) ─────────────────────────────────────────────
# A front cover carrying the project's title-block metadata as a legible
# label/value table, drawn with text + shape primitives ONLY (empty
# <elements>/<conductors>) like the supply/bornero folios, so it inherits the
# ISO 7200 title block and adds zero element/conductor instances. EVERY value is
# real data from the resolved title-block fields (or the L5X controller name); a
# field with no value renders a blank cell, never invented. Title-block tokens
# with no project-template data source (department/country/status/type/code) are
# intentionally omitted — there is no data path to populate them (add them to
# project_template.json to surface them on the cover).
PORTADA_TITLE = "Portada"
PORTADA_LABEL_X = 80
PORTADA_VALUE_X = 360
PORTADA_ROW_Y0 = 170
PORTADA_ROW_DY = 40
# (Spanish label, fields key), top→bottom. The controller name (real L5X data,
# not a title-block field) is appended as a final row with key None.
PORTADA_ROWS = (
    ("EMPRESA", "company"),
    ("CLIENTE", "client"),
    ("PROYECTO", "project"),
    ("MÁQUINA", "machine"),
    ("N.º DE PLANO", "drawing_number"),
    ("REVISIÓN", "revision"),
    ("FECHA", "date"),
    ("DIBUJÓ", "drawn_by"),
    ("REVISÓ", "revised_by"),
    ("APROBÓ", "approved_by"),
)


def build_portada_folio(project: ET.Element, order: int, fields: dict,
                        controller: str) -> int:
    """Append the cover (Portada) folio at the given section order, rendered with
    text + shape primitives ONLY (empty <elements>/<conductors>) so it inherits
    the ISO 7200 title block. The heading is the project/drawing name with the
    company beneath it; a label/value table lists the title-block metadata plus
    the L5X controller name. Values are real data; an unset field leaves its
    value cell blank (never invented). Returns 1 (the cover is part of every
    set)."""
    diagram = ET.SubElement(project, "diagram", {
        "order": str(order), "title": PORTADA_TITLE,
        "cols": "17", "colsize": "60", "rows": "8", "rowsize": "80",
        "height": str(SUMMARY_HEIGHT), "displaycols": "false",
        "displayrows": "false", "author": "logix_to_qet", "folio": "%id/%total",
        "version": "0.100",
    })
    ET.SubElement(diagram, "defaultconductor", {
        "type": "multi", "num": "", "condsize": "1", "numsize": "9",
        "displaytext": "1", "onetextperfolio": "0",
    })
    # empty containers — NO element/terminal instances, NO conductors
    ET.SubElement(diagram, "elements")
    ET.SubElement(diagram, "conductors")
    shapes = ET.SubElement(diagram, "shapes")
    inputs = ET.SubElement(diagram, "inputs")

    # cover heading: the project/drawing name, company beneath, a rule under both
    project_name = (fields.get("project") or "").strip()
    add_text(inputs, PORTADA_LABEL_X, 60,
             (project_name or PORTADA_TITLE).upper(), FONT_HEADER)
    company = (fields.get("company") or "").strip()
    if company:
        add_text(inputs, PORTADA_LABEL_X, 92, company, FONT_TEXT)
    add_rect(shapes, PORTADA_LABEL_X, 110, SUMMARY_PAGE_WIDTH, 112)

    # metadata table: label / value, one row each; blanks stay blank
    rows = list(PORTADA_ROWS) + [("CONTROLADOR (L5X)", None)]
    for i, (label, key) in enumerate(rows):
        y = PORTADA_ROW_Y0 + i * PORTADA_ROW_DY
        add_text(inputs, PORTADA_LABEL_X, y, label, FONT_SMALL)
        value = controller if key is None else (fields.get(key) or "")
        value = (value or "").strip()
        if value:
            add_text(inputs, PORTADA_VALUE_X, y, value, FONT_TEXT)
    return 1


# ── Simbología (symbol legend) folio (DA.4) ──────────────────────────────────
# A legend listing ONLY the field-symbol types actually placed in the set (those
# in sym_counts), one row each in the deterministic used-symbol order: the REAL
# symbol glyph (the same embedded element the drawings use, so the legend never
# drifts from the schematics) beside its localized name. Unlike the other
# front-matter folios this one DOES carry element instances (the glyphs); their
# <definition>s are embedded by build_collection, terminal ids are unique per
# diagram, and there are no conductors — so every structural invariant holds.
SIMBOLOGIA_TITLE = "Simbología"
SYM_LABEL_X = 80           # header / legend title x
SYM_GLYPH_X = 170          # glyph anchor x
SYM_NAME_X = 320           # name column x
SYM_ROW_Y0 = 130           # y of the first legend row
SYM_ROW_DY = 70            # pitch between rows (clears the rotated glyph)


def symbol_display_name(entry: dict, lang: str = "es") -> str:
    """Localized display name for a symbol, pulled from its embedded .elmt
    <names> (language-agnostic: the DB stores every locale QET ships). Prefers
    `lang`, then English, then any available name, then the entry's English
    `description`, then its id — so a symbol always has a legible label and no
    locale is hardcoded into the generator."""
    definition = entry.get("_definition")
    names = definition.find("names") if definition is not None else None
    by_lang = {}
    if names is not None:
        for n in names.findall("name"):
            if n.get("lang") and (n.text or "").strip():
                by_lang[n.get("lang")] = n.text.strip()
    if lang in by_lang:
        return by_lang[lang]
    if "en" in by_lang:
        return by_lang["en"]
    if by_lang:
        return next(iter(by_lang.values()))
    return (entry.get("description") or entry.get("id") or "").strip()


def build_symbology_folio(project: ET.Element, order: int, used_symbols: list,
                          lang: str = "es") -> int:
    """Append the symbol-legend (Simbología) folio at the given section order,
    one row per USED symbol type (glyph + localized name) in the deterministic
    used-symbol order. Returns 1 when at least one symbol was placed, else 0 (no
    legend for a symbol-less set, e.g. --no-symbols). The glyph is the actual
    embedded symbol element, so the legend matches the drawings exactly."""
    if not used_symbols:
        return 0
    diagram = ET.SubElement(project, "diagram", {
        "order": str(order), "title": SIMBOLOGIA_TITLE,
        "cols": "17", "colsize": "60", "rows": "8", "rowsize": "80",
        "height": str(SUMMARY_HEIGHT), "displaycols": "false",
        "displayrows": "false", "author": "logix_to_qet", "folio": "%id/%total",
        "version": "0.100",
    })
    ET.SubElement(diagram, "defaultconductor", {
        "type": "multi", "num": "", "condsize": "1", "numsize": "9",
        "displaytext": "1", "onetextperfolio": "0",
    })
    elements = ET.SubElement(diagram, "elements")
    ET.SubElement(diagram, "conductors")     # legend has glyphs but NO conductors
    shapes = ET.SubElement(diagram, "shapes")
    inputs = ET.SubElement(diagram, "inputs")
    ids = itertools.count(1)                 # terminal ids unique per diagram

    add_text(inputs, SYM_LABEL_X, 30, SIMBOLOGIA_TITLE.upper(), FONT_HEADER)
    add_text(inputs, SYM_LABEL_X, SYM_ROW_Y0 - 40, "SÍMBOLO", FONT_SMALL)
    add_text(inputs, SYM_NAME_X, SYM_ROW_Y0 - 40, "DESCRIPCIÓN", FONT_SMALL)
    y_rule = SYM_ROW_Y0 - 34
    add_rect(shapes, SYM_LABEL_X, y_rule, SUMMARY_PAGE_WIDTH, y_rule + 2)
    for i, entry in enumerate(used_symbols):
        y = SYM_ROW_Y0 + i * SYM_ROW_DY
        add_symbol_element(elements, entry, SYM_GLYPH_X, y, "", ids)
        add_text(inputs, SYM_NAME_X, y, symbol_display_name(entry, lang),
                 FONT_TEXT)
    return 1


# ── Document assembly: section page numbering + folio order (DA.2 / DA.5) ────
# Each document section starts on a round page boundary so a section can grow
# without renumbering the sections downstream of it. The drawing-sheet page
# number is ALSO the device-designation / wire-number prefix (decision: the
# designations FOLLOW the printed page), so the 10 schematic sheets carry pages
# 101..110 and their devices are -K101.x .. -K110.x. The non-schematic sections
# carry no designations, so their page numbers are free to use the gap scheme.
# Build order stays dependency-driven (bom_rows / drawn_cards / sym_counts come
# from the drawing loop); reorder_diagrams_by_position re-sorts the <diagram>
# children into this section order just before serialization.
SECTION_PORTADA = 0        # cover sheet (DA.3)
SECTION_SIMBOLOGIA = 1     # symbol legend (DA.4)
SECTION_SUPPLY = 100       # 'Alimentación' rail folio
SECTION_DRAWINGS = 101     # card drawings: 101 .. 101+N-1 (designation prefix)
SECTION_BORNERO = 200      # terminal-strip (bornero) folios, grouped
SECTION_BOM = 300          # BOM / device-index summary folios
SECTION_CHANGELOG = 900    # revision-history folio, LAST


# ── Continuation references (DA.5c) ──────────────────────────────────────────
# On a section that spans several folios, stamp each sheet with prev/next page
# references so a reader can follow the section across its sheets (the classic
# EPLAN "viene de / sigue en"). Abel's gated format (2026-06-14): a compact
# arrow + the SECTION page — "◄ pág. X" points back, "pág. Y ►" points forward —
# placed along the bottom of the sheet, just above the cajetín. The page shown
# is the diagram `order`, i.e. the SECTION page the cajetín already displays
# (DA.5b), NOT QET's document position. Targets the multi-sheet sections only:
# the card drawings and the two auto-paginated lists (borneros, BOM).
#
# Page ranges (lo inclusive, hi exclusive) that receive refs. The single-folio
# sections — Portada (0), Simbología (1), Alimentación (100), Historial (900) —
# fall OUTSIDE these ranges, so they never get refs; and a section that happens
# to occupy a single folio gets none either (its lone sheet has no neighbour).
CONTINUATION_RANGES = (
    (SECTION_DRAWINGS, SECTION_BORNERO),   # 101 .. 199  — card drawings
    (SECTION_BORNERO, SECTION_BOM),        # 200 .. 299  — borneros
    (SECTION_BOM, SECTION_CHANGELOG),      # 300 .. 899  — BOM / device index
)
# A short text lane along the bottom of every sheet: BELOW the tallest card box
# (a full 16-row column's box bottom is ROW_Y0 + 15*ROW_DY + 20 = 645) and
# inside the 660-px page frame, clear of the list folios' 30-px bottom margin
# too. The two refs sit on the same line — prev at the left, next at the right.
CONTINUATION_Y = 648
CONTINUATION_PREV_X = 60     # left end  — "◄ pág. {prev}"
CONTINUATION_NEXT_X = 860    # right end — "pág. {next} ►"


def add_continuation_refs(project: ET.Element) -> int:
    """DA.5c: stamp each folio of a multi-sheet section with prev/next page
    references (EPLAN 'viene de / sigue en'), so a reader can follow the section
    across its sheets. Targets CONTINUATION_RANGES (card drawings + the
    auto-paginated bornero/BOM lists); single-folio sections are skipped
    automatically (outside the ranges, or no neighbour). The reference shows the
    SECTION page (the diagram `order`, which is what the cajetín displays since
    DA.5b) as a compact arrow: '◄ pág. X' points back, 'pág. Y ►' forward. The
    first sheet of a section omits the back ref, the last omits the forward ref.
    Pure annotation — adds only <input> text, leaving every element/terminal/
    conductor count and the folio set untouched. Call before
    reorder_diagrams_by_position (while <project> holds only diagrams). Returns
    the number of reference lines added."""
    numbered = []
    for d in project.findall("diagram"):
        try:
            numbered.append((int(d.get("order")), d))
        except (TypeError, ValueError):
            continue  # graceful: a diagram without an integer order gets no ref
    added = 0
    for lo, hi in CONTINUATION_RANGES:
        section = sorted((p for p in numbered if lo <= p[0] < hi),
                         key=lambda p: p[0])
        for i, (_page, d) in enumerate(section):
            inputs = d.find("inputs")
            if inputs is None:
                inputs = ET.SubElement(d, "inputs")
            if i > 0:
                add_text(inputs, CONTINUATION_PREV_X, CONTINUATION_Y,
                         f"◄ pág. {section[i - 1][0]}", FONT_SMALL)
                added += 1
            if i < len(section) - 1:
                add_text(inputs, CONTINUATION_NEXT_X, CONTINUATION_Y,
                         f"pág. {section[i + 1][0]} ►", FONT_SMALL)
                added += 1
    return added


def reorder_diagrams_by_position(project: ET.Element) -> list:
    """Stably re-sort the <diagram> children of <project> by their integer
    `order` attribute (the DA.5 section page number), decoupling folio POSITION
    from build order (which is driven by data dependencies). QET renders folios
    in document order, so this puts the set into natural drawing order
    (Portada → Simbología → Alimentación → card drawings → borneros → BOM →
    Historial). Diagrams keep the slots they collectively occupied among any
    non-diagram children; a diagram with a missing/non-integer order sorts last,
    preserving its relative position (stable sort). Returns the ordered diagram
    list. Pure structural move — no attribute is changed."""
    children = list(project)
    slots = [i for i, c in enumerate(children) if c.tag == "diagram"]
    if not slots:
        return []

    def key(d: ET.Element):
        try:
            return (0, int(d.get("order")))
        except (TypeError, ValueError):
            return (1, 0)

    ordered = sorted((children[i] for i in slots), key=key)  # stable
    for slot, diagram in zip(slots, ordered):
        children[slot] = diagram
    for c in list(project):
        project.remove(c)
    for c in children:
        project.append(c)
    return ordered


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Convert a ControlLogix L5X export to a QElectroTech project.")
    ap.add_argument("l5x", help="path to the .L5X project export")
    ap.add_argument("-o", "--output", help="output .qet path (default: <l5x>.qet)")
    ap.add_argument("--include-hmi", action="store_true",
                    help="include PanelView/HMI-mapped points")
    ap.add_argument("--no-symbols", action="store_true",
                    help="skip field-device symbol matching (terminals only)")
    ap.add_argument("--wire-scheme", choices=("address", "sequential"),
                    default="address",
                    help="field-conductor wire numbering: 'address' uses the "
                         "EPLAN address verbatim (default); 'sequential' uses "
                         "per-folio W<page>.<n>")
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
    # per-folio wire-number counter keyed by page -> last sequential number,
    # so each folio numbers its own field conductors from 1 (sequential scheme)
    wire_counters: dict[int, int] = {}

    # cajetín / title-block fields: project_template.json merged over blank
    # defaults, with project/machine falling back to the parsed controller name
    tmpl = load_project_template()
    tb_fields = resolve_title_block_fields(tmpl, controller)

    project = ET.Element("project",
                         {"title": tb_fields["project"], "version": "0.80"})
    folios = 0
    # BOM rows accumulated DURING the folio/point traversal below (no second
    # pass): deterministic order == folio/point order, so repeat runs of the
    # same L5X produce byte-identical rows. Scope: the BOM indexes the DRAWN
    # points (one row per module + per drawn point); the points l2e skipped as
    # unmapped get no row, so the BOM mirrors the drawing, not the raw I/O map.
    bom_rows: list[dict] = []
    # ordered (mod, points) pairs for the drawing folios — reused verbatim to
    # build one dedicated bornero folio per card in the same deterministic order.
    drawn_cards: list = []
    # Drawing folios carry the schematic SECTION pages 101..110; that page is
    # also the designation/wire-number prefix (-K101.x …), per the gated
    # decision that designations follow the printed page (DA.5).
    page = SECTION_DRAWINGS
    for mod in io_mods:
        pts = per_module.get(mod.name)
        if not pts:
            continue
        build_folio(project, page, mod, pts, symbols, sym_counts, designations,
                    args.wire_scheme, wire_counters, bom_rows=bom_rows)
        drawn_cards.append((mod, pts))
        page += 1
        folios += 1
    # The remaining sections are built in DEPENDENCY order (the data they need is
    # ready only after the drawing loop) but each is stamped with its own SECTION
    # base page; reorder_diagrams_by_position re-sorts the <diagram> children into
    # the natural drawing order below, just before serialization (DA.2). All are
    # built BEFORE attach_titleblocks so they inherit the ISO 7200 title block.
    # the field symbols actually placed (sym_counts is populated by the drawing
    # loop) — reused for BOTH the legend folio and the embedded collection.
    used = [e for e in symbols if e["id"] in sym_counts]
    # Portada (cover) — the project's title-block metadata + controller name;
    # sorts to the very front (section page 0).
    portada_folios = build_portada_folio(project, SECTION_PORTADA, tb_fields,
                                         controller)
    # Simbología (symbol legend) — one row per used symbol type (glyph + name);
    # sorts right after the cover (section page 1).
    symbology_folios = build_symbology_folio(project, SECTION_SIMBOLOGIA, used)
    # supply-rail folio ('Alimentación') — draws the rails the cards' power
    # blocks reference; sits before the card drawings in the final order.
    supply_folios = build_supply_folios(project, SECTION_SUPPLY, io_mods)
    # dedicated terminal-strip (bornero) folios — one per drawing card, grouped,
    # in the same deterministic order as the drawing folios.
    bornero_folios = build_bornero_folios(project, SECTION_BORNERO, drawn_cards)
    # summary / device-index folio(s).
    summary_folios = build_summary_folios(project, SECTION_BOM, bom_rows)
    # changelog / revision-history folio comes LAST, so the whole document
    # carries a traceability sheet.
    revisions = normalize_revisions(tmpl.get("revisions"), tb_fields)
    changelog_folios = build_changelog_folios(project, SECTION_CHANGELOG, revisions)
    # DA.5c: prev/next continuation refs on the multi-sheet sections (drawings,
    # borneros, BOM). Added while <project> still holds only <diagram> children,
    # before the reorder; pure annotation, so the folio/element counts are
    # unaffected.
    add_continuation_refs(project)
    # DA.2: re-sort the folios into natural drawing order (by section page) now
    # that every section exists. Done before attach_titleblocks/build_collection
    # so at this point <project> holds only <diagram> children.
    reorder_diagrams_by_position(project)
    # cajetín: reference the ISO 7200 template from EVERY folio (drawing +
    # summary). QET renders the framed block + SVG logo + auto sheet number
    # itself; values (company, drawing no., rev, static date…) ride along as
    # per-diagram attributes/properties. The template element is injected
    # verbatim into the serialized XML further down (preserves the SVG).
    # DA.5b: show the SECTION page in the cajetín (QET's %{folio-id} would number
    # by position); rewrite the embedded copy's page token to %{page}, populated
    # per folio from its order. The committed asset is untouched (re-syncable).
    template_text = sectionize_titleblock_page(load_titleblock_template())
    page_total = attach_titleblocks(project, tb_fields, template_text,
                                    filename=Path(out_path).stem)
    build_collection(project, used)

    # CSV sidecar next to the .qet: <output-base>_bom.csv
    bom_path = re.sub(r"\.qet$", "", out_path, flags=re.I) + "_bom.csv"
    write_bom_csv(bom_path, bom_rows)

    pretty = minidom.parseString(ET.tostring(project, encoding="unicode")) \
        .toprettyxml(indent="    ")
    # drop blank lines minidom likes to add around preserved text nodes
    pretty = "\n".join(l for l in pretty.splitlines() if l.strip())
    # inject the title-block template verbatim (the SVG must not be reserialized)
    if template_text:
        pretty = embed_titleblock_templates(pretty, template_text)
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
    n_mod = sum(1 for r in bom_rows if r["category"] == "module")
    n_dev = sum(1 for r in bom_rows if r["category"] == "device")
    n_gen = sum(1 for r in bom_rows if r["category"] == "generic")
    print(f"bom        : {len(bom_rows)} rows ({n_mod} module, {n_dev} device, "
          f"{n_gen} generic) over {summary_folios} summary folio(s)", file=err)
    print(f"changelog  : {len(revisions)} revision(s) over "
          f"{changelog_folios} folio(s)", file=err)
    print(f"supply     : {supply_folios} '{SUPPLY_FOLIO_TITLE}' rail folio(s)",
          file=err)
    print(f"bornero    : {bornero_folios} terminal-strip ({STRIP_DESIGNATION}) "
          f"folio(s), one per card", file=err)
    print(f"portada    : {portada_folios} cover folio(s)", file=err)
    print(f"simbología : {symbology_folios} legend folio(s), "
          f"{len(used)} symbol type(s)", file=err)
    if page_total:
        print(f"title block: ISO 7200 ({TITLEBLOCK_NAME}) — "
              f"{tb_fields['company'] or '(no company)'}, {page_total} folio(s)",
              file=err)
    else:
        print(f"title block: none ({TITLEBLOCK_PATH.name} absent)", file=err)
    print(f"output     : {out_path}", file=err)
    print(f"bom csv    : {bom_path}", file=err)
    return 0


if __name__ == "__main__":
    sys.exit(main())
