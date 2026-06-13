#!/usr/bin/env python3
"""Unit tests for the designation/wire-number helpers in logix_to_qet.py.

Stdlib-only (unittest). Run from anywhere:
    python -m unittest src.test_logix_to_qet
or from src/:
    python -m unittest test_logix_to_qet
"""

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

import logix_to_qet as q


class NextDesignationTest(unittest.TestCase):
    def test_format_is_page_prefixed(self):
        """-<L><page>.<n> — letter, page, dot, per-page sequence."""
        self.assertEqual(q.next_designation({"dt": "K"}, {}, 3), "-K3.1")

    def test_sequence_increments_per_page_letter(self):
        counters = {}
        self.assertEqual(q.next_designation({"dt": "B"}, counters, 1), "-B1.1")
        self.assertEqual(q.next_designation({"dt": "B"}, counters, 1), "-B1.2")
        self.assertEqual(q.next_designation({"dt": "B"}, counters, 1), "-B1.3")

    def test_letters_count_independently(self):
        counters = {}
        self.assertEqual(q.next_designation({"dt": "B"}, counters, 1), "-B1.1")
        self.assertEqual(q.next_designation({"dt": "S"}, counters, 1), "-S1.1")
        self.assertEqual(q.next_designation({"dt": "B"}, counters, 1), "-B1.2")

    def test_counter_resets_each_page(self):
        counters = {}
        self.assertEqual(q.next_designation({"dt": "K"}, counters, 1), "-K1.1")
        self.assertEqual(q.next_designation({"dt": "K"}, counters, 2), "-K2.1")
        self.assertEqual(q.next_designation({"dt": "K"}, counters, 1), "-K1.2")

    def test_coil_and_feedback_never_collide_across_pages(self):
        """Coils live on output-card folios, feedback contacts on input-card
        folios; the page prefix keeps both K-series apart with no phantom
        coils — the MVP resolution to coil/contact cross-referencing."""
        counters = {}
        # output card page 3: two coils
        self.assertEqual(q.next_designation({"dt": "K"}, counters, 3), "-K3.1")
        self.assertEqual(q.next_designation({"dt": "K"}, counters, 3), "-K3.2")
        # input card page 5: a contactor feedback contact (also dt K)
        self.assertEqual(q.next_designation({"dt": "K"}, counters, 5), "-K5.1")

    def test_normalizes_case_and_whitespace(self):
        self.assertEqual(q.next_designation({"dt": " k "}, {}, 2), "-K2.1")

    def test_missing_dt_returns_none(self):
        self.assertIsNone(q.next_designation({}, {}, 1))

    def test_blank_dt_returns_none(self):
        self.assertIsNone(q.next_designation({"dt": "   "}, {}, 1))
        self.assertIsNone(q.next_designation({"dt": ""}, {}, 1))

    def test_non_string_dt_returns_none(self):
        for bad in (None, 7, ["K"], {"x": 1}):
            self.assertIsNone(q.next_designation({"dt": bad}, {}, 1))

    def test_multichar_or_non_alpha_dt_returns_none(self):
        """A malformed DB letter degrades gracefully instead of emitting a
        colliding/garbage designation (e.g. 'B1' must not become -B11.x)."""
        for bad in ("B1", "KA", "K-", "1", "!", "ñ"):
            self.assertIsNone(q.next_designation({"dt": bad}, {}, 1))

    def test_bad_dt_does_not_consume_a_number(self):
        counters = {}
        self.assertIsNone(q.next_designation({"dt": "B1"}, counters, 1))
        self.assertEqual(q.next_designation({"dt": "B"}, counters, 1), "-B1.1")

    def test_deterministic_repeat(self):
        seq1 = []
        c1 = {}
        for page, dt in [(1, "B"), (1, "B"), (1, "S"), (3, "K"), (3, "K")]:
            seq1.append(q.next_designation({"dt": dt}, c1, page))
        seq2 = []
        c2 = {}
        for page, dt in [(1, "B"), (1, "B"), (1, "S"), (3, "K"), (3, "K")]:
            seq2.append(q.next_designation({"dt": dt}, c2, page))
        self.assertEqual(seq1, seq2)
        self.assertEqual(seq1, ["-B1.1", "-B1.2", "-S1.1", "-K3.1", "-K3.2"])


class WireNumberTest(unittest.TestCase):
    def test_address_scheme_returns_address_verbatim(self):
        """Default scheme: address passes through untouched, no page prefix."""
        for addr in ("Q0.0", "I2.4", "IW100"):
            self.assertEqual(
                q.wire_number(addr, 3, "address", {}), addr)

    def test_sequential_scheme_increments_within_page(self):
        counters = {}
        self.assertEqual(q.wire_number("Q0.0", 3, "sequential", counters), "W3.1")
        self.assertEqual(q.wire_number("Q0.1", 3, "sequential", counters), "W3.2")
        self.assertEqual(q.wire_number("Q0.2", 3, "sequential", counters), "W3.3")

    def test_sequential_scheme_resets_on_new_page(self):
        counters = {}
        self.assertEqual(q.wire_number("I0.0", 3, "sequential", counters), "W3.1")
        self.assertEqual(q.wire_number("I0.1", 3, "sequential", counters), "W3.2")
        self.assertEqual(q.wire_number("I0.0", 4, "sequential", counters), "W4.1")
        # page 3 continues from where it left off, independent of page 4
        self.assertEqual(q.wire_number("I0.2", 3, "sequential", counters), "W3.3")

    def test_none_or_empty_address_returns_none_both_schemes(self):
        """Guardrail: no defined source point -> no invented number."""
        for scheme in ("address", "sequential"):
            for addr in (None, "", "   "):
                counters = {}
                self.assertIsNone(
                    q.wire_number(addr, 3, scheme, counters))
                # and it must not consume a sequential number
                self.assertEqual(counters, {})

    def test_deterministic_repeat(self):
        inputs = [("Q0.0", 3), ("Q0.1", 3), ("I0.0", 4), ("I0.1", 4), ("I0.2", 3)]
        seq1, c1 = [], {}
        for addr, page in inputs:
            seq1.append(q.wire_number(addr, page, "sequential", c1))
        seq2, c2 = [], {}
        for addr, page in inputs:
            seq2.append(q.wire_number(addr, page, "sequential", c2))
        self.assertEqual(seq1, seq2)
        self.assertEqual(seq1, ["W3.1", "W3.2", "W4.1", "W4.2", "W3.3"])

    def test_address_scheme_trims_surrounding_whitespace(self):
        """Emptiness is judged on the stripped form, so the returned value is
        stripped too — no padded num lands in the conductor attribute."""
        self.assertEqual(q.wire_number("  Q0.0  ", 3, "address", {}), "Q0.0")


class BuildFolioWireNumberTest(unittest.TestCase):
    """Integration: the build_folio call site must actually populate the
    conductor `num` (the pure-helper tests above can't catch a broken/dropped
    call site, which is the whole point of the feature)."""

    @staticmethod
    def _folio(scheme):
        """Build one folio with two symbol-matching input points (LS1/LS2 ->
        limit_switch) and return the resulting <diagram> element."""
        symbols = q.load_symbol_db()
        mod = SimpleNamespace(rack=1, slot=2, name="CARD", catalog="FAKE-NODB",
                              kind="DI", points=16, in_byte_base=0,
                              out_byte_base=0, an_in_word_base=0,
                              an_out_word_base=0)
        pts = [SimpleNamespace(module=mod, index=i, tag=tag, direction="I",
                               description="", analog=False)
               for i, tag in ((0, "LS1"), (1, "LS2"))]
        project = ET.Element("project")
        q.build_folio(project, 3, mod, pts, symbols, {}, {},
                      wire_scheme=scheme, wire_counters={})
        return project.find("diagram")

    def test_address_scheme_emits_eplan_address_verbatim(self):
        diagram = self._folio("address")
        nums = [c.get("num") for c in diagram.find("conductors").findall("conductor")]
        self.assertEqual(nums, ["I0.0", "I0.1"])
        # every field conductor carries a non-empty num...
        self.assertTrue(all(nums))
        # ...while the defaultconductor template stays empty (sourceless).
        self.assertEqual(diagram.find("defaultconductor").get("num"), "")

    def test_sequential_scheme_emits_per_folio_wire_numbers(self):
        diagram = self._folio("sequential")
        nums = [c.get("num") for c in diagram.find("conductors").findall("conductor")]
        self.assertEqual(nums, ["W3.1", "W3.2"])
        self.assertTrue(all(nums))
        self.assertEqual(diagram.find("defaultconductor").get("num"), "")


if __name__ == "__main__":
    unittest.main()
