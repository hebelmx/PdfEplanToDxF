#!/usr/bin/env python3
"""Unit tests for the device-designation helper in logix_to_qet.py.

Stdlib-only (unittest). Run from anywhere:
    python -m unittest src.test_logix_to_qet
or from src/:
    python -m unittest test_logix_to_qet
"""

import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
