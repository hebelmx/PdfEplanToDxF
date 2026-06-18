#!/usr/bin/env python3
"""Unit + integration tests for the S7-300 ``.asc`` global symbol-table parser.

Stdlib-only (unittest). Run from src/:
    python -m unittest test_s7300_asc
or via discovery:
    python -m unittest discover -p "test_*.py"

Two groups:
  * Pure-helper tests on small synthetic lines (column layout, bit-addr parse,
    long-address rows, name-with-spaces, FC/DB datatype-duplication) -- run
    everywhere, no fixture.
  * Fixture-gated tests asserting the REAL measured counts (area histogram,
    physical_io count, the control-off cross-check) -- skipped if the
    (gitignored) fixture is absent.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import s7300_asc as A


def _fixture() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "Fixtures",
                        "Siemens", "S7300", "brpl2twin.txt.asc")


_HAVE_FIXTURE = os.path.exists(_fixture())


class TestAscHelpers(unittest.TestCase):
    """Pure parsing of individual lines -- no fixture needed."""

    def test_simple_input_bit(self):
        line = ('126,control off             '
                'I       0.0 BOOL      PB206A  - NO pushbutton')
        s = A.parse_line(line)
        self.assertEqual(s.name, "control off")
        self.assertEqual(s.area, "I")
        self.assertEqual(s.addr, "0.0")
        self.assertEqual(s.bit_addr, (0, 0))
        self.assertEqual(s.datatype, "BOOL")
        self.assertEqual(s.comment, "PB206A  - NO pushbutton")
        self.assertTrue(s.is_physical)

    def test_output_bit(self):
        line = ('126,13.5 lamp test power    '
                'Q       3.7 BOOL      Power on to lamp')
        s = A.parse_line(line)
        self.assertEqual(s.name, "13.5 lamp test power")
        self.assertEqual(s.area, "Q")
        self.assertEqual(s.bit_addr, (3, 7))
        self.assertEqual(s.comment, "Power on to lamp")
        self.assertTrue(s.is_physical)

    def test_piw_word_no_bit(self):
        line = ('126,Camera_Result           '
                'PIW   372   WORD')
        s = A.parse_line(line)
        self.assertEqual(s.area, "PIW")
        self.assertEqual(s.addr, "372")
        self.assertIsNone(s.bit_addr)
        self.assertEqual(s.datatype, "WORD")
        self.assertEqual(s.comment, "")
        self.assertTrue(s.is_physical)

    def test_memory_flag_not_physical(self):
        line = ('126,1 minute sign           '
                'M     965.6 BOOL')
        s = A.parse_line(line)
        self.assertEqual(s.area, "M")
        self.assertEqual(s.bit_addr, (965, 6))
        self.assertFalse(s.is_physical)

    def test_fc_object_datatype_is_duplicated_verbatim(self):
        # For program blocks the "datatype" column repeats "<area> <num>".
        line = ('126,act time                '
                'FC     45   FC     45')
        s = A.parse_line(line)
        self.assertEqual(s.area, "FC")
        self.assertEqual(s.addr, "45")
        self.assertIsNone(s.bit_addr)
        self.assertEqual(s.datatype, "FC     45")  # preserved verbatim, not cleaned
        self.assertEqual(s.comment, "")
        self.assertFalse(s.is_physical)

    def test_long_four_digit_address_overruns_column(self):
        # A 4-digit address eats into the area padding; tokenising must still
        # recover area="Q", addr="1300.0".
        line = ('126,Np                      '
                'Q    1300.0 BOOL')
        s = A.parse_line(line)
        self.assertEqual(s.area, "Q")
        self.assertEqual(s.addr, "1300.0")
        self.assertEqual(s.bit_addr, (1300, 0))

    def test_wide_operand_datatype_not_truncated(self):
        # m1 regression: a wide operand ("PIW 1400") pushes the datatype past
        # the old fixed col-36 slice. Tokenising the post-name remainder must
        # recover the FULL datatype ("WORD"), not a truncated fragment.
        line = ('126,WidePIW                 '
                'PIW 1400    WORD')
        s = A.parse_line(line)
        self.assertEqual(s.area, "PIW")
        self.assertEqual(s.addr, "1400")
        self.assertEqual(s.datatype, "WORD")   # not "" and not truncated
        self.assertTrue(s.is_physical)

    def test_long_address_db_object(self):
        line = ('126,LEFT_REAR_CAMERA        '
                'DB   1148   DB   1148')
        s = A.parse_line(line)
        self.assertEqual(s.area, "DB")
        self.assertEqual(s.addr, "1148")
        self.assertFalse(s.is_physical)

    def test_name_with_spaces_24_char_boundary(self):
        # A name that exactly fills 24 chars (no trailing space) still parses.
        name = "I2.6 LeftSide S.Det Vent"  # 24 chars
        self.assertEqual(len(name), 24)
        line = '126,' + name + 'I       2.6 BOOL'
        s = A.parse_line(line)
        self.assertEqual(s.name, name)
        self.assertEqual(s.area, "I")
        self.assertEqual(s.bit_addr, (2, 6))

    def test_blank_line_returns_none(self):
        self.assertIsNone(A.parse_line(""))
        self.assertIsNone(A.parse_line("   \r\n"))

    def test_bit_addr_only_for_two_int_dotted(self):
        self.assertEqual(A._parse_bit_addr("0.2"), (0, 2))
        self.assertIsNone(A._parse_bit_addr("372"))
        self.assertIsNone(A._parse_bit_addr("1.2.3"))
        self.assertIsNone(A._parse_bit_addr("x.y"))

    def test_physical_areas_membership(self):
        self.assertIn("I", A.PHYSICAL_AREAS)
        self.assertIn("Q", A.PHYSICAL_AREAS)
        self.assertIn("PIW", A.PHYSICAL_AREAS)
        self.assertIn("PQW", A.PHYSICAL_AREAS)
        self.assertNotIn("M", A.PHYSICAL_AREAS)
        self.assertNotIn("FC", A.PHYSICAL_AREAS)


@unittest.skipUnless(_HAVE_FIXTURE, "S7300 .asc fixture not present")
class TestAscFixture(unittest.TestCase):
    """Integration against the real fixture -- asserts MEASURED ground truth."""

    @classmethod
    def setUpClass(cls):
        cls.syms = A.parse_asc(_fixture())
        cls.hist = A.area_histogram(cls.syms)

    def test_total_row_count(self):
        self.assertEqual(len(self.syms), 1467)

    def test_area_histogram(self):
        # Measured from the fixture (the brief's numbers were approximate).
        expected = {
            "M": 732, "I": 176, "T": 166, "Q": 139, "FC": 82, "DB": 74,
            "VAT": 30, "FB": 16, "MW": 13, "UDT": 9, "OB": 9, "SFC": 8,
            "PIW": 4, "QD": 4, "SFB": 3, "MD": 2,
        }
        self.assertEqual(self.hist, expected)
        # No corrupted multi-token area survived the column overrun.
        for area in self.hist:
            self.assertEqual(area, area.strip())
            self.assertNotIn(" ", area)

    def test_physical_io_filter(self):
        phys = A.physical_io(self.syms)
        # I(176) + Q(139) + PIW(4) = 319 ; no PQW present.
        self.assertEqual(len(phys), 319)
        for s in phys:
            self.assertIn(s.area, A.PHYSICAL_AREAS)
        self.assertTrue(all(s.area in ("I", "Q", "PIW") for s in phys))

    def test_control_off_cross_check(self):
        # Sanity cross-check vs the .cfg slot-4 DI32 channel 0 inline symbol.
        co = [s for s in self.syms if s.name == "control off"]
        self.assertEqual(len(co), 1)
        s = co[0]
        self.assertEqual(s.area, "I")
        self.assertEqual(s.addr, "0.0")
        self.assertEqual(s.bit_addr, (0, 0))
        self.assertEqual(s.comment, "PB206A  - NO pushbutton")

    def test_piw_rows_are_words(self):
        piw = [s for s in self.syms if s.area == "PIW"]
        self.assertEqual(len(piw), 4)
        for s in piw:
            self.assertIsNone(s.bit_addr)
            self.assertEqual(s.datatype, "WORD")
            self.assertTrue(s.is_physical)


if __name__ == "__main__":
    unittest.main()
