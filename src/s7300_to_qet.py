#!/usr/bin/env python3
"""s7300_to_qet.py — Siemens S7-300 (STEP 7 Classic) → QElectroTech command.

The S7-300 sibling of ``tia_to_qet`` / ``logix_to_qet``. Per Abel's LOCKED
design this is a SEPARATE command (its own front-end reads totally different
exports — a STEP 7 Classic ``.cfg`` hardware-config + optional ``.asc`` symbol
table — and the seam between vendors is the vendor-neutral PlcProject IR).

Pipeline:
    <project>.cfg [+ sibling <project>.asc]
        -> plc_ir.build_s7300_single_project(...)   (ONE PlcProject, "siemens")
        -> logix_to_qet.render_project(..., emit_vendor_folios=False)

SINGLE-STATION design: the S7-300 plant (one CPU 315-2 local rack + 5 ET200eco
DP drops + 1 Festo CPX drop) is MERGED into ONE PlcProject and rendered as a
single station — its I/O-card folios are the local modules (DI32×2, DO32×2, AI8)
PLUS every DP-drop module, in one sequence, with ONE bornero and ONE BOM.

Folio scope (CORE only for this chunk): cover (portada), símbología, índice,
rack, the per-card I/O folios (all 256 channels incl. RESERVA), bornero, BOM
(summary) and changelog, with the ISO 7200 title block. It GRACEFULLY OMITS the
off-module section (servos + cameras) and the network/topology folio — those are
later chunks; ``network_nodes`` stays empty so the NET folio is omitted.

STANDARD LIBRARY ONLY.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import plc_ir
import logix_to_qet


def _discover_asc(cfg_path: str) -> str | None:
    """Auto-discover the sibling ``.asc`` global symbol table for THIS ``.cfg``.

    The S7-300 fixture pairs ``brpl2twin.txt.cfg`` with ``brpl2twin.txt.asc``, so
    the rule is: take the ``.cfg`` path and swap its trailing ``.cfg`` for
    ``.asc``. Returns that path only when the file actually exists — absent => the
    AI8 has no PIW rows to join and stays all-RESERVA (NEVER invent). Returns None
    when there is no sibling ``.asc``."""
    p = Path(cfg_path)
    if p.suffix.lower() == ".cfg":
        cand = p.with_suffix(".asc")
        if cand.exists():
            return str(cand)
    return None


def _safe_stem(name: str) -> str:
    """Make a station name safe as a filename stem (replace path separators);
    never invents."""
    stem = (name or "").strip()
    for bad in ("/", "\\", ":", "*", "?", '"', "<", ">", "|"):
        stem = stem.replace(bad, "_")
    return stem.strip("_ ")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Convert a Siemens S7-300 (STEP 7 Classic) hardware-config "
                    "(.cfg [+ sibling .asc]) to a QElectroTech project.")
    ap.add_argument("cfg", help="path to the STEP 7 Classic <project>.cfg")
    ap.add_argument("-o", "--output",
                    help="output .qet path (default: <cfg-dir>/<station>.qet)")
    ap.add_argument("--asc",
                    help="path to the .asc global symbol table (overrides the "
                         "sibling auto-discovery; absent => the AI8 stays "
                         "all-RESERVA, never invented)")
    ap.add_argument("--no-symbols", action="store_true",
                    help="skip field-device symbol matching (terminals only)")
    ap.add_argument("--wire-scheme", choices=("address", "sequential"),
                    default="address",
                    help="field-conductor wire numbering: 'address' uses the "
                         "Siemens address verbatim (default); 'sequential' uses "
                         "per-folio W<page>.<n>")
    args = ap.parse_args(argv)

    asc_path = args.asc or _discover_asc(args.cfg)

    # Build the MERGED single-station IR and hand it to the SAME renderer
    # logix_to_qet uses — for a Siemens IR render_project auto-forces
    # emit_vendor_folios off and renders cover/símbología/índice/rack/I/O/
    # bornero/BOM/changelog; the NET folio renders ONLY when network_nodes is
    # non-empty (kept empty here so it is omitted gracefully).
    project_ir = plc_ir.build_s7300_single_project(args.cfg, asc_path)

    # default output: <cfg-dir>/<station>.qet (station name from the IR; fall
    # back to the cfg file stem when the station name is empty/unsafe).
    if args.output:
        out_path = args.output
    else:
        folder = Path(args.cfg).resolve().parent
        stem = _safe_stem(project_ir.name) or Path(args.cfg).stem
        out_path = str(folder / f"{stem}.qet")

    return logix_to_qet.render_project(
        project_ir, out_path,
        no_symbols=args.no_symbols,
        wire_scheme=args.wire_scheme,
        emit_vendor_folios=False,
        power_config=None,
    )


if __name__ == "__main__":
    sys.exit(main())
