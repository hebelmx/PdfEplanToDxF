#!/usr/bin/env python3
"""tia_to_qet.py — Siemens TIA Portal → QElectroTech command.

The Siemens sibling of logix_to_qet. Per Abel's decision this is a SEPARATE
command (not a --vendor flag on logix_to_qet): the two front-ends read totally
different exports and the seam between them is the vendor-neutral PlcProject IR.

Pipeline:
    IO_Channels.xml [+ sibling PLCTags*.xlsx]
        -> plc_ir.build_tia_project(...)          (PlcProject, vendor="siemens")
        -> logix_to_qet.render_project(..., emit_vendor_folios=False)

Folio scope (DECIDED — never-invent): the Siemens set renders cover (portada),
símbología, the per-card I/O folios, bornero, BOM (summary), changelog and the
ISO 7200 title block. It GRACEFULLY OMITS topología / supply 'Alimentación' /
chassis grounding — those classify ControlNet/EtherNet comms, read AB-1756
grounding gauges and group power off the Rockwell module_db, none of which exist
for a Siemens panel. The omission is done via render_project's emit_vendor_folios
knob (also auto-forced off for any non-rockwell IR).

STANDARD LIBRARY ONLY.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import plc_ir
import logix_to_qet
import power_config as power_config_mod


def _station_tags(io_channels_path: str) -> set[str]:
    """Read the set of non-empty <Tag> values from THIS station's IO_Channels.xml.

    These are the tag names the chosen PLCTags*.xlsx must cover (the join is
    Tag == xlsx.Name). Blank/whitespace tags (spares) are excluded. Returns an
    empty set on any parse problem — never invents."""
    try:
        root = ET.parse(io_channels_path).getroot()
    except (OSError, ET.ParseError):
        return set()
    tags: set[str] = set()
    for tag_el in root.iter("Tag"):
        t = (tag_el.text or "").strip()
        if t:
            tags.add(t)
    return tags


def _discover_tags(io_channels_path: str) -> str | None:
    """Auto-discover the sibling PLCTags*.xlsx that best covers THIS station.

    Domain rule: tag names are unique within a PLC, so the correct tag table is
    the one whose `Name` column covers the station's I/O `<Tag>` values. We
    gather ALL sibling `PLCTags*.xlsx`, read the station's non-empty tags from
    the IO_Channels.xml, and pick the candidate with the MOST Name∩Tag matches
    (tie-break alphabetically by filename for determinism). Returns None only
    when there are no candidates — descriptions then stay "" (NEVER invent).

    A one-line stderr note records the choice and its coverage so the selection
    is auditable, e.g.:  ``tags : selected PLCTagsS71500.xlsx (47/48 tags matched)``
    """
    import tia_front_end as tia

    folder = Path(io_channels_path).resolve().parent
    candidates = sorted(folder.glob("PLCTags*.xlsx"))
    if not candidates:
        return None

    station_tags = _station_tags(io_channels_path)
    n_station = len(station_tags)

    best = None  # (matches, name) of the winner; candidates already alphabetical
    best_matches = -1
    for cand in candidates:
        names = set(tia.load_tag_table(str(cand)).keys())
        matches = len(names & station_tags)
        if matches > best_matches:   # strict '>' keeps the alphabetically-first tie
            best_matches = matches
            best = cand

    print(f"tags : selected {best.name} "
          f"({best_matches}/{n_station} tags matched)", file=sys.stderr)
    return str(best)


def _discover_aml(io_channels_path: str) -> str | None:
    """Auto-discover a sibling CAx/AML hardware export (`*.aml`) next to the
    IO_Channels.xml. Returns None when none exists — catalog/network_address then
    stay blank/None (NEVER invent). Deterministic (sorted; first .aml)."""
    folder = Path(io_channels_path).resolve().parent
    candidates = sorted(folder.glob("*.aml"))
    return str(candidates[0]) if candidates else None


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Convert a Siemens TIA Portal IO_Channels.xml export to a "
                    "QElectroTech project.")
    ap.add_argument("io_channels", help="path to the <project>_IO_Channels.xml")
    ap.add_argument("-o", "--output",
                    help="output .qet path (default: <input-dir>/<station>.qet)")
    ap.add_argument("--tags",
                    help="path to a PLCTags*.xlsx tag table (overrides the "
                         "sibling auto-discovery; absent => descriptions stay \"\")")
    ap.add_argument("--aml",
                    help="path to a CAx/AML hardware export (<project>.aml) for "
                         "module order numbers + PROFINET addresses (overrides "
                         "the sibling *.aml auto-discovery; absent => catalog/"
                         "network_address stay blank)")
    ap.add_argument("--power-config",
                    help="path to a power one-line JSON config (system voltage, "
                         "input/output breakers, power supply, optional "
                         "transformer/ups); absent => no 'Alimentación' folio "
                         "(never invented). See docs/examples/power_config.example.json")
    ap.add_argument("--no-symbols", action="store_true",
                    help="skip field-device symbol matching (terminals only)")
    ap.add_argument("--wire-scheme", choices=("address", "sequential"),
                    default="address",
                    help="field-conductor wire numbering: 'address' uses the "
                         "Siemens address verbatim (default); 'sequential' uses "
                         "per-folio W<page>.<n>")
    args = ap.parse_args(argv)

    tags_path = args.tags or _discover_tags(args.io_channels)
    aml_path = args.aml or _discover_aml(args.io_channels)
    power_cfg = power_config_mod.load_power_config(args.power_config)

    # Build the vendor-neutral PlcProject IR via the Siemens front end, then hand
    # it to the SAME renderer logix_to_qet uses — only emit_vendor_folios differs.
    project_ir = plc_ir.build_tia_project(args.io_channels, tags_path, aml_path)

    # default output: <input-dir>/<station>.qet (station name from the IR; fall
    # back to the input file stem when the station name is empty/unsafe).
    if args.output:
        out_path = args.output
    else:
        folder = Path(args.io_channels).resolve().parent
        stem = _safe_stem(project_ir.name) or Path(args.io_channels).stem
        out_path = str(folder / f"{stem}.qet")

    return logix_to_qet.render_project(
        project_ir, out_path,
        no_symbols=args.no_symbols,
        wire_scheme=args.wire_scheme,
        emit_vendor_folios=False,
        power_config=power_cfg,
    )


def _safe_stem(name: str) -> str:
    """Make a station name safe as a filename stem (TIA stations can carry '/',
    e.g. 'Q100-Cooling1/UV'). Replaces path separators with '_'; never invents."""
    stem = (name or "").strip()
    for bad in ("/", "\\", ":", "*", "?", '"', "<", ">", "|"):
        stem = stem.replace(bad, "_")
    return stem.strip("_ ")


if __name__ == "__main__":
    sys.exit(main())
