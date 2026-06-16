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
from pathlib import Path

import plc_ir
import logix_to_qet


def _discover_tags(io_channels_path: str) -> str | None:
    """Auto-discover a sibling PLCTags*.xlsx next to the IO_Channels.xml.

    Prefers an S7-1200 table (`*S71200*`) when several are present, else the
    first PLCTags*.xlsx in the same directory. Returns None when none exists —
    descriptions then stay "" (NEVER invent). Deterministic (sorted).
    """
    folder = Path(io_channels_path).resolve().parent
    candidates = sorted(folder.glob("PLCTags*.xlsx"))
    if not candidates:
        return None
    preferred = [p for p in candidates if "S71200" in p.name]
    chosen = preferred[0] if preferred else candidates[0]
    return str(chosen)


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
    ap.add_argument("--no-symbols", action="store_true",
                    help="skip field-device symbol matching (terminals only)")
    ap.add_argument("--wire-scheme", choices=("address", "sequential"),
                    default="address",
                    help="field-conductor wire numbering: 'address' uses the "
                         "Siemens address verbatim (default); 'sequential' uses "
                         "per-folio W<page>.<n>")
    args = ap.parse_args(argv)

    tags_path = args.tags or _discover_tags(args.io_channels)

    # Build the vendor-neutral PlcProject IR via the Siemens front end, then hand
    # it to the SAME renderer logix_to_qet uses — only emit_vendor_folios differs.
    project_ir = plc_ir.build_tia_project(args.io_channels, tags_path)

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
