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


PROJECT_TEMPLATE_PATH = Path(__file__).resolve().parent / "project_template.json"


def load_project_template(path=PROJECT_TEMPLATE_PATH) -> dict:
    """Load the cajetín (title-block) config, merged over the blank built-in
    defaults. Mirrors load_module_db/load_symbol_db: stdlib json, utf-8-sig, and
    a graceful fallback so a missing OR malformed file yields all-default fields
    (every value a clean string, never garbage). Only string values for known
    keys are taken; unknown keys are ignored and missing keys keep their blank
    default, so the title block always has every field present."""
    tmpl = dict(PROJECT_TEMPLATE_DEFAULTS)
    p = Path(path)
    if not p.is_file():
        return tmpl
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: ignoring {p.name}: {exc}", file=sys.stderr)
        return tmpl
    if isinstance(data, dict):
        for key in tmpl:
            value = data.get(key)
            if isinstance(value, str):
                tmpl[key] = value
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
        ET.SubElement(props, "property",
                      {"name": token, "show": "1"}).text = values.get(token, "")
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


def add_conductor(conductors: ET.Element, terminal1: int, terminal2: int,
                  num: str = ""):
    ET.SubElement(conductors, "conductor", {
        "terminal1": str(terminal1), "terminal2": str(terminal2),
        "type": "multi", "num": num, "x": "0", "y": "0",
        "condsize": "1", "numsize": "9", "displaytext": "1",
        "onetextperfolio": "0", "freezeLabel": "false",
    })


def build_folio(project: ET.Element, order: int, mod, points,
                symbols: list[dict], sym_counts: dict, designations: dict,
                wire_scheme: str = "address", wire_counters: dict | None = None,
                bom_rows: list | None = None):
    """One diagram per I/O card; points already sorted.

    If `bom_rows` is given, schema rows are appended to it DURING this single
    traversal (no second pass, no recomputation): one (module) row for the
    card, then one (device) row per matched field device or one (generic) row
    per unmatched/analog point, in the deterministic folio/point order. The
    accumulator is data-only — appending to it does not touch the emitted XML,
    so the drawing folios stay byte-for-byte identical."""
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
    add_text(inputs, 40, 30, header, FONT_HEADER)
    if db:
        sub = " — ".join(s for s in (db.get("vendor"), db.get("description"),
                                     db.get("rtb")) if s)
        add_text(inputs, 40, 44, sub, FONT_SMALL)
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
            # the field conductor wires the I/O terminal pin to its matched
            # field-device symbol pin; the address is a defined source point, so
            # it carries a visible wire number (None only if address is empty)
            num = wire_number(address, order, wire_scheme, wire_counters) or ""
            add_conductor(conductors, term_ids[2], pin_ids[west], num)
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
        elif bom_rows is not None:
            # (generic) BOM row — unmatched point (or any analog point): only
            # tag/address; designation and catalog_or_type stay blank so we
            # never invent a device for a point that matched no symbol.
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
        "height": str(SUMMARY_HEIGHT), "displaycols": "true",
        "displayrows": "true", "author": "logix_to_qet", "folio": "%id/%total",
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
    tb_fields = resolve_title_block_fields(load_project_template(), controller)

    project = ET.Element("project",
                         {"title": tb_fields["project"], "version": "0.80"})
    order = 1
    folios = 0
    # BOM rows accumulated DURING the folio/point traversal below (no second
    # pass): deterministic order == folio/point order, so repeat runs of the
    # same L5X produce byte-identical rows. Scope: the BOM indexes the DRAWN
    # points (one row per module + per drawn point); the points l2e skipped as
    # unmapped get no row, so the BOM mirrors the drawing, not the raw I/O map.
    bom_rows: list[dict] = []
    for mod in io_mods:
        pts = per_module.get(mod.name)
        if not pts:
            continue
        build_folio(project, order, mod, pts, symbols, sym_counts, designations,
                    args.wire_scheme, wire_counters, bom_rows=bom_rows)
        order += 1
        folios += 1
    # summary folio(s) come AFTER the drawing folios (order continues past
    # them); the drawing folios' XML is untouched.
    summary_folios = build_summary_folios(project, order, bom_rows)
    # cajetín: reference the ISO 7200 template from EVERY folio (drawing +
    # summary). QET renders the framed block + SVG logo + auto sheet number
    # itself; values (company, drawing no., rev, static date…) ride along as
    # per-diagram attributes/properties. The template element is injected
    # verbatim into the serialized XML further down (preserves the SVG).
    template_text = load_titleblock_template()
    page_total = attach_titleblocks(project, tb_fields, template_text,
                                    filename=Path(out_path).stem)
    used = [e for e in symbols if e["id"] in sym_counts]
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
