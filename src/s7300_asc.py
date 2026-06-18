#!/usr/bin/env python3
"""Faithful parser for the Siemens S7-300 (STEP 7 Classic) global symbol table
(the ``.asc`` export, e.g. ``brpl2twin.txt.asc``).

SCOPE / PHILOSOPHY
------------------
This module is *data-only*: it reads exactly what is in the file into plain
Python structures and performs NO classification, NO invention, NO IR building.
A missing field is recorded as ``""`` (never fabricated). Downstream code decides
what a row *means*.

FILE FORMAT (verified against the real fixture, 1467 rows, ASCII / CRLF)
-----------------------------------------------------------------------
Every row is fixed-width and begins with the literal ``126,`` (an internal STEP 7
language/charset id). After that prefix the body has stable columns::

    col  0..23  symbol name (left-justified, space-padded; names may contain
                spaces, e.g. "control off", "ATEQ test ok"; a 24-char name fills
                the column with no trailing space and the area starts at col 24)
    col 24..35  operand area + address, whitespace-separated. The area is the
                first token, the address the second. NOTE: the split between
                area and address is NOT a fixed sub-column -- a 4-digit address
                (e.g. "1400.2", "1148") eats into the padding, so we tokenise
                this span rather than slice it. (areas: I, Q, M, T, C, FC, FB,
                DB, PIW, PQW, MW, MD, QD, OB, SFC, SFB, VAT, UDT ...)
    col 36..45  "datatype"        (BOOL / WORD / DWORD / TIMER for real operands;
                                   for program objects FC/FB/DB/SFC/SFB this column
                                   instead REPEATS "<area> <number>", so it is NOT a
                                   datatype -- exposed verbatim as ``datatype_raw``)
    col 46..    comment           (may be empty)

PHYSICAL vs PROGRAM rows
------------------------
Only operand areas that map to wired field I/O are "physical":
    I  (digital input bit)      Q  (digital output bit)
    PIW (peripheral input word)  PQW (peripheral output word)
Everything else (M flag, T timer, C counter, FC/FB/DB blocks, OB, SFC/SFB,
VAT/UDT, MW/MD/QD ...) is a program/flag object. ``physical_io()`` filters to
the physical rows; the raw parse keeps ALL rows tagged by area so a caller can
filter differently.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Column layout (0-based offsets into the body, i.e. the line AFTER the "126,"
# prefix). Derived empirically from the fixture and asserted by the tests.
# ---------------------------------------------------------------------------
_PREFIX = "126,"
_COL_NAME = (0, 24)
_COL_OPERAND = (24, 36)   # area + address span (tokenised, see module docstring)
_COL_DTYPE = (36, 46)
_COL_COMMENT = 46

# Operand areas that represent physical (wired) field I/O.
PHYSICAL_AREAS = frozenset({"I", "Q", "PIW", "PQW"})


@dataclass
class AscSymbol:
    """One row of the ``.asc`` global symbol table, recorded verbatim.

    Attributes:
        name: symbol name (trailing pad stripped; internal spaces preserved).
        area: operand area letter(s), e.g. "I", "Q", "M", "FC", "PIW".
        addr: raw address string exactly as in the file ("0.2", "372", "45").
        bit_addr: for bit operands (I/Q) the parsed ``(byte, bit)`` tuple,
            else ``None``. Never fabricated -- only set when ``addr`` is a
            clean ``<int>.<int>``.
        datatype: the datatype column verbatim. For real operands this is
            BOOL/WORD/DWORD/TIMER; for program blocks (FC/FB/DB/...) the file
            repeats "<area> <number>" here -- preserved as-is, NOT cleaned.
        comment: free-text comment ("" when absent).
        is_physical: True iff ``area`` is a wired I/O area (I/Q/PIW/PQW).
    """

    name: str
    area: str
    addr: str
    bit_addr: Optional[Tuple[int, int]]
    datatype: str
    comment: str

    @property
    def is_physical(self) -> bool:
        return self.area in PHYSICAL_AREAS


def _parse_bit_addr(addr: str) -> Optional[Tuple[int, int]]:
    """Parse a ``byte.bit`` address into ``(byte, bit)``; else ``None``.

    Faithful: returns None for anything that is not exactly two dot-separated
    non-negative integers (e.g. word/object addresses).
    """
    if addr.count(".") != 1:
        return None
    a, b = addr.split(".", 1)
    if a.isdigit() and b.isdigit():
        return (int(a), int(b))
    return None


def parse_line(line: str) -> Optional[AscSymbol]:
    """Parse a single ``.asc`` line into an :class:`AscSymbol`.

    Returns ``None`` for blank lines or lines that do not carry the ``126,``
    record prefix (defensive -- the real fixture has no such lines, but a
    trailing newline must not crash the caller).
    """
    # Keep the line as-is except for the trailing CR/LF; columns are significant.
    line = line.rstrip("\r\n")
    if not line.strip():
        return None
    if not line.startswith(_PREFIX):
        return None
    body = line[len(_PREFIX):]
    name = body[_COL_NAME[0]:_COL_NAME[1]].rstrip()
    # area + address share a span; tokenise it (a long address overruns the
    # nominal area sub-column, so a fixed slice is unsafe here).
    operand_toks = body[_COL_OPERAND[0]:_COL_OPERAND[1]].split()
    area = operand_toks[0] if len(operand_toks) >= 1 else ""
    addr = operand_toks[1] if len(operand_toks) >= 2 else ""
    datatype = body[_COL_DTYPE[0]:_COL_DTYPE[1]].strip()
    comment = body[_COL_COMMENT:].rstrip()
    if not area:
        # Defensive: a row with no operand area is malformed; skip it rather
        # than fabricate. (Not seen in the fixture.)
        return None
    return AscSymbol(
        name=name,
        area=area,
        addr=addr,
        bit_addr=_parse_bit_addr(addr),
        datatype=datatype,
        comment=comment,
    )


def _read_text(path: str) -> str:
    """Read the file as text, tolerant of encoding (utf-8 then latin-1)."""
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def parse_asc(path: str) -> List[AscSymbol]:
    """Parse a whole ``.asc`` global symbol table file.

    Returns ALL rows (physical and program/flag objects alike), each tagged
    with its operand area so the caller can filter. Use :func:`physical_io`
    for the wired-I/O subset.
    """
    text = _read_text(path)
    out: List[AscSymbol] = []
    for line in text.splitlines():
        sym = parse_line(line)
        if sym is not None:
            out.append(sym)
    return out


def physical_io(symbols: List[AscSymbol]) -> List[AscSymbol]:
    """Return only the physical (wired) I/O rows: areas I, Q, PIW, PQW."""
    return [s for s in symbols if s.is_physical]


def area_histogram(symbols: List[AscSymbol]) -> dict:
    """Convenience: count rows per operand area (handy for sanity checks)."""
    hist: dict = {}
    for s in symbols:
        hist[s.area] = hist.get(s.area, 0) + 1
    return hist
