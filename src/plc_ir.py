#!/usr/bin/env python3
"""
plc_ir.py — vendor-neutral PLC intermediate representation (IR).

The renderer (logix_to_qet) consumes a `PlcProject` instead of the raw
Rockwell tuple. A `PlcProject` is the single seam every front-end produces:
the Rockwell builder below wraps the existing L5X parser; a future Siemens
builder (TIA / S7-300 HW-config) will mirror `build_rockwell_project` and
emit the SAME `PlcProject` shape, so the renderer needs no vendor branches.

Design notes / decisions:
  * New module (not a section of logix_to_eplan_csv): the IR is the boundary
    BETWEEN vendor front-ends and the renderer, so it must not live inside any
    one vendor's front-end. The only added cost is one import.
  * `Module` and `IoPoint` are already vendor-neutral in their field names
    (rack/slot/kind/points, tag/direction/index/analog/description...), so the
    IR REUSES them verbatim as its element types — no rename, no churn.
  * `IoPoint.logix_address` is the one Rockwell-flavoured field name. It is kept
    as-is for byte-identical output and test back-compat rather than hard-renamed
    (which would churn the CSV path, the renderer and many tests). A neutral
    accessor can be added on the IR when a second vendor actually populates it;
    until then, adding one would be speculative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlcProject:
    """Vendor-neutral PLC project IR consumed by the renderer.

    Fields:
      name            controller / project name (was the tuple's `controller`)
      source_vendor   "rockwell" | "siemens" | ... — provenance, never invented
      modules         dict[name -> Module]: the full hardware tree
      io_mods         rack-assigned, byte/word-addressed I/O modules (ordered)
      points          list[IoPoint]: alias tags bound to physical points
      skipped         list of (tag, addr, reason) tuples not bound to a point
      controller_tags controller-scope tag dict (collectors may still need it)
      program_tags    program-scope tag dicts keyed by program name
    """

    name: str
    source_vendor: str
    modules: dict[str, Any] = field(default_factory=dict)
    io_mods: list = field(default_factory=list)
    points: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    controller_tags: dict = field(default_factory=dict)
    program_tags: dict = field(default_factory=dict)

    # convenience alias: the renderer/old tuple called this `controller`
    @property
    def controller_name(self) -> str:
        return self.name


def build_rockwell_project(path: str, include_hmi: bool = False) -> PlcProject:
    """Rockwell front-end: build a `PlcProject` from an `.L5X` export.

    Thin wrapper over logix_to_eplan_csv's load_l5x / assign_racks_and_addresses
    / collect_points. This is the seam a future Siemens builder mirrors.
    """
    import logix_to_eplan_csv as l2e

    controller, modules, ctrl_tags, program_tags = l2e.load_l5x(path)
    io_mods = l2e.assign_racks_and_addresses(modules)
    points, skipped = l2e.collect_points(
        modules, ctrl_tags, program_tags, include_hmi=include_hmi
    )
    return PlcProject(
        name=controller,
        source_vendor="rockwell",
        modules=modules,
        io_mods=io_mods,
        points=points,
        skipped=skipped,
        controller_tags=ctrl_tags,
        program_tags=program_tags,
    )


def build_tia_project(io_channels_path: str, tags_xlsx_path: str | None = None) -> PlcProject:
    """Siemens TIA front-end: build a `PlcProject` from a TIA Portal export.

    Mirrors `build_rockwell_project` but reads an `<project>_IO_Channels.xml`
    (the real absolute-address point source) and, optionally, a `PLCTags*.xlsx`
    tag table for descriptions/comments (joined on Tag == xlsx.Name). Returns
    the SAME `PlcProject` shape with `source_vendor="siemens"`. The heavy parsing
    lives in tia_front_end so this module stays the single vendor seam.
    """
    import tia_front_end as tia

    tag_table = tia.load_tag_table(tags_xlsx_path) if tags_xlsx_path else {}
    station_name, modules, io_mods, points, skipped = tia.build_modules_and_points(
        io_channels_path, tag_table
    )
    return PlcProject(
        name=station_name,
        source_vendor="siemens",
        modules=modules,
        io_mods=io_mods,
        points=points,
        skipped=skipped,
        controller_tags={},
        program_tags={},
    )
