#!/usr/bin/env python3
"""s7300_front_end.py — Siemens S7-300 (STEP 7 Classic) front-end (IR core).

Mirrors :mod:`tia_front_end` but reads the S7-300 ``.cfg`` hardware-config and
(optionally) the ``.asc`` global symbol table that chunk-1 parsers
(:mod:`s7300_cfg`, :mod:`s7300_asc`) already turned into faithful data. It
produces the SAME vendor-neutral elements (``Module`` / ``IoPoint`` from
``logix_to_eplan_csv``) grouped into one ``PlcProject`` per station/drop, so the
renderer needs NO vendor branches. ``plc_ir.build_s7300_project`` is the thin
seam that wraps this (exactly like ``build_tia_distributed_project`` wraps
``tia_front_end.build_distributed_stations``).

STATION DECOMPOSITION (one project per "station/drop", mirrors the E6 per-drop
model):
  * the LOCAL RACK (the CPU 315-2 station) holding the local DI32/DO32/AI8
    modules — first in the ordered list;
  * EACH PROFIBUS-DP drop that has wired I/O — the 5× ``ET 200eco 16DI`` and the
    ``Festo CPX-Terminal`` — as its OWN project, ordered by DP address.
Every project: ``source_vendor="siemens"``, ``controller_cpu`` = the real CPU
type from the cfg (read, not hardcoded).

CHANNEL SOURCES (never invented):
  * DIGITAL channels come from the inline ``.cfg`` SYMBOL lines (STEP 7
    pre-joined tag↔channel). Capacity = the module's point count; for each
    channel ``ch`` (0-based bit offset): a placeholder/Spare symbol
    (``looks_like_spare``) → RESERVA appended to ``skipped``; otherwise an
    ``IoPoint`` with ``description=comment`` ("" when blank). The real address is
    ``%I{start+ch//8}.{ch%8}`` (``%Q`` for outputs), where ``start`` is the
    module/sub-slot start byte.
  * The local AI8 has NO inline ``.cfg`` symbols — its channels join via the
    ``.asc`` PIW rows by WORD address (start byte 352, 8 words at 352,354,…366).
    A PIW row whose numeric word address matches a channel word → mapped analog
    ``IoPoint`` (``logix_address`` = the row's RAW addr, e.g. ``%PIW372`` if the
    row carried one, else ``%IW{word}`` synthesised from the real channel word);
    an unmatched channel → RESERVA. NEVER invent a tag/desc.

NOT WIRED CHANNEL MODULES (never synthesised as DI/DO/AI, never silently
dropped): the CMMP-AS M3 servo drives (telegram ranges, no channel symbols) and
the Keyence PROFINET cameras. Their identity + real address ranges are exposed
via :func:`offmodule_devices` so a later chunk can render an off-module section.

network_nodes is left EMPTY here (the PROFINET/PROFIBUS topology folio is a later
render decision); an empty list makes the renderer omit it gracefully.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import s7300_asc as A
import s7300_cfg as C
from logix_to_eplan_csv import IoPoint, Module


# --------------------------------------------------------------------------
# Address computation (the only "math" — pure + tested)
# --------------------------------------------------------------------------
def digital_address(area: str, start_byte: int, ch: int) -> str:
    """Real Siemens digital address for channel ``ch`` of a module/sub-slot whose
    first byte is ``start_byte``: ``%I{start+ch//8}.{ch%8}`` for inputs,
    ``%Q…`` for outputs. ``area`` is 'I' (input) or 'O'/'Q' (output)."""
    letter = "I" if area == "I" else "Q"
    return f"%{letter}{start_byte + ch // 8}.{ch % 8}"


def analog_word(start_byte: int, channel: int) -> int:
    """The WORD address of analog channel ``channel`` (0-based) of a module whose
    first byte is ``start_byte``. Each channel is one 16-bit word == 2 bytes, so
    word = start + 2*channel."""
    return start_byte + 2 * channel


def _kind_direction(kind: str) -> str:
    """'I' for input kinds (DI/AI), 'O' for output kinds (DO/AO)."""
    return "I" if kind in ("DI", "AI") else "O"


# --------------------------------------------------------------------------
# Module construction from a set of inline .cfg channel symbols
# --------------------------------------------------------------------------
def _make_digital_module(name: str, parent: str, kind: str, capacity: int,
                         start_byte: int, slot: Optional[int],
                         catalog: str) -> Module:
    """Build a single-direction digital ``Module`` and set the matching byte base.

    ``kind`` is 'DI' or 'DO'; ``start_byte`` becomes ``in_byte_base`` (DI) or
    ``out_byte_base`` (DO). ``points`` = capacity (the physical channel count)."""
    mod = Module(
        name=name,
        catalog=catalog,
        parent=parent,
        slot=slot,
        kind=kind,
        points=capacity,
        rack=0,
    )
    if kind == "DI":
        mod.in_byte_base = start_byte
    else:
        mod.out_byte_base = start_byte
    return mod


def _emit_digital_channels(mod: Module, symbols: List["C.Symbol"], capacity: int,
                           start_byte: int, kind: str, scope: str,
                           points: List[IoPoint], skipped: List[Tuple]) -> None:
    """Walk a digital module's inline symbols (one per wired channel) and append
    mapped channels to ``points`` and spare channels to ``skipped`` as RESERVA.

    The inline symbols carry their own 0-based ``ch`` (bit offset within the
    module); we honour it so the real address is exact even if the file ever
    omits a channel. NEVER invent a tag/description: a blank comment -> "".
    """
    direction = "I" if kind == "DI" else "O"
    area = "I" if kind == "DI" else "O"
    for sym in symbols:
        ch = sym.ch
        # Clamp to the declared module capacity: a channel index at/above the
        # point count cannot be a physical channel, so never emit it (mirrors
        # the analog path's `for ch in range(capacity)`). Guards against a
        # module carrying more inline SYMBOL lines than its declared points.
        if ch >= capacity:
            continue
        raw = digital_address(area, start_byte, ch)
        if sym.looks_like_spare:
            skipped.append(("RESERVA", raw, "spare"))
            continue
        points.append(
            IoPoint(
                tag=sym.name,
                module=mod,
                direction=direction,
                index=ch,
                analog=False,
                radix="",
                description=sym.comment or "",  # NEVER invent
                logix_address=raw,
                scope=scope,
            )
        )


# --------------------------------------------------------------------------
# AI8: join channels to .asc PIW rows by word address (no inline symbols)
# --------------------------------------------------------------------------
def _index_piw_by_word(asc_symbols: List["A.AscSymbol"]) -> dict:
    """Index the ``.asc`` PIW rows by their numeric WORD address.

    Returns ``{word:int -> AscSymbol}``. Only PIW rows with a clean integer
    address participate (a non-numeric address can't match a channel word). On a
    duplicate word the FIRST row wins (deterministic); never invented."""
    index: dict = {}
    for s in asc_symbols or []:
        if s.area != "PIW":
            continue
        if not s.addr.isdigit():
            continue
        w = int(s.addr)
        if w not in index:
            index[w] = s
    return index


def _emit_analog_channels(mod: Module, capacity: int, start_byte: int,
                          piw_index: dict, scope: str,
                          points: List[IoPoint], skipped: List[Tuple]) -> None:
    """Emit the AI8's channels by joining each channel's WORD address to the
    ``.asc`` PIW rows. A matched channel -> mapped analog ``IoPoint``; an
    unmatched channel -> RESERVA. NEVER invents an address or tag.

    ``logix_address`` uses the matched row's RAW addr verbatim when it carries an
    area prefix; the chunk-1 parser strips the ``PIW`` area off into ``area`` and
    leaves ``addr`` as the bare number, so we reconstruct ``%PIW{word}`` from the
    real word (the area is real, the number is real — nothing invented)."""
    for ch in range(capacity):
        word = analog_word(start_byte, ch)
        row = piw_index.get(word)
        if row is None:
            skipped.append(("RESERVA", f"%IW{word}", "spare"))
            continue
        raw = f"%{row.area}{row.addr}"  # real area + real number, e.g. %PIW372
        points.append(
            IoPoint(
                tag=row.name,
                module=mod,
                direction="I",
                index=ch,
                analog=True,
                radix="",
                description=row.comment or "",  # NEVER invent
                logix_address=raw,
                scope=scope,
            )
        )


# --------------------------------------------------------------------------
# Per-station builders -> dicts mirroring tia build_distributed_stations
# --------------------------------------------------------------------------
def _local_station(cfg: "C.CfgData", asc_symbols: List["A.AscSymbol"],
                   controller_cpu: Optional[str]) -> dict:
    """Build the LOCAL RACK station dict (CPU 315-2 + local DI/DO/AI modules)."""
    station_name = cfg.station.id if cfg.station else "S7300"
    modules: dict = {}
    io_mods: List[Module] = []
    points: List[IoPoint] = []
    skipped: List[Tuple] = []
    piw_index = _index_piw_by_word(asc_symbols)

    for m in cfg.modules:
        if m.kind in ("DI", "DO"):
            # Guard the address block like the analog branch: a DI/DO module
            # with no address block must not crash (start defaults to 0).
            if m.kind == "DI":
                start = m.in_addr.start_byte if m.in_addr else 0
            else:
                start = m.out_addr.start_byte if m.out_addr else 0
            mod = _make_digital_module(
                name=f"Slot{m.slot} {m.kind}", parent=station_name,
                kind=m.kind, capacity=m.points or 0, start_byte=start,
                slot=m.slot, catalog=m.order_no)
            modules[mod.name] = mod
            io_mods.append(mod)
            _emit_digital_channels(mod, m.symbols, m.points or 0, start,
                                   m.kind, station_name, points, skipped)
        elif m.kind == "AI":
            start = m.in_addr.start_byte if m.in_addr else 0
            mod = Module(
                name=f"Slot{m.slot} AI", catalog=m.order_no,
                parent=station_name, slot=m.slot, kind="AI",
                points=m.points or 0, rack=0)
            mod.an_in_word_base = start
            modules[mod.name] = mod
            io_mods.append(mod)
            _emit_analog_channels(mod, m.points or 0, start, piw_index,
                                  station_name, points, skipped)
        # power/cpu/comms/other -> not wired channel modules; not drawn here.

    return {
        "station_name": station_name,
        "controller_cpu": controller_cpu,
        "modules": modules,
        "io_mods": io_mods,
        "points": points,
        "skipped": skipped,
    }


def _et200_capacity(subslot: "C.DpSubslot") -> int:
    """Channel capacity of an ET 200eco wired sub-slot: the digit count in its
    type string ('16DE' -> 16). Falls back to the symbol count when the type
    carries no count (never invents a larger capacity)."""
    import re
    m = re.search(r"(\d+)", subslot.type_str or "")
    if m:
        return int(m.group(1))
    return subslot.symbol_count


def _dp_station(slave: "C.DpSlave", controller_cpu: Optional[str]) -> Optional[dict]:
    """Build ONE station dict for a wired PROFIBUS-DP drop (ET200eco or Festo
    CPX). Returns None for a drop with no wired channel symbols (servos)."""
    # find the wired sub-slots (those carrying inline channel symbols)
    wired = [ss for ss in slave.subslots if ss.symbol_count > 0]
    if not wired:
        return None

    station_name = f"DP{slave.dp_address} {slave.type_str}"
    modules: dict = {}
    io_mods: List[Module] = []
    points: List[IoPoint] = []
    skipped: List[Tuple] = []

    for ss in wired:
        # direction is decided by which address block carries the symbols
        if ss.out_addr is not None and ss.out_addr.symbols:
            kind = "DO"
            start = ss.out_addr.start_byte
            syms = ss.out_addr.symbols
        else:
            kind = "DI"
            start = ss.in_addr.start_byte if ss.in_addr else 0
            syms = ss.in_addr.symbols if ss.in_addr else []
        # Always derive capacity generically from the sub-slot type ('16DE'->16,
        # '32DE'->32), falling back to the symbol count when the type carries no
        # count. No literal-string gate, so a sibling ET 200eco 32DI drop counts
        # 32 capacity (extendable), not just its symbol count.
        capacity = _et200_capacity(ss)
        name = f"DP{slave.dp_address} Slot{ss.slot} {kind}"
        mod = _make_digital_module(
            name=name, parent=station_name, kind=kind, capacity=capacity,
            start_byte=start, slot=ss.slot, catalog=slave.gsd)
        modules[mod.name] = mod
        io_mods.append(mod)
        _emit_digital_channels(mod, syms, capacity, start, kind,
                               station_name, points, skipped)

    return {
        "station_name": station_name,
        "controller_cpu": controller_cpu,
        "modules": modules,
        "io_mods": io_mods,
        "points": points,
        "skipped": skipped,
    }


def controller_cpu_type(cfg: "C.CfgData") -> Optional[str]:
    """The real CPU type string from the local rack (e.g. 'CPU 315-2 PN/DP'),
    or None when no CPU module is present. NEVER invented."""
    for m in cfg.modules:
        if m.kind == "cpu":
            return m.type_str or None
    return None


def build_stations(cfg: "C.CfgData",
                   asc_symbols: Optional[List["A.AscSymbol"]] = None) -> List[dict]:
    """Build the ORDERED list of station dicts (local rack first, then wired DP
    drops by ascending DP address). Each dict carries station_name,
    controller_cpu, modules, io_mods, points, skipped (the PlcProject shape minus
    the wrapper). PURE over the parsed inputs; NEVER invents."""
    cpu = controller_cpu_type(cfg)
    stations: List[dict] = [_local_station(cfg, asc_symbols or [], cpu)]
    for slave in sorted(cfg.dp_slaves, key=lambda s: s.dp_address):
        st = _dp_station(slave, cpu)
        if st is not None:
            stations.append(st)
    return stations


# --------------------------------------------------------------------------
# Off-module devices (servos + cameras) — exposed, never drawn as channels
# --------------------------------------------------------------------------
def offmodule_devices(cfg: "C.CfgData") -> List[dict]:
    """Expose the NON-channel devices (servo drives + PROFINET cameras) with their
    real identity + address ranges, so a later chunk can render an off-module
    section. These never appear as DI/DO/AI channels and are never silently
    dropped.

    Returns a list of dicts (PROFIBUS servos by DP address, then PROFINET cameras
    by IO address), each:
        {"bus", "kind", "address", "type", "gsd"|"gsdml", "ranges": [...]}
    where ``ranges`` is a list of ``(direction, start_byte, length_bytes)`` from
    the device's wired/telegram address blocks. NEVER invents — every value is
    real parser data."""
    out: List[dict] = []

    # PROFIBUS-DP servo drives (CMMP-AS M3): telegram ranges, no channel symbols.
    for slave in sorted(cfg.dp_slaves, key=lambda s: s.dp_address):
        if any(ss.symbol_count > 0 for ss in slave.subslots):
            continue  # this drop has wired channels -> it is a real I/O station
        ranges: List[Tuple] = []
        for ss in slave.subslots:
            if ss.in_addr is not None:
                ranges.append(("in", ss.in_addr.start_byte,
                               ss.in_addr.length_bytes))
            if ss.out_addr is not None:
                ranges.append(("out", ss.out_addr.start_byte,
                               ss.out_addr.length_bytes))
        out.append({
            "bus": "PROFIBUS-DP",
            "kind": "servo",
            "address": slave.dp_address,
            "type": slave.type_str,
            "gsd": slave.gsd,
            "ranges": ranges,
        })

    # PROFINET-IO devices (Keyence cameras). Group sub-slots by io_address into
    # one device entry each, collecting every wired range.
    cams: dict = {}
    cam_order: List[int] = []
    for dev in cfg.io_devices:
        ioaddr = dev.io_address
        if ioaddr not in cams:
            # the device HEAD record (slot is None) carries the device name
            cams[ioaddr] = {
                "bus": "PROFINET-IO",
                "kind": "camera",
                "address": ioaddr,
                "type": dev.name,
                "gsdml": dev.gsdml,
                "ranges": [],
            }
            cam_order.append(ioaddr)
        # prefer the head-record name (slot is None) as the device identity
        if dev.slot is None and dev.name:
            cams[ioaddr]["type"] = dev.name
            cams[ioaddr]["gsdml"] = dev.gsdml
        if dev.in_addr is not None:
            cams[ioaddr]["ranges"].append(
                ("in", dev.in_addr.start_byte, dev.in_addr.length_bytes))
        if dev.out_addr is not None:
            cams[ioaddr]["ranges"].append(
                ("out", dev.out_addr.start_byte, dev.out_addr.length_bytes))
    for ioaddr in cam_order:
        out.append(cams[ioaddr])

    return out
