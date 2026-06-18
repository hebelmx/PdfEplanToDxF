#!/usr/bin/env python3
"""render_plant.py — E6 (c1): whole-plant distributed-I/O renderer.

Compose ONE QElectroTech `.qet` for the FULL Siemens plant (all 9 stations of
the IMV1 distributed-I/O project) as PER-STATION NUMERIC BANDS. The single-
station renderer (`logix_to_qet.render_project`) draws ONE PlcProject; this
module draws an ORDERED `list[PlcProject]` (from
`plc_ir.build_tia_distributed_project`) into one document, reusing the EXISTING
folio builders verbatim so nothing in the single-station / Rockwell path is
touched.

Layout (folio `order` == DA.5 section page):
  Front matter (shared, built ONCE)
    0   Portada       — plant cover: a 9-row station table (station / functional
                        name / owning CPU / page band)
    1   Simbología    — the UNION of every symbol used across all stations
    2   Red PROFINET  — the shared network_nodes (built once)
    3   Índice        — enumerates EVERY folio across all bands (built LAST)
    4   Rack          — ONE plant rack folio GROUPED BY STATION (a station
                        sub-header over each station's module boxes)
  Per-station bands (station index i, 0-based)  →  band base = (i+1)*100
    base+1, base+2, …   I/O card folios   (build_folio / build_split_card_folio)
    base+50, base+51, … bornero folios    (build_bornero_folios)
  Back matter (shared)
    1000+ BOM          — aggregates ALL stations' bom_rows in station order
    1100+ E/S PROFINET fuera de módulo (E6 c2) — the off-module section: per
                        function (Drives / Identification / Coordination/Safety) a
                        summary table + packed per-element placeholder boxes
    1900  Historial    — changelog

Every band's I/O folios reuse build_folio / build_split_card_folio (incl. the
split-pair detection and the RESERVA spare drawing) EXACTLY as render_project
does; the bornero folios reuse build_bornero_folios. The whole project is post-
processed ONCE (continuation refs → reorder → title blocks).

STANDARD LIBRARY ONLY. NEVER invents — an unknown functional name degrades to
"" (the label becomes "<station> — <CPU>"), never a guess.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

import logix_to_qet as lq


# ── Per-station numeric bands ────────────────────────────────────────────────
# Station i (0-based) occupies the 100-band base = (i+1)*100: Q100 -> 100,
# Q200 -> 200, …, the 1214C station -> 900. Within a band the I/O card folios
# take base+1.. (≤49 slots, far more than the ~15 a station ever needs) and the
# bornero folios take base+50.. (the 50..99 half of the band). The shared back
# matter (BOM 300+, changelog 900) lives in its own SECTION_* orders, which the
# bands never reach (no station's I/O/bornero ever hits +50 worth of cards).
BAND_SIZE = 100
BAND_IO_OFFSET = 1        # first I/O folio at base+1
BAND_BORNERO_OFFSET = 50  # first bornero folio at base+50

# Shared front/back-matter section orders. The per-station bands occupy
# (i+1)*100 + 1 .. +99 — i.e. orders 101..999 — so the front matter MUST stay
# in 0..99 and the back matter MUST stay ABOVE 999. Each front-matter section
# gets its own 10-wide slot so a section that paginates (e.g. símbología with a
# plant-wide symbol union) never spills into the next one. Back matter sits in a
# 1000+ band, clear of every station band and the changelog.
#
# (The single-station SECTION_* constants pack BOM at 300 / changelog at 900,
# which would collide with the Q300 / 1214C station bands here — so the plant
# uses its OWN back-matter orders. Front-matter section identities are reused
# from logix_to_qet where they don't collide: PORTADA 0 is fine; the rest move.)
PLANT_SEC_PORTADA = 0       # plant cover (== SECTION_PORTADA)
PLANT_SEC_SIMBOLOGIA = 10   # symbol-legend union (paginates 10, 11, …)
PLANT_SEC_NETWORK = 30      # shared PROFINET network
PLANT_SEC_INDEX = 40        # plant índice (built LAST)
PLANT_SEC_RACK = 50         # station-grouped rack
PLANT_SEC_BOM = 1000        # aggregated BOM (paginates 1000, 1001, …)
# E6 (c2): the off-module PROFINET I/O section. It sorts AFTER the station bands
# and AFTER the BOM (which paginates 1000..10xx) and BEFORE the changelog, so the
# índice reading order is front → stations → BOM → off-module → changelog. Its
# own folios (per-function summary tables + packed per-element box folios) take
# consecutive orders from this base upward (a running counter), so the section
# never collides with the BOM band below it or the changelog above it.
PLANT_SEC_OFFMODULE = 1100  # off-module PROFINET I/O (summary tables + boxes)
PLANT_SEC_CHANGELOG = 1900  # changelog, LAST


def band_base(station_index: int) -> int:
    """The 100-band base order for the station at 0-based `station_index`."""
    return (station_index + 1) * BAND_SIZE


# ── Functional-name derivation (Abel's spec) ─────────────────────────────────
# The station section label is "<station> — <functional> — <owning CPU>". The
# functional part is REAL data when the .aml station name carries it (e.g.
# "Q100-Cooling1/UV" -> "Cooling1/UV"); otherwise it is conservatively DERIVED
# from the station's tag descriptions, and blank when nothing is clear. NEVER
# invented.

# A PANEL designation token at the head of an .aml station name — the "Q100" in
# "Q100-Cooling1/UV" or the bare "Q200". The functional suffix is whatever
# follows the FIRST "-" after that token. Deliberately STRICT: a single letter
# group + digits (the Q-panel grammar). A TIA default device name like
# "S7-1200 station_1" is NOT a panel designation ("S7" is the CPU family, not a
# panel), so it does NOT match — its suffix ("1200 station_1") is the device
# MODEL, not a function, and must never be surfaced as one. Such names fall
# through to conservative derivation (or blank), never inventing a function.
_STATION_TOKEN_RE = re.compile(r"^Q\d+", re.IGNORECASE)

# Stop-words and noise skipped when deriving a theme from descriptions: short
# function words plus pure-number / address-like tokens. Conservative — when in
# doubt a word is KEPT (so a real theme word is never dropped), and the result
# is only used when it dominates a clear plurality.
_DERIVE_STOPWORDS = frozenset({
    "the", "a", "an", "of", "to", "for", "and", "or", "in", "on", "at", "by",
    "with", "de", "la", "el", "los", "las", "del", "y", "o", "en", "para",
    "con", "por", "un", "una", "señal", "signal", "status", "estado",
})
_DERIVE_MIN_LEN = 3          # ignore very short leading tokens
_DERIVE_PLURALITY = 0.30     # a derived theme must lead ≥30% of described points


def _significant_leading_word(description: str) -> str:
    """The first SIGNIFICANT word of a tag description (lowercased), skipping
    stop-words and pure-number/address tokens. Returns "" when the description
    has no significant leading word. NEVER invents."""
    for raw in re.split(r"[\s/,_\-]+", (description or "").strip()):
        word = raw.strip()
        if not word:
            continue
        low = word.lower()
        if low in _DERIVE_STOPWORDS:
            continue
        if len(low) < _DERIVE_MIN_LEN:
            continue
        # skip pure numbers / address-like tokens (digits with separators)
        if re.fullmatch(r"[\d.:%]+", low):
            continue
        return low
    return ""


def _derive_functional_name(station_ir) -> str:
    """Conservatively derive a functional theme from the station's tag
    descriptions: the most common SIGNIFICANT leading word across the described
    points, returned Title-cased when it leads a clear plurality
    (≥ _DERIVE_PLURALITY of the described points), else "". NEVER invents."""
    leads: dict[str, int] = {}
    described = 0
    for pt in getattr(station_ir, "points", None) or []:
        # NON-DEVICE points (VS_/'Vsupply' supply monitors, permits) describe the
        # device's SUPPLY/interlock, not the station's function — skip them so a
        # 'Vsupply …'-dominated count never masks the real theme (reuses the IR's
        # own no_symbol classification; never invents).
        if getattr(pt, "no_symbol", False):
            continue
        desc = (getattr(pt, "description", "") or "").strip()
        if not desc:
            continue
        described += 1
        word = _significant_leading_word(desc)
        if word:
            leads[word] = leads.get(word, 0) + 1
    if not leads or described == 0:
        return ""
    # winner = most frequent leading word (tie-break alphabetically, determinism)
    word, count = max(leads.items(), key=lambda kv: (kv[1], -ord(kv[0][0])))
    word = min((w for w in leads if leads[w] == count), default=word)
    count = leads[word]
    if count / described < _DERIVE_PLURALITY:
        return ""
    return word.title()


def _station_functional_name(station_ir) -> tuple[str, bool]:
    """Return ``(functional_name, derived)`` for a station IR.

    * If the .aml station name carries a functional suffix after the leading
      Qxxx/Sxxx token (e.g. "Q100-Cooling1/UV" -> "Cooling1/UV": split on the
      FIRST "-" after the token, keep the remainder), use it verbatim and mark
      it REAL (``derived=False``).
    * Else (a bare "Q200" etc.) SEMANTICALLY DERIVE from the station's tag
      descriptions; mark it ``derived=True``. If nothing is clear, "" (the
      label degrades to "<station> — <CPU>") — NEVER invented.
    """
    name = (getattr(station_ir, "name", "") or "").strip()
    m = _STATION_TOKEN_RE.match(name)
    if m:
        rest = name[m.end():]
        if rest.startswith("-"):
            suffix = rest[1:].strip()
            if suffix:
                return suffix, False
    # bare station name with no suffix → derive conservatively (or blank)
    return _derive_functional_name(station_ir), True


def station_section_label(station_ir, functional: str = None) -> str:
    """The station section label, Abel's spec: ``<station> — <functional> —
    <owning CPU>``. The functional and/or CPU segments are OMITTED (with their
    separators) when blank, so the label degrades gracefully and never shows an
    empty segment. NEVER invents."""
    name = (getattr(station_ir, "name", "") or "").strip()
    if functional is None:
        functional, _ = _station_functional_name(station_ir)
    cpu = (getattr(station_ir, "controller_cpu", "") or "").strip()
    parts = [p for p in (name, (functional or "").strip(), cpu) if p]
    return " — ".join(parts)


# ── Per-station folio building (reuses the existing builders verbatim) ───────
def _group_points_per_module(points):
    """Group points per module exactly as render_project does (first tag wins on
    duplicates, deterministic sort). Returns dict[module_name -> list[point]]."""
    per_module: dict[str, list] = {}
    seen = set()
    for pt in sorted(points, key=lambda p: (p.module.rack, p.module.slot or 0,
                                            p.direction, p.analog, p.index,
                                            p.tag)):
        key = (pt.module.name, pt.direction, pt.index, pt.analog)
        if key in seen:
            continue
        seen.add(key)
        per_module.setdefault(pt.module.name, []).append(pt)
    return per_module


def _build_station_bands(project, station_ir, base, *, symbols, sym_counts,
                         designations, wire_scheme, wire_counters, bom_rows,
                         spare_counter):
    """Build ONE station's I/O card folios (base+1..) and bornero folios
    (base+50..) into the shared `project`, mirroring render_project's drawing
    loop EXACTLY (split-pair detection, RESERVA spares, drawn_cards order).

    The shared accumulators (sym_counts / designations / wire_counters /
    bom_rows / spare_counter) are passed in so symbols/BOM aggregate across the
    whole plant. Returns ``(io_folios, bornero_folios, n_points)``."""
    io_mods = station_ir.io_mods
    per_module = _group_points_per_module(station_ir.points)

    page = base + BAND_IO_OFFSET
    io_folios = 0
    drawn_cards: list = []
    i = 0
    while i < len(io_mods):
        mod = io_mods[i]
        sib = io_mods[i + 1] if i + 1 < len(io_mods) else None
        if sib is not None and lq._is_split_sibling_pair(mod, sib):
            left_pts = per_module.get(mod.name) or []
            right_pts = per_module.get(sib.name) or []
            lq.build_split_card_folio(project, page, mod, left_pts, sib,
                                      right_pts, symbols, sym_counts,
                                      designations, wire_scheme, wire_counters,
                                      bom_rows=bom_rows,
                                      spare_counter=spare_counter)
            drawn_cards.append((mod, left_pts))
            drawn_cards.append((sib, right_pts))
            page += 1
            io_folios += 1
            i += 2
            continue
        pts = per_module.get(mod.name) or []
        lq.build_folio(project, page, mod, pts, symbols, sym_counts,
                       designations, wire_scheme, wire_counters,
                       bom_rows=bom_rows, spare_counter=spare_counter)
        drawn_cards.append((mod, pts))
        page += 1
        io_folios += 1
        i += 1

    bornero_folios = lq.build_bornero_folios(
        project, base + BAND_BORNERO_OFFSET, drawn_cards)
    n_points = sum(len(v) for v in per_module.values())
    return io_folios, bornero_folios, n_points


# ── Plant front matter (custom: cover table + station-grouped rack) ──────────
PLANT_PORTADA_TITLE = "Portada"
# station-table geometry on the plant cover (label/value primitives, like the
# single-station portada's metadata table)
_PCOL_STATION_X = 60
_PCOL_FUNC_X = 300
_PCOL_CPU_X = 560
_PCOL_BAND_X = 900
_PROW_HEAD_Y = 150
_PROW_Y0 = 178
_PROW_DY = 26


def _build_plant_portada(project, fields, station_rows):
    """Append the plant cover folio at SECTION_PORTADA: the project heading +
    company, then a station table (station / functional / owning CPU / page
    band), one row per station. `station_rows` is an ordered list of
    ``(station, functional, cpu, band)`` tuples (blanks render blank, never
    invented). Text + shape primitives only (no elements/conductors), so it
    inherits the ISO 7200 title block. Returns 1."""
    diagram = ET.SubElement(project, "diagram", {
        "order": str(PLANT_SEC_PORTADA), "title": PLANT_PORTADA_TITLE,
        "cols": "17", "colsize": "60", "rows": "8", "rowsize": "80",
        "height": str(lq.SUMMARY_HEIGHT), "displaycols": "false",
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

    project_name = (fields.get("project") or "").strip()
    lq.add_text(inputs, _PCOL_STATION_X, 60,
                (project_name or "PLANTA").upper() + " — PLANTA",
                lq.FONT_HEADER)
    company = (fields.get("company") or "").strip()
    if company:
        lq.add_text(inputs, _PCOL_STATION_X, 92, company, lq.FONT_TEXT)
    lq.add_rect(shapes, _PCOL_STATION_X, 110, lq.SUMMARY_PAGE_WIDTH, 130)

    # station-table column headers + an underline rule
    lq.add_text(inputs, _PCOL_STATION_X, _PROW_HEAD_Y, "ESTACIÓN", lq.FONT_SMALL)
    lq.add_text(inputs, _PCOL_FUNC_X, _PROW_HEAD_Y, "FUNCIÓN", lq.FONT_SMALL)
    lq.add_text(inputs, _PCOL_CPU_X, _PROW_HEAD_Y, "CPU", lq.FONT_SMALL)
    lq.add_text(inputs, _PCOL_BAND_X, _PROW_HEAD_Y, "PÁGS.", lq.FONT_SMALL)
    lq.add_rect(shapes, _PCOL_STATION_X, _PROW_HEAD_Y + 4,
                lq.SUMMARY_PAGE_WIDTH, _PROW_HEAD_Y + 5)

    for r, (station, functional, cpu, band) in enumerate(station_rows):
        y = _PROW_Y0 + r * _PROW_DY
        lq.add_text(inputs, _PCOL_STATION_X, y, station or "", lq.FONT_SMALL)
        if functional:
            lq.add_text(inputs, _PCOL_FUNC_X, y, functional, lq.FONT_SMALL)
        if cpu:
            lq.add_text(inputs, _PCOL_CPU_X, y, cpu, lq.FONT_SMALL)
        lq.add_text(inputs, _PCOL_BAND_X, y, band or "", lq.FONT_SMALL)
    return 1


# station-grouped rack: a sub-header band per station over that station's
# module boxes, drawn with the same RACK_* box geometry as build_rack_folio.
_PRACK_HEAD_Y = 56          # first station sub-header baseline
_PRACK_SUBHEAD_DY = 14      # gap from a sub-header to its rack rail
_PRACK_STATION_GAP = 24     # vertical gap below a station's boxes


def _build_plant_rack(project, station_groups):
    """Append ONE plant rack folio at SECTION_RACK, GROUPED BY STATION: each
    station gets a sub-header line, then its modules drawn as RACK_* boxes on a
    mounting rail beneath it, stacked top-to-bottom. `station_groups` is an
    ordered list of ``(label, modules)``. Text + shape primitives only. Returns
    1 when any station has modules, else 0 (graceful)."""
    if not any(mods for _label, mods in station_groups):
        return 0
    diagram = ET.SubElement(project, "diagram", {
        "order": str(PLANT_SEC_RACK), "title": lq.RACK_TITLE,
        "cols": "17", "colsize": "60", "rows": "8", "rowsize": "80",
        "height": str(lq.RACK_PAGE_H), "displaycols": "false",
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

    lq.add_text(inputs, lq.RACK_X_MARGIN, 30, lq.RACK_TITLE.upper(),
                lq.FONT_HEADER)

    col_pitch = lq.RACK_BOX_W + lq.RACK_COL_GAP
    y = _PRACK_HEAD_Y
    for label, modules in station_groups:
        mods = sorted(list(modules), key=lq._rack_sort_key)
        # the station sub-header even when it has no modules, so the cover and
        # the rack agree on the station roster
        lq.add_text(inputs, lq.RACK_X_MARGIN, y, label, lq.FONT_TEXT)
        rows = (max(len(mods), 1) + lq.RACK_COLS - 1) // lq.RACK_COLS
        for r in range(rows):
            rail_y = y + _PRACK_SUBHEAD_DY + r * (lq.RACK_BOX_H
                                                  + lq.RACK_ROW_GAP + 16)
            lq.add_rect(shapes, lq.RACK_X_MARGIN, rail_y,
                        lq.RACK_PAGE_W - lq.RACK_X_MARGIN,
                        rail_y + lq.RACK_RAIL_H)
        for i, mod in enumerate(mods):
            col = i % lq.RACK_COLS
            row = i // lq.RACK_COLS
            x = lq.RACK_X_MARGIN + col * col_pitch
            rail_y = y + _PRACK_SUBHEAD_DY + row * (lq.RACK_BOX_H
                                                    + lq.RACK_ROW_GAP + 16)
            box_y = rail_y + 16
            lq._add_rack_box(shapes, inputs, x, box_y, mod)
        used_rows = (len(mods) + lq.RACK_COLS - 1) // lq.RACK_COLS or 1
        y += (_PRACK_SUBHEAD_DY + used_rows * (lq.RACK_BOX_H + lq.RACK_ROW_GAP
                                               + 16) + _PRACK_STATION_GAP)
    return 1


# ── Title prefixing ──────────────────────────────────────────────────────────
def _prefix_titles(diagrams, label):
    """Prefix each diagram's `title` with ``"<label> · "`` so a station's I/O /
    bornero folios carry the station section label. Blank labels are left
    untouched. The order attribute is NOT changed."""
    if not label:
        return
    for d in diagrams:
        t = d.get("title") or ""
        d.set("title", f"{label} · {t}")


# ── Off-module PROFINET I/O section (E6 c2) ──────────────────────────────────
# A NEW section drawing the ~231 addressed tags that parse as real I/O addresses
# yet fall outside every I/O module's .aml range — drive telegrams / RFID / plant-
# coordination signals on mixed-brand PROFINET nodes, NOT on a Siemens I/O card.
# Grouped BY FUNCTION (Drives / Identification / Coordination/Safety) → PER
# ELEMENT (tag-name prefix). Each function gets: a SUMMARY TABLE folio (every tag:
# address | tag | description | element, paginated, mirroring build_summary_folios'
# text-grid) THEN packed per-element placeholder BOX folios (each box = the element
# name + its address range, with one labelled borne_2 STUB per tag, like the card
# folios' RESERVA stubs — placeholders, no conductors required). NEVER invents:
# every address/name/description is the real tag-table data.
OFFMODULE_SECTION_TITLE = "E/S PROFINET fuera de módulo"

# The section-title TEMPLATE: the protocol word ("PROFINET") is variable so a
# vendor whose off-module devices sit on a different bus (e.g. S7-300 servos on
# PROFIBUS-DP) can title each function with its real bus, while the default
# (None bus_labels) reproduces OFFMODULE_SECTION_TITLE byte-for-byte.
_OFFMODULE_TITLE_TEMPLATE = "E/S {bus} fuera de módulo"
_OFFMODULE_DEFAULT_BUS = "PROFINET"


def _offmodule_section_title(func, bus_labels) -> str:
    """The off-module section title for ONE function. When ``bus_labels`` is None
    the result is exactly ``OFFMODULE_SECTION_TITLE`` (the E6/TIA plant path —
    byte-for-byte unchanged). When provided, the protocol word is
    ``bus_labels.get(func, "PROFINET")`` so each function reads its real bus."""
    if bus_labels is None:
        return OFFMODULE_SECTION_TITLE
    bus = bus_labels.get(func, _OFFMODULE_DEFAULT_BUS)
    return _OFFMODULE_TITLE_TEMPLATE.format(bus=bus)

# Summary-table folio: reuse the BOM/summary text-grid geometry. Columns laid out
# left→right inside SUMMARY_PAGE_WIDTH; description gets the widest budget.
_OFF_SUMMARY_COLUMNS = (
    ("address", 20, 12),
    ("tag", 130, 34),
    ("description", 430, 64),
    ("element", 880, 18),
)
_OFF_SUMMARY_LABELS = {
    "address": "DIRECCIÓN", "tag": "TAG", "description": "DESCRIPCIÓN",
    "element": "ELEMENTO",
}

# Per-element box geometry: small "mini I/O card" boxes packed several per folio.
# Two columns of boxes; a box is a rectangle with a heading (name + address range)
# and one stub row per tag (a borne_2 terminal + address/tag/description text).
_OFFBOX_COL_X = (60, 540)      # left x of the box in each of the 2 columns
_OFFBOX_W = 440                # box width
_OFFBOX_HEAD_Y = 50            # first box-row top y on a folio
_OFFBOX_STUB_DY = 18           # per-stub vertical pitch
_OFFBOX_HEAD_H = 26            # heading band height above the first stub
_OFFBOX_STUB_X = 30            # stub terminal x inset from the box left edge
_OFFBOX_PAD = 14               # vertical pad below the last stub inside the box
_OFFBOX_GAP = 16               # vertical gap between stacked boxes in a column
_OFFBOX_PAGE_H = lq.SUMMARY_HEIGHT  # 660
_OFFBOX_BOTTOM = _OFFBOX_PAGE_H - 24


def _offmodule_summary_rows(element_list) -> list[dict]:
    """Flatten one function's element_list into summary table rows (one per tag),
    in element order then tag order: {address, tag, description, element}."""
    rows = []
    for el in element_list:
        for raw, name, desc in el["tags"]:
            rows.append({"address": raw, "tag": name,
                         "description": desc, "element": el["name"]})
    return rows


def _add_offmodule_summary_diagram(project, order, title, page_rows,
                                   page_no, page_total):
    """One off-module summary-table folio: a legible text grid (header + rows),
    text + shape primitives ONLY (empty <elements>/<conductors>) so it inherits
    the ISO title block, mirroring _add_summary_diagram's style."""
    diagram = ET.SubElement(project, "diagram", {
        "order": str(order),
        "title": f"{title} ({page_no}/{page_total})",
        "cols": "17", "colsize": "60", "rows": "8", "rowsize": "80",
        "height": str(lq.SUMMARY_HEIGHT), "displaycols": "false",
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

    x0 = _OFF_SUMMARY_COLUMNS[0][1]
    lq.add_text(inputs, x0, 30,
                f"{title.upper()}   (PÁG. {page_no} DE {page_total})",
                lq.FONT_HEADER)
    for key, x, _w in _OFF_SUMMARY_COLUMNS:
        lq.add_text(inputs, x, lq.SUMMARY_ROW_Y0, _OFF_SUMMARY_LABELS[key],
                    lq.FONT_SMALL)
    for i, row in enumerate(page_rows):
        y = lq.SUMMARY_ROW_Y0 + (i + 1) * lq.SUMMARY_ROW_DY
        for key, x, w in _OFF_SUMMARY_COLUMNS:
            value = lq._ellipsize(row.get(key, ""), w)
            if value:
                lq.add_text(inputs, x, y, value, lq.FONT_SMALL)
    return diagram


def _build_offmodule_summary(project, start_order, func, element_list,
                             bus_labels=None) -> int:
    """Append the paginated summary-table folio(s) for ONE function (every tag in
    that function). Returns the count appended. Orders run start_order .."""
    rows = _offmodule_summary_rows(element_list)
    if not rows:
        return 0
    per = lq.SUMMARY_ROWS_PER_PAGE
    pages = [rows[i:i + per] for i in range(0, len(rows), per)]
    title = f"{_offmodule_section_title(func, bus_labels)} · {func} — resumen"
    for n, page_rows in enumerate(pages, start=1):
        _add_offmodule_summary_diagram(project, start_order + n - 1, title,
                                       page_rows, n, len(pages))
    return len(pages)


def _offbox_height(n_tags: int) -> int:
    """Drawn height of one element box with `n_tags` stubs."""
    return _OFFBOX_HEAD_H + max(n_tags, 1) * _OFFBOX_STUB_DY + _OFFBOX_PAD


def _pack_offmodule_boxes(element_list) -> list[list[tuple]]:
    """Pack the function's element boxes into folios: a 2-column layout, boxes
    stacked top→bottom per column until the next box would overflow the page,
    then the next column, then a new folio. Returns a list of folios; each folio
    is a list of (col_x, top_y, element) placements. Deterministic."""
    folios: list[list[tuple]] = []
    cur: list[tuple] = []
    col = 0
    y = _OFFBOX_HEAD_Y
    for el in element_list:
        h = _offbox_height(len(el["tags"]))
        if y + h > _OFFBOX_BOTTOM:
            # this column is full — move to the next column, or a new folio
            col += 1
            y = _OFFBOX_HEAD_Y
            if col >= len(_OFFBOX_COL_X):
                folios.append(cur)
                cur = []
                col = 0
        cur.append((_OFFBOX_COL_X[col], y, el))
        y += h + _OFFBOX_GAP
    if cur:
        folios.append(cur)
    return folios


def _draw_offmodule_box(elements, shapes, inputs, ids, col_x, top_y, el):
    """Draw ONE element placeholder box at (col_x, top_y): a rectangle headed by
    the element name + address RANGE, with one labelled borne_2 STUB per tag
    (address + tag + description) for the user to wire. Placeholder — no
    conductors. Reuses add_terminal_element (borne_2, like the RESERVA stubs);
    `ids` keeps every terminal id unique per diagram."""
    tags = el["tags"]
    box_bottom = top_y + _offbox_height(len(tags))
    lq.add_rect(shapes, col_x, top_y, col_x + _OFFBOX_W, box_bottom)
    rng = el["addr_min"] if el["addr_min"] == el["addr_max"] \
        else f"{el['addr_min']} … {el['addr_max']}"
    lq.add_text(inputs, col_x + 6, top_y - 4,
                f"-{el['name']}   [{rng}]", lq.FONT_TEXT)
    # a rule under the heading band
    head_rule_y = top_y + _OFFBOX_HEAD_H - 6
    lq.add_rect(shapes, col_x, head_rule_y, col_x + _OFFBOX_W, head_rule_y + 1)
    for i, (raw, name, desc) in enumerate(tags):
        y = top_y + _OFFBOX_HEAD_H + i * _OFFBOX_STUB_DY
        x = col_x + _OFFBOX_STUB_X
        # the wiring STUB: a borne_2 terminal (no conductor — a placeholder the
        # user finishes), labelled with the real address; function = the tag name
        # so QET shows it on the terminal. ids stay diagram-unique.
        lq.add_terminal_element(elements, x, y, raw, name, ids)
        # the row text: address, tag, then description (ellipsized so it stays in
        # the box). NEVER invents — a blank description prints nothing.
        lq.add_text(inputs, x + 16, y - 6,
                    f"{lq._ellipsize(raw, 9):<9}  {lq._ellipsize(name, 28)}",
                    lq.FONT_SMALL)
        if desc:
            lq.add_text(inputs, x + 16, y + 4, lq._ellipsize(desc, 52),
                        lq.FONT_SMALL)


def _add_offmodule_box_diagram(project, order, title, placements):
    """One packed per-element box folio: several element placeholder boxes drawn
    with borne_2 stubs + text + shapes. Carries the ISO title block."""
    diagram = ET.SubElement(project, "diagram", {
        "order": str(order), "title": title,
        "cols": "17", "colsize": "60", "rows": "8", "rowsize": "80",
        "height": str(_OFFBOX_PAGE_H), "displaycols": "false",
        "displayrows": "false", "author": "logix_to_qet", "folio": "%id/%total",
        "version": "0.100",
    })
    ET.SubElement(diagram, "defaultconductor", {
        "type": "multi", "num": "", "condsize": "1", "numsize": "9",
        "displaytext": "1", "onetextperfolio": "0",
    })
    elements = ET.SubElement(diagram, "elements")
    ET.SubElement(diagram, "conductors")
    shapes = ET.SubElement(diagram, "shapes")
    inputs = ET.SubElement(diagram, "inputs")
    ids = __import__("itertools").count(1)  # terminal ids unique per diagram

    lq.add_text(inputs, _OFFBOX_COL_X[0], 30, title.upper(), lq.FONT_HEADER)
    for col_x, top_y, el in placements:
        _draw_offmodule_box(elements, shapes, inputs, ids, col_x, top_y, el)
    return diagram


def _build_offmodule_boxes(project, start_order, func, element_list,
                           bus_labels=None) -> int:
    """Append the packed per-element box folio(s) for ONE function. Returns the
    count appended. Orders run start_order .."""
    folios = _pack_offmodule_boxes(element_list)
    if not folios:
        return 0
    total = len(folios)
    for n, placements in enumerate(folios, start=1):
        suffix = f" ({n}/{total})" if total > 1 else ""
        title = f"{_offmodule_section_title(func, bus_labels)} · {func}{suffix}"
        _add_offmodule_box_diagram(project, start_order + n - 1, title,
                                   placements)
    return total


def build_offmodule_section(project, start_order, groups,
                            bus_labels=None) -> tuple[int, list]:
    """Append the WHOLE off-module section to `project`: per function (in the
    Drives, Identification, Coordination/Safety order the data layer yields), a
    summary-table folio block THEN the packed per-element box folios. Orders run
    consecutively from `start_order` (a running counter), so the section never
    collides with the BOM band below it or the changelog above it.

    ``bus_labels`` (optional ``{func: bus}``) makes the section title bus-aware
    PER FUNCTION: the protocol word becomes ``bus_labels.get(func, "PROFINET")``
    (e.g. S7-300 Drives -> "PROFIBUS-DP"). When None (the E6/TIA plant caller)
    every title is the unchanged ``OFFMODULE_SECTION_TITLE`` — byte-for-byte.

    Returns ``(n_folios, layout)`` where layout is a per-function report list of
    ``{func, n_elements, n_tags, summary_orders, box_orders}`` for the stderr
    note / índice. Returns (0, []) when `groups` is empty (gated OFF — never an
    empty section). NEVER invents."""
    if not groups:
        return 0, []
    order = start_order
    n_folios = 0
    layout = []
    for func, element_list in groups:
        n_tags = sum(len(e["tags"]) for e in element_list)
        s0 = order
        ns = _build_offmodule_summary(project, order, func, element_list,
                                      bus_labels)
        order += ns
        b0 = order
        nb = _build_offmodule_boxes(project, order, func, element_list,
                                    bus_labels)
        order += nb
        n_folios += ns + nb
        layout.append({
            "func": func, "n_elements": len(element_list), "n_tags": n_tags,
            "summary_orders": list(range(s0, s0 + ns)),
            "box_orders": list(range(b0, b0 + nb)),
        })
    return n_folios, layout


# ── render_plant ─────────────────────────────────────────────────────────────
def render_plant(station_irs, out_path, *, no_symbols=False,
                 wire_scheme="address", offmodule_groups=None):
    """Render the FULL plant (ordered ``list[PlcProject]``) to ONE `.qet` with
    PER-STATION 100-BANDS. See the module docstring for the layout. Reuses the
    existing folio builders verbatim (so the single-station / Rockwell path is
    untouched). Returns 0.

    Gracefully degrades on an empty list (writes a cover-only plant document) —
    NEVER raises, NEVER invents."""
    station_irs = list(station_irs or [])

    # shared accumulators (aggregate symbols / designations / wires / BOM /
    # spares across the WHOLE plant, exactly like a single station does locally)
    symbols = [] if no_symbols else lq.load_symbol_db()
    sym_counts: dict[str, int] = {}
    designations: dict[tuple[int, str], int] = {}
    spare_counter: dict[str, int] = {}
    wire_counters: dict[int, int] = {}
    bom_rows: list[dict] = []

    # title-block fields: project_template merged over defaults; the plant uses
    # the FIRST station's name as the fallback project/machine name (real data).
    tmpl = lq.load_project_template()
    plant_name = station_irs[0].name if station_irs else ""
    tb_fields = lq.resolve_title_block_fields(tmpl, plant_name)

    project = ET.Element("project",
                         {"title": tb_fields["project"], "version": "0.80"})

    # ── per-station bands ────────────────────────────────────────────────────
    band_map: list[dict] = []   # one entry per station (for the stderr report)
    total_io = 0
    total_bornero = 0
    for i, st in enumerate(station_irs):
        base = band_base(i)
        before = list(project.findall("diagram"))
        io_n, born_n, n_pts = _build_station_bands(
            project, st, base, symbols=symbols, sym_counts=sym_counts,
            designations=designations, wire_scheme=wire_scheme,
            wire_counters=wire_counters, bom_rows=bom_rows,
            spare_counter=spare_counter)
        after = list(project.findall("diagram"))
        new_diagrams = after[len(before):]
        functional, derived = _station_functional_name(st)
        label = station_section_label(st, functional)
        _prefix_titles(new_diagrams, label)
        total_io += io_n
        total_bornero += born_n
        band_map.append({
            "station": st.name, "base": base,
            "functional": functional, "derived": derived,
            "cpu": (st.controller_cpu or ""),
            "io": io_n, "bornero": born_n, "points": n_pts,
            "label": label,
        })
        # one-line stderr note so the user can eyeball derived-vs-real names
        kind = "derived" if derived else "real"
        fn = functional or "(none)"
        print(f"station    : {st.name} — functional {fn!r} ({kind}), "
              f"band {base} (I/O {io_n}, bornero {born_n})", file=sys.stderr)

    # ── shared front matter (built ONCE) ─────────────────────────────────────
    used = [e for e in symbols if e["id"] in sym_counts]
    # plant cover: a 9-row station table
    station_rows = []
    for m in band_map:
        b = m["base"]
        last_io = b + BAND_IO_OFFSET + max(m["io"] - 1, 0)
        band_txt = f"{b + BAND_IO_OFFSET:03d}–{last_io:03d}" if m["io"] \
            else f"{b:03d}"
        station_rows.append((m["station"], m["functional"], m["cpu"], band_txt))
    portada_folios = _build_plant_portada(project, tb_fields, station_rows)
    symbology_folios = lq.build_symbology_folio(project, PLANT_SEC_SIMBOLOGIA,
                                                used)
    # shared PROFINET network (the node list is the same on every station IR)
    network_nodes = []
    for st in station_irs:
        network_nodes = getattr(st, "network_nodes", None) or network_nodes
        if network_nodes:
            break
    network_folios = lq.build_network_folio(project, PLANT_SEC_NETWORK,
                                            network_nodes)
    # station-grouped plant rack
    station_groups = [(m["label"] or m["station"],
                       station_irs[i].io_mods)
                      for i, m in enumerate(band_map)]
    rack_folios = _build_plant_rack(project, station_groups)

    # ── shared back matter ───────────────────────────────────────────────────
    summary_folios = lq.build_summary_folios(project, PLANT_SEC_BOM, bom_rows)
    # E6 (c2): the off-module PROFINET I/O section (BETWEEN the BOM and the
    # changelog). Gated ON only when there ARE off-module groups (never an empty
    # section). Built BEFORE the índice so the índice enumerates it.
    offmodule_folios, offmodule_layout = build_offmodule_section(
        project, PLANT_SEC_OFFMODULE, offmodule_groups or [])
    revisions = lq.normalize_revisions(tmpl.get("revisions"), tb_fields)
    changelog_folios = lq.build_changelog_folios(project, PLANT_SEC_CHANGELOG,
                                                 revisions)
    # índice LAST so it enumerates every folio (all 9 bands + front/back matter)
    index_folios = lq.build_index_folio(project, PLANT_SEC_INDEX)

    # ── post-process the WHOLE project ONCE ──────────────────────────────────
    lq.add_continuation_refs(project)
    lq.reorder_diagrams_by_position(project)
    template_text = lq.sectionize_titleblock_page(lq.load_titleblock_template())
    page_total = lq.attach_titleblocks(project, tb_fields, template_text,
                                       filename=Path(out_path).stem)
    lq.build_collection(project, used)

    # BOM CSV sidecar next to the .qet
    bom_path = re.sub(r"\.qet$", "", out_path, flags=re.I) + "_bom.csv"
    lq.write_bom_csv(bom_path, bom_rows)

    pretty = minidom.parseString(ET.tostring(project, encoding="unicode")) \
        .toprettyxml(indent="    ")
    pretty = "\n".join(l for l in pretty.splitlines() if l.strip())
    if template_text:
        pretty = lq.embed_titleblock_templates(pretty, template_text)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(pretty + "\n")

    # ── stderr summary ───────────────────────────────────────────────────────
    err = sys.stderr
    print(f"plant      : {len(station_irs)} station(s)", file=err)
    print(f"folios     : {total_io} I/O + {total_bornero} bornero over "
          f"{len(station_irs)} band(s)", file=err)
    n_spare = sum(spare_counter.values())
    print(f"spare      : {n_spare} reserve terminal(s) (RESERVA)", file=err)
    print(f"bom        : {len(bom_rows)} rows over {summary_folios} "
          f"summary folio(s)", file=err)
    if offmodule_folios:
        n_off = sum(r["n_tags"] for r in offmodule_layout)
        print(f"off-module : {offmodule_folios} folio(s) (orders "
              f"{PLANT_SEC_OFFMODULE}..), {n_off} tag(s) over "
              f"{len(offmodule_layout)} function(s)", file=err)
        for r in offmodule_layout:
            print(f"  · {r['func']:<20} {r['n_elements']:>2} elem / "
                  f"{r['n_tags']:>3} tag — summary {r['summary_orders']}, "
                  f"boxes {r['box_orders']}", file=err)
    if network_folios:
        print(f"red PN     : {network_folios} '{lq.PROFINET_TITLE}' folio(s) "
              f"(order {PLANT_SEC_NETWORK}), {len(network_nodes)} node(s)",
              file=err)
    if rack_folios:
        print(f"rack       : {rack_folios} '{lq.RACK_TITLE}' folio(s) "
              f"(order {PLANT_SEC_RACK}), grouped by station", file=err)
    print(f"portada    : {portada_folios} cover folio(s), "
          f"{len(station_rows)} station(s) tabled", file=err)
    print(f"simbología : {symbology_folios} legend folio(s), "
          f"{len(used)} symbol type(s)", file=err)
    print(f"changelog  : {changelog_folios} folio(s)", file=err)
    if index_folios:
        print(f"índice     : {index_folios} '{lq.INDEX_TITLE}' folio(s) "
              f"(order {PLANT_SEC_INDEX})", file=err)
    if page_total:
        print(f"title block: ISO 7200 ({lq.TITLEBLOCK_NAME}), "
              f"{page_total} folio(s)", file=err)
    # band map (one line per station — the per-station band occupancy)
    for m in band_map:
        b = m["base"]
        print(f"band {b:>3} : {m['station']} — I/O {b + BAND_IO_OFFSET}.."
              f"{b + BAND_IO_OFFSET + max(m['io'] - 1, 0)} "
              f"({m['io']}), bornero {b + BAND_BORNERO_OFFSET}.."
              f"{b + BAND_BORNERO_OFFSET + max(m['bornero'] - 1, 0)} "
              f"({m['bornero']})", file=err)
    print(f"output     : {out_path}", file=err)
    print(f"bom csv    : {bom_path}", file=err)
    return 0
