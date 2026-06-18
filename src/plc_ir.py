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
      network_nodes   list[(ip, name, type, subnet_mask, is_controller)]
                      PROFINET subnet nodes for the network/topology folio;
                      empty unless a front-end populates it (Siemens fills it
                      from the CAx/AML — subnet_mask + is_controller are REAL
                      .aml provenance, never invented).
      controller_cpu  str|None — the CPU TYPE that owns this station (e.g.
                      "CPU 1512SP F-1 PN"), derived from the real .aml PROFINET
                      controller node whose IP matches the station's modules'
                      network_address. DATA-ONLY seam for a later by-PLC
                      labelling cycle (no rendering uses it yet). None when no
                      .aml / no matching controller / no station address — and
                      always None for Rockwell. NEVER invented.
    """

    name: str
    source_vendor: str
    modules: dict[str, Any] = field(default_factory=dict)
    io_mods: list = field(default_factory=list)
    points: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    controller_tags: dict = field(default_factory=dict)
    program_tags: dict = field(default_factory=dict)
    network_nodes: list = field(default_factory=list)
    controller_cpu: str | None = None

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


def build_tia_project(
    io_channels_path: str,
    tags_xlsx_path: str | None = None,
    aml_path: str | None = None,
) -> PlcProject:
    """Siemens TIA front-end: build a `PlcProject` from a TIA Portal export.

    Mirrors `build_rockwell_project` but reads an `<project>_IO_Channels.xml`
    (the real absolute-address point source) and, optionally, a `PLCTags*.xlsx`
    tag table for descriptions/comments (joined on Tag == xlsx.Name) and a CAx
    `<project>.aml` hardware export (joined on module name) that fills each
    `Module.catalog` (Siemens order number) and `Module.network_address`
    (PROFINET). Returns the SAME `PlcProject` shape with `source_vendor="siemens"`.
    The heavy parsing lives in tia_front_end so this module stays the single
    vendor seam. Missing optional inputs degrade gracefully — NEVER invented.
    """
    import tia_front_end as tia

    tag_table = tia.load_tag_table(tags_xlsx_path) if tags_xlsx_path else {}
    station_name, modules, io_mods, points, skipped = tia.build_modules_and_points(
        io_channels_path, tag_table, aml_path
    )
    # PROFINET subnet nodes for the network/topology folio. Populated ONLY when a
    # CAx/AML is supplied (the IO_Channels.xml carries no addresses); empty
    # otherwise so the folio is gracefully omitted — NEVER invented.
    network_nodes: list = []
    controller_cpu: str | None = None
    if aml_path:
        import tia_aml
        network_nodes = tia_aml.profinet_nodes(aml_path)
        controller_cpu = _owning_controller_cpu(network_nodes, io_mods)
    return PlcProject(
        name=station_name,
        source_vendor="siemens",
        modules=modules,
        io_mods=io_mods,
        points=points,
        skipped=skipped,
        controller_tags={},
        program_tags={},
        network_nodes=network_nodes,
        controller_cpu=controller_cpu,
    )


def build_tia_distributed_project(
    aml_path: str,
    tag_paths: list[str] | None = None,
) -> list[PlcProject]:
    """E6: build ONE PlcProject per station for the FULL plant's distributed I/O.

    The single-station `build_tia_project` reads a per-station IO_Channels.xml
    (only Q100 has one). This NEW path instead synthesizes every station's real
    channel addresses from the FULL CAx/AML (`parse_aml`) and joins them to the
    per-PLC `PLCTags*.xlsx` tag tables by address — covering all 9 stations.

    Args:
      aml_path:  the full CAx/AML export.
      tag_paths: explicit list of PLCTags*.xlsx paths; when None, sibling
                 `PLCTags*.xlsx` next to the .aml are auto-discovered (sorted).

    Returns an ORDERED `list[PlcProject]` (heaviest-PLC-first; see
    tia_front_end.build_distributed_stations), one per station, each with
    source_vendor="siemens", modules/io_mods/points/skipped filled, the shared
    plant `network_nodes`, and `controller_cpu` set to the OWNING-PLC CPU type
    (derived from the owner group's CPU-local station, so distributed drops that
    carry no CPU module still show their owning 1512SP / 1214C).

    NEVER raises on a missing/!aml or missing tag tables — returns [] (mirroring
    the existing path's graceful degradation of optional inputs). NEVER invents.
    """
    import os
    import glob as _glob

    if not aml_path or not os.path.isfile(aml_path):
        return []

    import tia_front_end as tia
    import tia_aml

    # discover sibling PLCTags*.xlsx if not given (mirror tia_to_qet spirit)
    if tag_paths is None:
        folder = os.path.dirname(os.path.abspath(aml_path))
        tag_paths = sorted(_glob.glob(os.path.join(folder, "PLCTags*.xlsx")))

    # label each table by its xlsx stem with the "PLCTags" prefix stripped, e.g.
    # "PLCTagsS71500.xlsx" -> "S71500" — the owning-PLC label surfaced in the IR.
    tag_tables: dict[str, dict] = {}
    for p in tag_paths or []:
        stem = os.path.splitext(os.path.basename(p))[0]
        label = stem[len("PLCTags"):] if stem.startswith("PLCTags") else stem
        tag_tables[label] = tia.load_tag_table(p)

    try:
        stations = tia.build_distributed_stations(aml_path, tag_tables)
    except Exception:
        # a true .aml parse error degrades to [] rather than crashing the caller
        return []

    network_nodes = tia_aml.profinet_nodes(aml_path)

    projects: list[PlcProject] = []
    for s in stations:
        # controller_cpu = the OWNING-PLC CPU type, derived data-driven from the
        # owner group's CPU-local station (so ET200SP drops that carry no CPU
        # module still show their owning 1512SP/1214C). Falls back to the
        # IP-matched controller node only if the front-end didn't surface one.
        controller_cpu = s.get("owning_cpu") or _owning_controller_cpu(
            network_nodes, s["io_mods"])
        projects.append(
            PlcProject(
                name=s["station_name"],
                source_vendor="siemens",
                modules=s["modules"],
                io_mods=s["io_mods"],
                points=s["points"],
                skipped=s["skipped"],
                controller_tags={},
                program_tags={},
                network_nodes=network_nodes,
                controller_cpu=controller_cpu,
            )
        )
    return projects


def build_s7300_project(
    cfg_path: str,
    asc_path: str | None = None,
) -> list[PlcProject]:
    """Siemens S7-300 front-end: build ONE PlcProject per station/drop.

    Thin seam over ``s7300_front_end`` (mirrors ``build_tia_distributed_project``
    over ``tia_front_end.build_distributed_stations``). Reads the STEP 7 Classic
    ``.cfg`` hardware-config (the primary channel source via inline SYMBOL lines)
    and, optionally, the ``.asc`` global symbol table (used ONLY to join the local
    AI8's analog channels via their PIW word addresses — the AI8 has no inline
    symbols). Returns an ORDERED ``list[PlcProject]``: the local rack first, then
    each wired PROFIBUS-DP drop by ascending DP address.

    Every project: ``source_vendor="siemens"``; ``controller_cpu`` = the real CPU
    type from the ``.cfg`` (e.g. "CPU 315-2 PN/DP"); ``modules``/``io_mods``/
    ``points``/``skipped`` filled (a spare channel -> RESERVA in ``skipped``, not
    a point). ``network_nodes`` is left EMPTY (the PROFIBUS/PROFINET topology
    folio is a later render decision; an empty list makes the renderer omit it).

    The CMMP-AS servo drives and Keyence PROFINET cameras are NOT wired channel
    modules: they never appear in any io_mods/points; their identity + real
    address ranges are exposed separately via
    ``s7300_front_end.offmodule_devices(parse_cfg(cfg_path))`` for a later chunk.

    NEVER raises on a missing/!cfg — returns [] (mirroring the TIA path's graceful
    degradation of optional inputs). NEVER invents.
    """
    import os

    if not cfg_path or not os.path.isfile(cfg_path):
        return []

    import s7300_cfg
    import s7300_asc
    import s7300_front_end as s7

    cfg = s7300_cfg.parse_cfg(cfg_path)
    asc_symbols = (s7300_asc.parse_asc(asc_path)
                   if asc_path and os.path.isfile(asc_path) else [])

    stations = s7.build_stations(cfg, asc_symbols)

    projects: list[PlcProject] = []
    for s in stations:
        projects.append(
            PlcProject(
                name=s["station_name"],
                source_vendor="siemens",
                modules=s["modules"],
                io_mods=s["io_mods"],
                points=s["points"],
                skipped=s["skipped"],
                controller_tags={},
                program_tags={},
                network_nodes=[],
                controller_cpu=s["controller_cpu"],
            )
        )
    return projects


def build_s7300_single_project(
    cfg_path: str,
    asc_path: str | None = None,
) -> PlcProject:
    """Merge the S7-300 multi-project list into ONE single-station PlcProject.

    Abel's LOCKED design renders the S7-300 as a SINGLE station whose I/O-card
    folios are the local rack modules PLUS every PROFIBUS-DP drop module (the
    5 ET200eco drops + the Festo CPX sub-modules), in ONE ordered sequence, with
    ONE bornero and ONE BOM. This helper calls ``build_s7300_project`` and folds
    its ordered list (local rack first, then DP drops by ascending DP address)
    into a single ``PlcProject``:

      * ``name``           = the local-rack station name ("S7300")
      * ``source_vendor``  = "siemens"
      * ``controller_cpu`` = carried from the projects (all share one CPU)
      * ``modules``        = merged dict of every project's ``modules``
      * ``io_mods``        = concatenation, order preserved (local then drops)
      * ``points``         = concatenation
      * ``skipped``        = concatenation
      * ``network_nodes``  = [] (NET folio omitted for this core chunk)
      * ``controller_tags``/``program_tags`` = {}

    Module names are unique across drops (e.g. "Slot4 DI" vs "DP4 Slot2 DI"), so
    the merge has no key collisions — that invariant is asserted. An empty build
    (no/!cfg) degrades to a degenerate empty PlcProject; NEVER crashes, NEVER
    invents. ADDITIVE seam — mirrors ``build_s7300_project``."""
    projects = build_s7300_project(cfg_path, asc_path)
    if not projects:
        return PlcProject(name="S7300", source_vendor="siemens")

    name = projects[0].name
    controller_cpu = projects[0].controller_cpu

    merged_modules: dict = {}
    io_mods: list = []
    points: list = []
    skipped: list = []
    for p in projects:
        for key, mod in p.modules.items():
            assert key not in merged_modules, (
                f"S7-300 single-station merge: module key collision on {key!r}")
            merged_modules[key] = mod
        io_mods.extend(p.io_mods)
        points.extend(p.points)
        skipped.extend(p.skipped)

    return PlcProject(
        name=name,
        source_vendor="siemens",
        modules=merged_modules,
        io_mods=io_mods,
        points=points,
        skipped=skipped,
        controller_tags={},
        program_tags={},
        network_nodes=[],
        controller_cpu=controller_cpu,
    )


def _owning_controller_cpu(network_nodes: list, io_mods: list) -> str | None:
    """Identify the CPU TYPE that owns this station from REAL .aml data.

    The station's owning CPU is the PROFINET node that IS a controller
    (`is_controller==True`) AND whose IP equals the station IP — the
    `network_address` shared by the station's I/O modules. Returns that node's
    `type` (the CPU type string, e.g. "CPU 1512SP F-1 PN"), or None when there
    is no station address, or no controller node matches it — NEVER invented.

    network_nodes tuples are (ip, name, type, subnet_mask, is_controller).
    """
    # the station IP is the network_address the station's modules carry (filled
    # from the .aml by the front-end). Take the first non-empty one.
    station_ip = None
    for mod in io_mods:
        addr = getattr(mod, "network_address", None)
        if addr:
            station_ip = addr
            break
    if not station_ip:
        return None
    for ip, _name, type_name, _mask, is_controller in network_nodes:
        if is_controller and ip == station_ip:
            return type_name or None
    return None
