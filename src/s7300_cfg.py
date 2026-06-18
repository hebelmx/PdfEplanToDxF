#!/usr/bin/env python3
"""Faithful parser for the Siemens S7-300 (STEP 7 Classic) hardware-config
export (the ``.cfg`` text file, e.g. ``brpl2twin.txt.cfg``, ``FILEVERSION "3.2"``).

SCOPE / PHILOSOPHY
------------------
*Data-only*: this records exactly what the file says into plain Python
structures. It performs NO classification (NO RESERVA decision, NO IR), NO
invention. Hard rules honoured here:

  * Masked ``?`` digits in order numbers are kept VERBATIM
    (e.g. ``6ES7 390-1???0-0AA0`` is never filled in).
  * The real subnet mask ``FFFFFF00`` is captured as found; a ``/24`` is never
    synthesized.
  * A SYMBOL whose name is a bare placeholder address ("I0.4", "I38.1", "Q11.3")
    or whose comment is "Spare" is PARSE-PRESERVED as-is. We expose the raw
    name+comment and a cheap ``looks_like_spare`` hint, but do NOT drop or
    relabel it -- the front-end later decides RESERVA.
  * Missing field -> ``None`` / ``""``; never a fabricated value.

FILE STRUCTURE (verified against the real fixture)
--------------------------------------------------
Top-level records are introduced by a header line, then ``BEGIN`` .. ``END``::

    STATION <id> , "<descr>"
    SUBNET INDUSTRIAL_ETHERNET , "Ethernet(1)"
    SUBNET PROFIBUS , "PROFIBUS(1)"
    RACK 0, "<order#>", "UR"                              (the rack frame, no slot)
    RACK 0, SLOT <m>, "<order#>"[ "<fw>"], "<type>"      (a local module)
    RACK 0, SLOT <m>, SUBSLOT <k>, "<order#>", "<type>"  (CPU sub-modules: MPI/DP, PN-IO, ports)
    DPSUBSYSTEM 1, "PROFIBUS(1): DP master system (1)"   (the DP master, container)
    DPSUBSYSTEM 1, DPADDRESS <a>, "<GSD>", "<type>"      (a PROFIBUS-DP slave HEAD)
    DPSUBSYSTEM 1, DPADDRESS <a>, SLOT <k>, "<io>", "<type>"   (a DP sub-slot)
    IOSUBSYSTEM 100, ...                                  (PROFINET IO devices/sub-slots)

Inside a record an address block may appear (note: not wrapped in BEGIN/END --
it sits between BEGIN and END of the record)::

    LOCAL_IN_ADDRESSES
      ADDRESS  <startByte>, 0, <lenBytes>, 0, <areaCode>, 0
    SYMBOL  I , <ch>, "<name>", "<comment>"
    ...

``LOCAL_OUT_ADDRESSES`` is the same for outputs (``SYMBOL  O , ...``).

Diagnostic vs wired addresses: a DP slave HEAD carries a high ADDRESS
(2033..2047) that is the DP *diagnostic* address, NOT wired I/O. We keep that on
the head record (``DpSlave.diagnostic_addr``) and attach the *wired* ranges to
the sub-slots, so the two are never conflated.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Type-string -> (kind, points) extraction
# ---------------------------------------------------------------------------
# The module <type> string encodes its function. I/O modules lead with
# DI/DO/AI/AO followed by an integer channel count, e.g.:
#   "DI32xDC24V"      -> ("DI", 32)
#   "DO32xDC24V/0.5A" -> ("DO", 32)
#   "AI8x12Bit"       -> ("AI", 8)
# Non-I/O modules are mapped to a coarse kind with no channel count:
#   "PS 307 5A"        -> ("power", None)
#   "CPU 315-2 PN/DP"  -> ("cpu",   None)
#   "CP 340-RS232C"    -> ("comms", None)
_IO_RE = re.compile(r"^(DI|DO|AI|AO)\s*(\d+)", re.IGNORECASE)


def classify_type(type_str: str) -> Tuple[str, Optional[int]]:
    """Return ``(kind, points)`` for a module ``<type>`` string.

    Faithful and conservative: only leading ``DI|DO|AI|AO<int>`` yields a point
    count; PS/CPU/CP map to power/cpu/comms with ``points=None``; anything
    unrecognised returns ``("other", None)`` (never guessed).
    """
    if type_str is None:
        return ("other", None)
    s = type_str.strip()
    m = _IO_RE.match(s)
    if m:
        return (m.group(1).upper(), int(m.group(2)))
    up = s.upper()
    if up.startswith("PS"):
        return ("power", None)
    if up.startswith("CPU"):
        return ("cpu", None)
    if up.startswith("CP"):
        return ("comms", None)
    return ("other", None)


# A bare placeholder symbol NAME is the WHOLE name being just an address token,
# e.g. "I0.4", "I38.1", "Q11.3", "13.5" -- with or without the leading I/Q
# letter, and with NOTHING following it. The match is ANCHORED (full-match):
# a name that merely BEGINS with an address but carries a real description
# ("I2.6 LeftSide S.Det Vent", "Q3.4 Venturi AutoExp", "13.5 lamp test power")
# is a REAL wired channel, NOT a spare, and must keep its tag+comment.
_PLACEHOLDER_NAME_RE = re.compile(r"[IQ]?\d+\.\d+")


def looks_like_spare(name: str, comment: str) -> bool:
    """Cheap, non-destructive hint: does this symbol look like a spare/reserve?

    True iff the comment is exactly "Spare" OR the stripped name FULL-matches a
    bare address token (``[IQ]?<byte>.<bit>`` and nothing else). A name with any
    trailing description is a real wired channel and returns False (so its real
    tag+comment are preserved). This does NOT drop or relabel anything --
    classification is the front-end's job; provided only as a hint.
    """
    if (comment or "").strip().lower() == "spare":
        return True
    return bool(_PLACEHOLDER_NAME_RE.fullmatch((name or "").strip()))


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class Symbol:
    """One wired channel symbol inside an address block."""

    area: str          # 'I' or 'O'
    ch: int            # 0-based channel index within the module/sub-slot
    name: str
    comment: str

    @property
    def looks_like_spare(self) -> bool:
        return looks_like_spare(self.name, self.comment)


@dataclass
class AddrBlock:
    """A LOCAL_IN/OUT_ADDRESSES block: a start byte + its inline symbols."""

    direction: str               # 'in' or 'out'
    start_byte: int              # first number on the ADDRESS line
    length_bytes: int            # third number on the ADDRESS line (0 if absent)
    area_code: int               # fifth number on the ADDRESS line
    symbols: List[Symbol] = field(default_factory=list)


@dataclass
class Subnet:
    """A SUBNET record (INDUSTRIAL_ETHERNET or PROFIBUS)."""

    kind: str                    # 'INDUSTRIAL_ETHERNET' or 'PROFIBUS'
    name: str                    # e.g. "Ethernet(1)"
    subnet_mask: Optional[str] = None   # real hex mask if found (e.g. "FFFFFF00")
    ip_address: Optional[str] = None    # CPU PN-IO IP if found (hex, e.g. "C0A81EBE")


@dataclass
class Station:
    """The STATION record."""

    id: str                      # e.g. "S7300"
    descr: str                   # e.g. "SIMATIC 300(1)"


@dataclass
class CfgModule:
    """A local rack module (``RACK 0, SLOT m, ...``)."""

    rack: int
    slot: int
    order_no: str                # masked '?' kept verbatim
    fw_version: Optional[str]    # e.g. "V3.2", else None
    type_str: str
    kind: str                    # DI/DO/AI/AO/power/cpu/comms/other
    points: Optional[int]
    in_addr: Optional[AddrBlock] = None
    out_addr: Optional[AddrBlock] = None

    @property
    def symbols(self) -> List[Symbol]:
        """All inline channel symbols across the in/out blocks (in then out)."""
        out: List[Symbol] = []
        if self.in_addr:
            out.extend(self.in_addr.symbols)
        if self.out_addr:
            out.extend(self.out_addr.symbols)
        return out


@dataclass
class DpSubslot:
    """A DP slave sub-slot (``DPSUBSYSTEM 1, DPADDRESS a, SLOT k, ...``)."""

    slot: int
    io_descr: str                # e.g. "0 output bytes, 2 input bytes", "MPA1S: ... [8DO]"
    type_str: str                # e.g. "16DE", "8DA", "183", "64"
    in_addr: Optional[AddrBlock] = None
    out_addr: Optional[AddrBlock] = None

    @property
    def symbols(self) -> List[Symbol]:
        out: List[Symbol] = []
        if self.in_addr:
            out.extend(self.in_addr.symbols)
        if self.out_addr:
            out.extend(self.out_addr.symbols)
        return out

    @property
    def symbol_count(self) -> int:
        return len(self.symbols)


@dataclass
class DpSlave:
    """A PROFIBUS-DP slave (head record + its sub-slots)."""

    dp_address: int
    gsd: str                     # GSD/GSE file, e.g. "META\\SIEM80DA.GSE"
    type_str: str                # e.g. "ET 200eco 16DI", "Festo CPX-Terminal", "CMMP-AS M3"
    diagnostic_addr: Optional[int] = None   # the head's high diagnostic ADDRESS
    subslots: List[DpSubslot] = field(default_factory=list)


@dataclass
class IoSubslot:
    """A PROFINET IO sub-record (head, port, or wired module under IOSUBSYSTEM).

    The fixture also carries a PROFINET-IO system (two Keyence cameras). It is
    outside the brief's DP/local focus but present in the file, so we capture it
    faithfully here rather than silently drop data.
    """

    io_address: int              # the IOADDRESS of the device
    slot: Optional[int]          # SLOT number, or None for the device head
    subslot: Optional[int]       # SUBSLOT number if present
    gsdml: str                   # GSDML reference / "<...>" descriptor
    name: str
    in_addr: Optional[AddrBlock] = None
    out_addr: Optional[AddrBlock] = None
    # PROFINET addressing carried on the device's SLOT 0 record (the cameras'
    # real IP lives there, NOT on the head). Hex as found, e.g. "C0A81EC5";
    # None when the record carries none. NEVER invented.
    ip_address: Optional[str] = None
    subnet_mask: Optional[str] = None
    device_name: Optional[str] = None

    @property
    def symbols(self) -> List[Symbol]:
        out: List[Symbol] = []
        if self.in_addr:
            out.extend(self.in_addr.symbols)
        if self.out_addr:
            out.extend(self.out_addr.symbols)
        return out


@dataclass
class CfgData:
    """The whole parsed ``.cfg``."""

    station: Optional[Station]
    subnets: List[Subnet] = field(default_factory=list)
    modules: List[CfgModule] = field(default_factory=list)
    dp_slaves: List[DpSlave] = field(default_factory=list)
    io_devices: List[IoSubslot] = field(default_factory=list)
    fileversion: Optional[str] = None
    # The CPU's PROFIBUS-DP MASTER node address (the "MASTER DPSUBSYSTEM 1,
    # ..., DPADDRESS 2" line on the CPU MPI/DP sub-slot record). The master is
    # the bus controller; this is its DP node address. None when absent (no DP
    # master on the station). NEVER invented.
    dp_master_address: Optional[int] = None


# ---------------------------------------------------------------------------
# Low-level line helpers
# ---------------------------------------------------------------------------
# Split a comma-separated header respecting quoted fields, e.g.
#   RACK 0, SLOT 2, "6ES7 315-2EH14-0AB0" "V3.2", "CPU 315-2 PN/DP"
# -> ['RACK 0', 'SLOT 2', '"6ES7 315-2EH14-0AB0" "V3.2"', '"CPU 315-2 PN/DP"']
def _split_commas(s: str) -> List[str]:
    parts: List[str] = []
    buf = []
    in_q = False
    for ch in s:
        if ch == '"':
            in_q = not in_q
            buf.append(ch)
        elif ch == "," and not in_q:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf).strip())
    return parts


_QUOTED_RE = re.compile(r'"([^"]*)"')


def _quoted(s: str) -> List[str]:
    """Return all double-quoted substrings in order."""
    return _QUOTED_RE.findall(s)


def _read_text(path: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _parse_symbol_line(line: str) -> Optional[Symbol]:
    """Parse ``SYMBOL  I , 0, "name", "comment"`` -> Symbol."""
    # After the SYMBOL keyword: area, then ", ch, "name", "comment""
    rest = line[len("SYMBOL"):].lstrip()
    # area is the first token before the comma
    m = re.match(r'([IO])\s*,\s*(\d+)\s*,(.*)$', rest)
    if not m:
        return None
    area = m.group(1)
    ch = int(m.group(2))
    tail = m.group(3)
    q = _quoted(tail)
    name = q[0] if len(q) >= 1 else ""
    comment = q[1] if len(q) >= 2 else ""
    return Symbol(area=area, ch=ch, name=name, comment=comment)


def _parse_address_line(line: str) -> Tuple[int, int, int]:
    """Parse ``ADDRESS  <start>, 0, <len>, 0, <area>, 0`` -> (start, len, area)."""
    nums = re.findall(r'-?\d+', line)
    start = int(nums[0]) if len(nums) >= 1 else 0
    length = int(nums[2]) if len(nums) >= 3 else 0
    areacode = int(nums[4]) if len(nums) >= 5 else 0
    return start, length, areacode


# ---------------------------------------------------------------------------
# Record collection
# ---------------------------------------------------------------------------
# A "record" is the header line plus everything up to (but not including) the
# next top-level header line. STEP 7 .cfg records start at column 0; inner
# attributes / address blocks / symbols are part of the current record.
_HEADER_PREFIXES = ("STATION", "SUBNET", "IRT_DOMAIN", "RACK", "DPSUBSYSTEM",
                    "IOSUBSYSTEM")


def _is_header(line: str) -> bool:
    if line[:1].isspace():
        return False
    for p in _HEADER_PREFIXES:
        if line.startswith(p):
            return True
    return False


def _iter_records(text: str):
    """Yield (header_line, body_lines) for each top-level record."""
    lines = text.splitlines()
    header = None
    body: List[str] = []
    for line in lines:
        stripped = line.rstrip("\r\n")
        if _is_header(stripped):
            if header is not None:
                yield header, body
            header = stripped
            body = []
        else:
            if header is not None:
                body.append(stripped)
    if header is not None:
        yield header, body


def _collect_addr_blocks(body: List[str]) -> Tuple[Optional[AddrBlock], Optional[AddrBlock]]:
    """Scan a record body for LOCAL_IN/OUT_ADDRESSES blocks + their symbols.

    For a HEAD record with multiple ADDRESS lines we keep the FIRST ADDRESS as
    the block's start_byte (heads have no wired symbols; their single high
    address is the diagnostic one). Symbols attach to whichever block keyword
    most recently opened.
    """
    in_block: Optional[AddrBlock] = None
    out_block: Optional[AddrBlock] = None
    current: Optional[AddrBlock] = None
    for raw in body:
        s = raw.strip()
        if s.startswith("LOCAL_IN_ADDRESSES"):
            current = in_block = AddrBlock(direction="in", start_byte=0,
                                           length_bytes=0, area_code=0)
            current._seen_addr = False  # type: ignore[attr-defined]
        elif s.startswith("LOCAL_OUT_ADDRESSES"):
            current = out_block = AddrBlock(direction="out", start_byte=0,
                                            length_bytes=0, area_code=0)
            current._seen_addr = False  # type: ignore[attr-defined]
        elif s.startswith("ADDRESS") and current is not None:
            # Only record the FIRST ADDRESS line of the block as start_byte.
            if not getattr(current, "_seen_addr", False):
                start, length, areacode = _parse_address_line(s)
                current.start_byte = start
                current.length_bytes = length
                current.area_code = areacode
                current._seen_addr = True  # type: ignore[attr-defined]
        elif s.startswith("SYMBOL") and current is not None:
            sym = _parse_symbol_line(s)
            if sym is not None:
                current.symbols.append(sym)
        elif s in ("END", "END ", "PARAMETER", "PARAMETER "):
            # PARAMETER ends the I/O symbol region for this record.
            if s.startswith("PARAMETER"):
                current = None
    # Clean the helper attribute (best effort; harmless if it lingers).
    for blk in (in_block, out_block):
        if blk is not None and hasattr(blk, "_seen_addr"):
            try:
                delattr(blk, "_seen_addr")
            except AttributeError:
                pass
    return in_block, out_block


# ---------------------------------------------------------------------------
# Header parsers
# ---------------------------------------------------------------------------
def _parse_station(header: str) -> Station:
    # STATION S7300 , "SIMATIC 300(1)"
    q = _quoted(header)
    descr = q[0] if q else ""
    m = re.match(r'STATION\s+(\S+)', header)
    sid = m.group(1) if m else ""
    return Station(id=sid, descr=descr)


def _parse_subnet(header: str) -> Subnet:
    # SUBNET INDUSTRIAL_ETHERNET , "Ethernet(1)"
    m = re.match(r'SUBNET\s+(\S+)', header)
    kind = m.group(1) if m else ""
    q = _quoted(header)
    name = q[0] if q else ""
    return Subnet(kind=kind, name=name)


def _parse_rack_module(header: str, fields: List[str]) -> Optional[CfgModule]:
    # fields[0] == "RACK 0"; need SLOT.
    rack_m = re.match(r'RACK\s+(\d+)', fields[0])
    rack = int(rack_m.group(1)) if rack_m else 0
    slot = None
    subslot_present = False
    for f in fields[1:]:
        sm = re.match(r'SLOT\s+(\d+)', f)
        if sm:
            slot = int(sm.group(1))
        if re.match(r'SUBSLOT\s+(\d+)', f):
            subslot_present = True
    if slot is None or subslot_present:
        # Rack frame (no SLOT) or a CPU sub-slot (MPI/DP, PN-IO, ports) -> not a
        # wired local rack module; skip for the modules list.
        return None
    # The order#/fw/type live in the quoted fields. The slot field may contain
    # '"6ES7 ..." "V3.2"' (order + fw) then a separate type field.
    quoted_fields = [f for f in fields if '"' in f]
    # Flatten all quoted tokens in order across the (typically 2) trailing fields.
    tokens: List[str] = []
    for f in fields:
        tokens.extend(_quoted(f))
    if not tokens:
        return None
    order_no = tokens[0]
    fw_version = None
    type_str = ""
    if len(tokens) == 2:
        type_str = tokens[1]
    elif len(tokens) >= 3:
        # order, fw, type  (fw looks like "V3.2")
        fw_version = tokens[1]
        type_str = tokens[2]
    kind, points = classify_type(type_str)
    return CfgModule(rack=rack, slot=slot, order_no=order_no,
                     fw_version=fw_version, type_str=type_str,
                     kind=kind, points=points)


def _parse_dp_fields(fields: List[str]):
    """Extract (dp_address, slot_or_None) from DPSUBSYSTEM header fields."""
    dp_addr = None
    slot = None
    for f in fields:
        m = re.match(r'DPADDRESS\s+(\d+)', f)
        if m:
            dp_addr = int(m.group(1))
        m = re.match(r'SLOT\s+(\d+)', f)
        if m:
            slot = int(m.group(1))
    return dp_addr, slot


def _parse_io_fields(fields: List[str]):
    """Extract (io_address, slot, subslot) from IOSUBSYSTEM header fields."""
    io_addr = None
    slot = None
    subslot = None
    for f in fields:
        m = re.match(r'IOADDRESS\s+(\d+)', f)
        if m:
            io_addr = int(m.group(1))
        m = re.match(r'SLOT\s+(\d+)', f)
        if m:
            slot = int(m.group(1))
        m = re.match(r'SUBSLOT\s+(\d+)', f)
        if m:
            subslot = int(m.group(1))
    return io_addr, slot, subslot


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def parse_cfg(path: str) -> CfgData:
    """Parse an S7-300 STEP 7 ``.cfg`` hardware-config file into :class:`CfgData`."""
    text = _read_text(path)

    fileversion = None
    fv = re.search(r'FILEVERSION\s+"([^"]*)"', text)
    if fv:
        fileversion = fv.group(1)

    data = CfgData(station=None, fileversion=fileversion)

    # Track the "current DP slave" so sub-slots attach to their head.
    current_dp: Optional[DpSlave] = None

    for header, body in _iter_records(text):
        fields = _split_commas(header)
        head0 = fields[0]

        if head0.startswith("STATION"):
            data.station = _parse_station(header)

        elif head0.startswith("SUBNET"):
            data.subnets.append(_parse_subnet(header))

        elif head0.startswith("IRT_DOMAIN"):
            continue  # not needed for the data model

        elif head0.startswith("RACK"):
            # The CPU PN-IO sub-slot carries the SUBNETMASK + IPADDRESS we want
            # to fold onto the Ethernet subnet.
            mask = None
            ip = None
            for raw in body:
                s = raw.strip()
                mm = re.match(r'SUBNETMASK\s+"([^"]*)"', s)
                if mm:
                    mask = mm.group(1)
                ipm = re.match(r'IPADDRESS\s+"([^"]*)"', s)
                if ipm:
                    ip = ipm.group(1)
                # The CPU MPI/DP sub-slot declares the DP MASTER node address on a
                # "MASTER DPSUBSYSTEM 1, ..., DPADDRESS <a>" line in its body.
                # Capture it once (the real master DP address, e.g. 2).
                msm = re.match(r'MASTER\s+DPSUBSYSTEM.*\bDPADDRESS\s+(\d+)', s)
                if msm and data.dp_master_address is None:
                    data.dp_master_address = int(msm.group(1))
            if mask or ip:
                for sn in data.subnets:
                    if sn.kind == "INDUSTRIAL_ETHERNET":
                        if mask and sn.subnet_mask is None:
                            sn.subnet_mask = mask
                        if ip and sn.ip_address is None:
                            sn.ip_address = ip
                        break
            module = _parse_rack_module(header, fields)
            if module is not None:
                in_blk, out_blk = _collect_addr_blocks(body)
                module.in_addr = in_blk
                module.out_addr = out_blk
                data.modules.append(module)

        elif head0.startswith("DPSUBSYSTEM"):
            dp_addr, slot = _parse_dp_fields(fields)
            tokens = []
            for f in fields:
                tokens.extend(_quoted(f))
            if dp_addr is None:
                # The DP master container ("PROFIBUS(1): DP master system (1)").
                current_dp = None
                continue
            if slot is None:
                # Slave HEAD: DPSUBSYSTEM 1, DPADDRESS a, "<GSD>", "<type>"
                gsd = tokens[0] if len(tokens) >= 1 else ""
                type_str = tokens[1] if len(tokens) >= 2 else ""
                in_blk, _ = _collect_addr_blocks(body)
                diag = in_blk.start_byte if in_blk is not None else None
                slave = DpSlave(dp_address=dp_addr, gsd=gsd, type_str=type_str,
                                diagnostic_addr=diag)
                data.dp_slaves.append(slave)
                current_dp = slave
            else:
                # Sub-slot: DPSUBSYSTEM 1, DPADDRESS a, SLOT k, "<io>", "<type>"
                io_descr = tokens[0] if len(tokens) >= 1 else ""
                type_str = tokens[1] if len(tokens) >= 2 else ""
                in_blk, out_blk = _collect_addr_blocks(body)
                ss = DpSubslot(slot=slot, io_descr=io_descr, type_str=type_str,
                               in_addr=in_blk, out_addr=out_blk)
                # Attach to the matching head (by dp_address) defensively.
                target = current_dp
                if target is None or target.dp_address != dp_addr:
                    target = next((s for s in data.dp_slaves
                                   if s.dp_address == dp_addr), None)
                if target is not None:
                    target.subslots.append(ss)

        elif head0.startswith("IOSUBSYSTEM"):
            io_addr, slot, subslot = _parse_io_fields(fields)
            tokens = []
            for f in fields:
                tokens.extend(_quoted(f))
            if io_addr is None:
                continue  # the PROFINET-IO system container
            gsdml = tokens[0] if len(tokens) >= 1 else ""
            name = tokens[1] if len(tokens) >= 2 else ""
            in_blk, out_blk = _collect_addr_blocks(body)
            # The camera SLOT 0 record carries the device IPADDRESS / SUBNETMASK
            # (and CPUs a DEVICE_NAME); capture them verbatim (hex as found). The
            # head record carries none, so these stay None there. NEVER invented.
            ip = mask = dev_name = None
            for raw in body:
                s = raw.strip()
                im = re.match(r'IPADDRESS\s+"([^"]*)"', s)
                if im:
                    ip = im.group(1)
                mm = re.match(r'SUBNETMASK\s+"([^"]*)"', s)
                if mm:
                    mask = mm.group(1)
                dm = re.match(r'DEVICE_NAME\s+"([^"]*)"', s)
                if dm:
                    dev_name = dm.group(1)
            data.io_devices.append(IoSubslot(
                io_address=io_addr, slot=slot, subslot=subslot,
                gsdml=gsdml, name=name, in_addr=in_blk, out_addr=out_blk,
                ip_address=ip, subnet_mask=mask, device_name=dev_name))

    return data
