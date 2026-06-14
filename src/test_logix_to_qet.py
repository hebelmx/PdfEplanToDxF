#!/usr/bin/env python3
"""Unit tests for the designation/wire-number helpers in logix_to_qet.py.

Stdlib-only (unittest). Run from anywhere:
    python -m unittest src.test_logix_to_qet
or from src/:
    python -m unittest test_logix_to_qet
"""

import csv
import io
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
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
        # the inline strip terminal breaks each matched field conductor into TWO
        # segments (card->strip, strip->device); the wire number rides the
        # card->strip segment ONLY, so it appears EXACTLY ONCE per matched point
        # (not lost, not duplicated). The strip->device segment carries "".
        self.assertEqual([n for n in nums if n], ["I0.0", "I0.1"])
        self.assertEqual(nums.count("I0.0"), 1)
        self.assertEqual(nums.count("I0.1"), 1)
        # the defaultconductor template stays empty (sourceless).
        self.assertEqual(diagram.find("defaultconductor").get("num"), "")

    def test_sequential_scheme_emits_per_folio_wire_numbers(self):
        diagram = self._folio("sequential")
        nums = [c.get("num") for c in diagram.find("conductors").findall("conductor")]
        # each wire number appears exactly once (on the card->strip segment); the
        # per-folio counter still advances once per matched point, not per segment.
        self.assertEqual([n for n in nums if n], ["W3.1", "W3.2"])
        self.assertEqual(nums.count("W3.1"), 1)
        self.assertEqual(nums.count("W3.2"), 1)
        self.assertEqual(diagram.find("defaultconductor").get("num"), "")


class BomRowBuilderTest(unittest.TestCase):
    """Pure row-builder helper: column fill/blank pattern per category, the
    no-invented-designation guardrail, analog-goes-generic, determinism. No
    I/O — these run against the helpers only."""

    def test_schema_has_exactly_ten_ordered_columns(self):
        self.assertEqual(q.BOM_COLUMNS, (
            "category", "folio", "designation", "catalog_or_type", "tag",
            "address", "vendor", "description", "rack", "slot"))

    def test_every_row_has_all_columns(self):
        for row in (
            q.module_bom_row(1, catalog="C", vendor="V", description="D",
                             rack="0", slot="2"),
            q.device_bom_row(1, designation="-K1.1", type_id="relay_coil",
                             description="Coil", tag="T", address="Q0.0"),
            q.generic_bom_row(1, tag="T", address="I0.0"),
        ):
            self.assertEqual(tuple(row.keys()), q.BOM_COLUMNS)

    def test_module_row_fill_and_blank_pattern(self):
        row = q.module_bom_row(7, catalog="1756-IB16", vendor="AB",
                               description="16pt DI", rack="0", slot="3")
        self.assertEqual(row["category"], "module")
        self.assertEqual(row["folio"], "7")
        self.assertEqual(row["catalog_or_type"], "1756-IB16")
        self.assertEqual(row["vendor"], "AB")
        self.assertEqual(row["description"], "16pt DI")
        self.assertEqual(row["rack"], "0")
        self.assertEqual(row["slot"], "3")
        # blanks: a card is not a device
        self.assertEqual(row["designation"], "")
        self.assertEqual(row["tag"], "")
        self.assertEqual(row["address"], "")

    def test_device_row_fill_and_blank_pattern(self):
        row = q.device_bom_row(3, designation="-K3.1", type_id="solenoid_valve",
                               description="Solenoid valve", tag="SV1",
                               address="Q0.0")
        self.assertEqual(row["category"], "device")
        self.assertEqual(row["folio"], "3")
        self.assertEqual(row["designation"], "-K3.1")
        self.assertEqual(row["catalog_or_type"], "solenoid_valve")
        self.assertEqual(row["description"], "Solenoid valve")
        self.assertEqual(row["tag"], "SV1")
        self.assertEqual(row["address"], "Q0.0")
        # blanks: device has no card vendor/rack/slot
        self.assertEqual(row["vendor"], "")
        self.assertEqual(row["rack"], "")
        self.assertEqual(row["slot"], "")

    def test_generic_row_leaves_designation_and_type_blank(self):
        """Guardrail: an unmatched point is never given a device identity."""
        row = q.generic_bom_row(2, tag="CORTE", address="I0.3")
        self.assertEqual(row["category"], "generic")
        self.assertEqual(row["folio"], "2")
        self.assertEqual(row["tag"], "CORTE")
        self.assertEqual(row["address"], "I0.3")
        self.assertEqual(row["designation"], "")
        self.assertEqual(row["catalog_or_type"], "")
        self.assertEqual(row["vendor"], "")
        self.assertEqual(row["description"], "")
        self.assertEqual(row["rack"], "")
        self.assertEqual(row["slot"], "")

    def test_folio_is_coerced_to_string(self):
        self.assertEqual(q.generic_bom_row(5, tag="T", address="I0.0")["folio"],
                         "5")

    def test_deterministic_repeat(self):
        def build():
            rows = []
            rows.append(q.module_bom_row(1, catalog="C", vendor="V",
                                         description="D", rack="0", slot="2"))
            rows.append(q.device_bom_row(1, designation="-K1.1",
                                         type_id="relay_coil", description="Coil",
                                         tag="T", address="Q0.0"))
            rows.append(q.generic_bom_row(1, tag="G", address="I0.0"))
            return rows
        self.assertEqual(build(), build())


class BuildFolioBomAccumulatorTest(unittest.TestCase):
    """Integration: build_folio must populate the accumulator during its single
    traversal with the right categories/columns, and an analog point must land
    in generic with a blank designation."""

    @staticmethod
    def _run(pts, *, order=1, catalog="FAKE-NODB"):
        symbols = q.load_symbol_db()
        mod = SimpleNamespace(rack=0, slot=2, name="CARD", catalog=catalog,
                              kind="DI", points=16, in_byte_base=0,
                              out_byte_base=0, an_in_word_base=0,
                              an_out_word_base=0)
        project = ET.Element("project")
        rows = []
        q.build_folio(project, order, mod, pts, symbols, {}, {},
                      wire_scheme="address", wire_counters={}, bom_rows=rows)
        return rows

    def _pt(self, mod, index, tag, direction="I", analog=False):
        return SimpleNamespace(module=mod, index=index, tag=tag,
                               direction=direction, description="", analog=analog)

    def test_module_row_emitted_first(self):
        mod = SimpleNamespace(rack=0, slot=2, name="CARD", catalog="FAKE-NODB",
                              kind="DI", points=16, in_byte_base=0,
                              out_byte_base=0, an_in_word_base=0,
                              an_out_word_base=0)
        rows = self._run([self._pt(mod, 0, "LS1")])
        self.assertEqual(rows[0]["category"], "module")
        self.assertEqual(rows[0]["catalog_or_type"], "FAKE-NODB")
        self.assertEqual(rows[0]["rack"], "0")
        self.assertEqual(rows[0]["slot"], "2")

    def test_matched_point_makes_device_row_with_emitted_designation(self):
        mod = SimpleNamespace(rack=0, slot=2, name="CARD", catalog="FAKE-NODB",
                              kind="DI", points=16, in_byte_base=0,
                              out_byte_base=0, an_in_word_base=0,
                              an_out_word_base=0)
        rows = self._run([self._pt(mod, 0, "LS1")])
        dev = [r for r in rows if r["category"] == "device"]
        self.assertEqual(len(dev), 1)
        # limit_switch -> dt "S" on page 1 -> -S1.1; designation is non-blank
        # and matches what the symbol was actually labelled with.
        self.assertTrue(dev[0]["designation"])
        self.assertEqual(dev[0]["catalog_or_type"], "limit_switch")
        self.assertEqual(dev[0]["tag"], "LS1")
        self.assertEqual(dev[0]["address"], "I0.0")
        self.assertEqual(dev[0]["vendor"], "")

    def test_unmatched_point_makes_generic_row(self):
        mod = SimpleNamespace(rack=0, slot=2, name="CARD", catalog="FAKE-NODB",
                              kind="DI", points=16, in_byte_base=0,
                              out_byte_base=0, an_in_word_base=0,
                              an_out_word_base=0)
        rows = self._run([self._pt(mod, 0, "ZZZ_NOMATCH_XYZ")])
        gen = [r for r in rows if r["category"] == "generic"]
        self.assertEqual(len(gen), 1)
        self.assertEqual(gen[0]["designation"], "")
        self.assertEqual(gen[0]["catalog_or_type"], "")
        self.assertEqual(gen[0]["tag"], "ZZZ_NOMATCH_XYZ")

    def test_analog_point_always_goes_generic(self):
        """Even a tag that would fuzzy-match must stay generic when analog."""
        mod = SimpleNamespace(rack=0, slot=2, name="AICARD", catalog="FAKE-NODB",
                              kind="AI", points=8, in_byte_base=0,
                              out_byte_base=0, an_in_word_base=0,
                              an_out_word_base=0)
        rows = self._run([self._pt(mod, 0, "LS1", analog=True)])
        cats = [r["category"] for r in rows]
        self.assertNotIn("device", cats)
        gen = [r for r in rows if r["category"] == "generic"]
        self.assertEqual(len(gen), 1)
        self.assertEqual(gen[0]["designation"], "")
        self.assertEqual(gen[0]["catalog_or_type"], "")

    def test_accumulator_is_deterministic(self):
        mod = SimpleNamespace(rack=0, slot=2, name="CARD", catalog="FAKE-NODB",
                              kind="DI", points=16, in_byte_base=0,
                              out_byte_base=0, an_in_word_base=0,
                              an_out_word_base=0)
        pts = lambda: [self._pt(mod, 0, "LS1"), self._pt(mod, 1, "PB1"),
                       self._pt(mod, 2, "ZZZ_NOMATCH")]
        self.assertEqual(self._run(pts()), self._run(pts()))


class BomCsvTest(unittest.TestCase):
    def test_csv_header_and_blank_fields(self):
        rows = [
            q.module_bom_row(1, catalog="1756-IA16", vendor="AB",
                             description="DI", rack="0", slot="2"),
            q.device_bom_row(1, designation="-S1.1", type_id="limit_switch",
                             description="Limit switch", tag="LS1",
                             address="I0.0"),
            q.generic_bom_row(1, tag="ZZZ", address="I0.3"),
        ]
        buf = io.StringIO()
        # mirror write_bom_csv but to a buffer (no temp file needed)
        w = csv.DictWriter(buf, fieldnames=list(q.BOM_COLUMNS))
        w.writeheader()
        for r in rows:
            w.writerow(r)
        lines = buf.getvalue().splitlines()
        self.assertEqual(lines[0], ",".join(q.BOM_COLUMNS))
        parsed = list(csv.DictReader(io.StringIO(buf.getvalue())))
        self.assertEqual(parsed[0]["designation"], "")   # module blank
        self.assertEqual(parsed[0]["catalog_or_type"], "1756-IA16")
        self.assertEqual(parsed[1]["designation"], "-S1.1")
        self.assertEqual(parsed[2]["catalog_or_type"], "")  # generic blank


class SummaryFolioTest(unittest.TestCase):
    @staticmethod
    def _rows(n):
        return [q.generic_bom_row(1, tag=f"T{i}", address=f"I0.{i}")
                for i in range(n)]

    def test_summary_appended_after_drawing_folios_in_order(self):
        project = ET.Element("project")
        # two pretend drawing folios at orders 1,2
        for o in (1, 2):
            ET.SubElement(project, "diagram", {"order": str(o), "title": f"d{o}"})
        rows = self._rows(3)
        added = q.build_summary_folios(project, 3, rows)
        self.assertEqual(added, 1)
        diags = project.findall("diagram")
        self.assertEqual(diags[-1].get("order"), "3")

    def test_summary_contains_only_text_and_shapes(self):
        project = ET.Element("project")
        q.build_summary_folios(project, 1, self._rows(5))
        d = project.find("diagram")
        self.assertEqual(len(d.find("elements").findall("element")), 0)
        self.assertEqual(len(d.find("conductors").findall("conductor")), 0)
        self.assertGreater(len(d.find("inputs").findall("input")), 0)

    def test_pagination_splits_large_row_set(self):
        n = q.SUMMARY_ROWS_PER_PAGE * 2 + 1   # forces 3 pages
        project = ET.Element("project")
        added = q.build_summary_folios(project, 1, self._rows(n))
        self.assertEqual(added, 3)
        self.assertEqual(len(project.findall("diagram")), 3)

    def test_no_row_drawn_past_page_bottom(self):
        project = ET.Element("project")
        q.build_summary_folios(project, 1, self._rows(q.SUMMARY_ROWS_PER_PAGE))
        d = project.find("diagram")
        ys = [int(inp.get("y")) for inp in d.find("inputs").findall("input")]
        self.assertTrue(all(y < q.SUMMARY_HEIGHT for y in ys))

    def test_empty_rows_appends_nothing(self):
        project = ET.Element("project")
        self.assertEqual(q.build_summary_folios(project, 1, []), 0)
        self.assertEqual(len(project.findall("diagram")), 0)


class CsvInjectionGuardTest(unittest.TestCase):
    """The CSV sidecar must not let a formula-leading value (every device
    designation starts with '-') be misread as a formula by a spreadsheet."""

    def test_risky_leading_chars_are_guarded(self):
        for v in ("-S1.1", "=cmd", "+1", "@x"):
            self.assertEqual(q._csv_safe(v), "'" + v)

    def test_safe_values_pass_through(self):
        for v in ("I0.0", "LS1", "1756-IA16", "", "Limit switch", "W3.1"):
            self.assertEqual(q._csv_safe(v), v)

    def test_write_bom_csv_guards_designation_but_leaves_row_dict_raw(self):
        rows = [
            q.module_bom_row(1, catalog="1756-IA16", vendor="AB",
                             description="DI", rack="0", slot="2"),
            q.device_bom_row(3, designation="-S1.1", type_id="limit_switch",
                             description="Limit switch", tag="LS1",
                             address="I0.0"),
        ]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x_bom.csv"
            q.write_bom_csv(p, rows)
            parsed = list(csv.DictReader(p.open(encoding="utf-8")))
        # the CSV cell is guarded so a spreadsheet treats it as text...
        self.assertEqual(parsed[1]["designation"], "'-S1.1")
        # ...while the source row dict (used for the .qet label) is untouched.
        self.assertEqual(rows[1]["designation"], "-S1.1")
        # a non-risky catalog stays verbatim
        self.assertEqual(parsed[0]["catalog_or_type"], "1756-IA16")


class EllipsizeTest(unittest.TestCase):
    def test_short_value_unchanged(self):
        self.assertEqual(q._ellipsize("abc", 5), "abc")

    def test_exact_length_unchanged(self):
        self.assertEqual(q._ellipsize("abcd", 4), "abcd")

    def test_truncates_with_single_ellipsis(self):
        out = q._ellipsize("abcdef", 4)
        self.assertEqual(out, "abc…")
        self.assertEqual(len(out), 4)

    def test_blank_or_zero_width_returns_input(self):
        self.assertEqual(q._ellipsize("", 5), "")
        self.assertEqual(q._ellipsize("abc", 0), "abc")


class SummaryFolioSubsetTest(unittest.TestCase):
    """The summary folio renders only the legible subset and ellipsizes wide
    cells, while staying inside the page frame."""

    @staticmethod
    def _diagram(rows):
        project = ET.Element("project")
        q.build_summary_folios(project, 1, rows)
        return project.find("diagram")

    def _texts(self, diagram):
        return [inp.get("text")
                for inp in diagram.find("inputs").findall("input")]

    def test_only_subset_columns_rendered_not_vendor_rack_slot(self):
        rows = [q.module_bom_row(
            1, catalog="1756-IA16",
            vendor="Allen-Bradley (Rockwell Automation)",
            description="16-pt DI", rack="0", slot="2")]
        texts = self._texts(self._diagram(rows))
        self.assertIn("1756-IA16", texts)                 # catalog_or_type shown
        self.assertIn("TYPE", texts)                      # relabelled header
        # vendor is in the CSV but NOT on the folio subset
        self.assertNotIn("Allen-Bradley (Rockwell Automation)", texts)
        self.assertNotIn("VENDOR", texts)
        self.assertNotIn("RACK", texts)
        self.assertNotIn("SLOT", texts)

    def test_long_description_is_ellipsized(self):
        rows = [q.device_bom_row(1, designation="-K1.1", type_id="relay_coil",
                                 description="X" * 200, tag="T", address="Q0.0")]
        shown = [t for t in self._texts(self._diagram(rows))
                 if t and t.startswith("X")]
        self.assertTrue(shown)
        self.assertTrue(all(len(t) <= 88 for t in shown))
        self.assertTrue(any(t.endswith("…") for t in shown))

    def test_no_header_rule_drawn(self):
        # the header rule struck through the column-header text, so it was
        # removed (DA.8 review fix): the summary folio draws no shape.
        diagram = self._diagram([q.generic_bom_row(1, tag="T", address="I0.0")])
        self.assertEqual(len(diagram.find("shapes").findall("shape")), 0)

    def test_all_text_starts_inside_page_width(self):
        rows = [q.device_bom_row(1, designation="-K1.1", type_id="solenoid_valve",
                                 description="d", tag="LONG_TAG_NAME_123456789",
                                 address="Q0.0")]
        diagram = self._diagram(rows)
        xs = [int(inp.get("x"))
              for inp in diagram.find("inputs").findall("input")]
        self.assertTrue(all(x < q.SUMMARY_PAGE_WIDTH for x in xs))


class SummaryPageFitTest(unittest.TestCase):
    """The derived rows-per-page must keep the last data row inside the frame."""

    def test_rows_per_page_positive(self):
        self.assertGreater(q.SUMMARY_ROWS_PER_PAGE, 0)

    def test_last_row_stays_within_frame(self):
        last_y = (q.SUMMARY_ROW_Y0
                  + q.SUMMARY_ROWS_PER_PAGE * q.SUMMARY_ROW_DY)
        self.assertLessEqual(last_y, q.SUMMARY_HEIGHT)


class DrawingFolioUnchangedTest(unittest.TestCase):
    """Central safety claim: passing a bom_rows accumulator must NOT alter the
    emitted drawing-folio XML. Build the same folio with and without the
    accumulator, normalize the per-element random uuids, assert byte equality."""

    @staticmethod
    def _normalized_xml(with_accumulator):
        symbols = q.load_symbol_db()
        mod = SimpleNamespace(rack=0, slot=2, name="CARD", catalog="FAKE-NODB",
                              kind="DI", points=16, in_byte_base=0,
                              out_byte_base=0, an_in_word_base=0,
                              an_out_word_base=0)
        pts = [SimpleNamespace(module=mod, index=i, tag=tag, direction="I",
                               description="", analog=False)
               for i, tag in ((0, "LS1"), (1, "PB1"), (2, "ZZZ_NOMATCH"))]
        project = ET.Element("project")
        rows = [] if with_accumulator else None
        q.build_folio(project, 3, mod, pts, symbols, {}, {},
                      wire_scheme="address", wire_counters={}, bom_rows=rows)
        xml = ET.tostring(project.find("diagram"), encoding="unicode")
        # uuid4() per-element randomness is pre-existing nondeterminism
        return re.sub(r'uuid="\{[^}]*\}"', 'uuid="{}"', xml)

    def test_accumulator_does_not_change_drawing_folio_xml(self):
        self.assertEqual(self._normalized_xml(False),
                         self._normalized_xml(True))


class LoadProjectTemplateTest(unittest.TestCase):
    """The cajetín config loader mirrors load_module_db: graceful defaults when
    absent/malformed, string-only merge of known keys when present."""

    def _write(self, d, text):
        p = Path(d) / "project_template.json"
        p.write_text(text, encoding="utf-8")
        return p

    def test_absent_file_returns_all_blank_defaults(self):
        tmpl = q.load_project_template(Path("does-not-exist-anywhere.json"))
        # every documented field present and blank (title block never KeyErrors)
        for k, v in q.PROJECT_TEMPLATE_DEFAULTS.items():
            self.assertEqual(tmpl[k], v)
        # plus an empty revisions list for the changelog
        self.assertEqual(tmpl["revisions"], [])

    def test_malformed_file_degrades_to_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, "{ this is not json")
            tmpl = q.load_project_template(p)
            for k, v in q.PROJECT_TEMPLATE_DEFAULTS.items():
                self.assertEqual(tmpl[k], v)
            self.assertEqual(tmpl["revisions"], [])

    def test_revisions_list_is_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, '{"revisions": [{"rev": "00", '
                               '"description": "Primera emisión"}]}')
            tmpl = q.load_project_template(p)
            self.assertEqual(len(tmpl["revisions"]), 1)
            self.assertEqual(tmpl["revisions"][0]["rev"], "00")

    def test_revisions_non_list_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, '{"revisions": "oops"}')
            self.assertEqual(q.load_project_template(p)["revisions"], [])

    def test_present_file_merges_string_values(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, '{"company": "Exxerpro Solutions", '
                               '"drawn_by": "ES", "revision": "00"}')
            tmpl = q.load_project_template(p)
            self.assertEqual(tmpl["company"], "Exxerpro Solutions")
            self.assertEqual(tmpl["drawn_by"], "ES")
            self.assertEqual(tmpl["revision"], "00")
            # a field not in the file keeps its blank default
            self.assertEqual(tmpl["client"], "")

    def test_unknown_keys_ignored_and_nonstring_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, '{"company": "X", "bogus": "Y", "revision": 7}')
            tmpl = q.load_project_template(p)
            self.assertNotIn("bogus", tmpl)
            self.assertEqual(tmpl["company"], "X")
            # a non-string value is ignored, keeping the blank default
            self.assertEqual(tmpl["revision"], "")

    def test_shipped_template_loads(self):
        """The committed src/project_template.json must be valid and carry the
        Exxerpro company name (the configured default for this repo)."""
        tmpl = q.load_project_template()   # default path
        self.assertEqual(tmpl["company"], "Exxerpro Solutions")


class ResolveTitleBlockFieldsTest(unittest.TestCase):
    def test_blank_project_and_machine_fall_back_to_controller(self):
        fields = q.resolve_title_block_fields(
            dict(q.PROJECT_TEMPLATE_DEFAULTS), "WADDING_1")
        self.assertEqual(fields["project"], "WADDING_1 I/O")
        self.assertEqual(fields["machine"], "WADDING_1")

    def test_explicit_project_and_machine_kept(self):
        tmpl = dict(q.PROJECT_TEMPLATE_DEFAULTS)
        tmpl["project"] = "Line 4 Retrofit"
        tmpl["machine"] = "Palletizer A"
        fields = q.resolve_title_block_fields(tmpl, "WADDING_1")
        self.assertEqual(fields["project"], "Line 4 Retrofit")
        self.assertEqual(fields["machine"], "Palletizer A")


class YyyymmddTest(unittest.TestCase):
    def test_iso_date_becomes_compact(self):
        self.assertEqual(q._yyyymmdd("2026-06-13"), "20260613")

    def test_blank_or_malformed_becomes_null(self):
        for bad in ("", None, "2026-6", "garbage", "2026/06/13/1"):
            self.assertEqual(q._yyyymmdd(bad), "null")

    def test_already_compact_passes(self):
        self.assertEqual(q._yyyymmdd("20260613"), "20260613")


class TitleblockPropertiesTest(unittest.TestCase):
    """Pure mapping of our config onto the ISO 7200 custom %{token} property
    names (pure ISO 7200 — no client/revised cells), dropping blanks."""

    def test_maps_config_to_iso7200_tokens(self):
        fields = q.resolve_title_block_fields({
            **q.PROJECT_TEMPLATE_DEFAULTS,
            "company": "Exxerpro Solutions", "drawing_number": "PL-001",
            "revision": "00", "approved_by": "JD"}, "WADDING_1")
        props = q.titleblock_properties(fields)
        self.assertEqual(props["owner"], "Exxerpro Solutions")   # EMPRESA
        self.assertEqual(props["name"], "WADDING_1 I/O")         # drawing name
        self.assertEqual(props["ref"], "PL-001")                 # PLANO N.º
        self.assertEqual(props["rev"], "00")                     # REV
        self.assertEqual(props["approval"], "JD")                # APROBÓ

    def test_blank_fields_are_dropped_not_emitted_empty(self):
        props = q.titleblock_properties(
            q.resolve_title_block_fields(dict(q.PROJECT_TEMPLATE_DEFAULTS), "C"))
        # no company/drawing_number/revision/approved -> those keys absent
        for absent in ("owner", "ref", "rev", "approval"):
            self.assertNotIn(absent, props)
        # 'name' is present because project/machine fall back to the controller
        self.assertEqual(props["name"], "C I/O")

    def test_no_builtin_tokens_in_properties(self):
        """author/title/date/filename/folio come from diagram attrs or QET, so
        they must never be emitted as custom properties (would double up)."""
        props = q.titleblock_properties(q.resolve_title_block_fields(
            {**q.PROJECT_TEMPLATE_DEFAULTS, "drawn_by": "ES",
             "date": "2026-06-13"}, "C"))
        for builtin in ("author", "title", "date", "filename",
                        "folio-id", "folio-total"):
            self.assertNotIn(builtin, props)


class TitleblockCustomTokensTest(unittest.TestCase):
    def test_extracts_customs_and_excludes_builtins(self):
        tpl = ('<x><value>%{owner}</value><value>%{author}</value>'
               '<value>%{ref}</value><value>%{folio-id}/%{folio-total}</value>'
               '<value>%{owner}</value></x>')   # owner repeated
        toks = q.titleblock_custom_tokens(tpl)
        self.assertIn("owner", toks)
        self.assertIn("ref", toks)
        self.assertEqual(toks.count("owner"), 1)            # de-duplicated
        for builtin in ("author", "folio-id", "folio-total"):
            self.assertNotIn(builtin, toks)

    def test_real_template_tokens(self):
        toks = q.titleblock_custom_tokens(q.load_titleblock_template())
        # the ISO 7200 customs we must fill/blank
        for t in ("department", "ref", "owner", "type", "status", "code",
                  "name", "rev", "country", "approval"):
            self.assertIn(t, toks)
        # built-ins must be excluded
        for b in ("author", "title", "date", "filename", "folio-id"):
            self.assertNotIn(b, toks)


class SectionizeTitleblockPageTest(unittest.TestCase):
    """DA.5b: the page-number field is rewritten to the custom %{page} token so
    the cajetín shows the SECTION page instead of QET's position counter."""

    def test_replaces_folio_id_total_pair(self):
        self.assertEqual(
            q.sectionize_titleblock_page("<v>%{folio-id}/%{folio-total}</v>"),
            "<v>%{page}</v>")

    def test_replaces_bare_folio_id(self):
        self.assertEqual(q.sectionize_titleblock_page("x %{folio-id} y"),
                         "x %{page} y")

    def test_none_is_noop(self):
        self.assertIsNone(q.sectionize_titleblock_page(None))

    def test_template_without_token_unchanged(self):
        self.assertEqual(q.sectionize_titleblock_page("<v>%{ref}</v>"),
                         "<v>%{ref}</v>")

    def test_real_template_gains_page_loses_folio_id(self):
        out = q.sectionize_titleblock_page(q.load_titleblock_template())
        self.assertIn("%{page}", out)
        self.assertNotIn("%{folio-id}", out)
        # %{page} is now a CUSTOM token, so apply_titleblock will fill it
        self.assertIn("page", q.titleblock_custom_tokens(out))


class ApplyTitleblockTest(unittest.TestCase):
    @staticmethod
    def _diagram():
        symbols = q.load_symbol_db()
        mod = SimpleNamespace(rack=1, slot=2, name="CARD", catalog="FAKE-NODB",
                              kind="DI", points=16, in_byte_base=0,
                              out_byte_base=0, an_in_word_base=0,
                              an_out_word_base=0)
        pts = [SimpleNamespace(module=mod, index=0, tag="LS1", direction="I",
                               description="", analog=False)]
        project = ET.Element("project")
        q.build_folio(project, 1, mod, pts, symbols, {}, {},
                      wire_scheme="address", wire_counters={})
        return project.find("diagram")

    def _fields(self):
        return q.resolve_title_block_fields({
            **q.PROJECT_TEMPLATE_DEFAULTS, "company": "Exxerpro Solutions",
            "drawn_by": "ES", "date": "2026-06-13", "revision": "00"}, "WADDING_1")

    # the ISO 7200 custom fields the real template carries
    TOKENS = ["department", "ref", "approval", "owner", "type", "status",
              "code", "name", "rev", "country"]

    def test_sets_native_titleblock_attributes(self):
        d = self._diagram()
        q.apply_titleblock(d, self._fields(), self.TOKENS, filename="WADDING_1")
        self.assertEqual(d.get("titleblocktemplate"), "exxerpro")
        self.assertEqual(d.get("titleblocktemplateCollection"), "embedded")
        self.assertEqual(d.get("displayAt"), "bottom")
        self.assertEqual(d.get("date"), "20260613")     # static, compact
        self.assertEqual(d.get("author"), "ES")
        self.assertEqual(d.get("filename"), "WADDING_1")

    def test_properties_block_is_first_child_with_values(self):
        d = self._diagram()
        q.apply_titleblock(d, self._fields(), self.TOKENS)
        self.assertEqual(list(d)[0].tag, "properties")   # QET writes it first
        props = {p.get("name"): p.text for p in d.find("properties")}
        self.assertEqual(props["owner"], "Exxerpro Solutions")
        self.assertEqual(props["rev"], "00")
        self.assertEqual(props["name"], "WADDING_1 I/O")

    def test_every_custom_token_gets_a_property_blank_if_no_data(self):
        """The placeholder-leak fix: EVERY custom token must get a <property>
        (empty when we have no value) so QET never renders the raw %{token}."""
        d = self._diagram()
        q.apply_titleblock(d, self._fields(), self.TOKENS)
        props = {p.get("name"): (p.text or "") for p in d.find("properties")}
        # every template token is present...
        self.assertEqual(set(props), set(self.TOKENS))
        # ...unfilled ones are empty strings, not missing / not a raw token
        for blank in ("department", "type", "status", "code", "country", "ref"):
            self.assertEqual(props[blank], "")

    def test_page_token_renders_zero_padded_section_order(self):
        # DA.5b: with %{page} among the custom tokens, the property is the
        # diagram's order zero-padded to 3 digits (here the folio was built at
        # order 1 → "001").
        d = self._diagram()
        q.apply_titleblock(d, self._fields(), self.TOKENS + [q.PAGE_TOKEN])
        props = {p.get("name"): p.text for p in d.find("properties")}
        self.assertEqual(props[q.PAGE_TOKEN], "001")

    def test_does_not_touch_electrical_content(self):
        """The title block must not alter <elements>/<conductors> — it only adds
        attributes + a <properties> block."""
        d = self._diagram()
        before_el = ET.tostring(d.find("elements"))
        before_co = ET.tostring(d.find("conductors"))
        q.apply_titleblock(d, self._fields(), self.TOKENS)
        self.assertEqual(ET.tostring(d.find("elements")), before_el)
        self.assertEqual(ET.tostring(d.find("conductors")), before_co)


class LoadTitleblockTemplateTest(unittest.TestCase):
    def test_shipped_template_loads_as_text(self):
        text = q.load_titleblock_template()      # default assets/exxerpro.titleblock
        self.assertIsNotNone(text)
        self.assertIn('<titleblocktemplate name="exxerpro"', text)
        self.assertIn("<svg", text)              # the embedded SVG logo

    def test_absent_file_returns_none(self):
        self.assertIsNone(q.load_titleblock_template(
            Path("nope-no-such.titleblock")))

    def test_malformed_file_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.titleblock"
            p.write_text("<titleblocktemplate><not closed", encoding="utf-8")
            self.assertIsNone(q.load_titleblock_template(p))


class AttachTitleblocksTest(unittest.TestCase):
    @staticmethod
    def _project():
        symbols = q.load_symbol_db()
        mod = SimpleNamespace(rack=1, slot=2, name="CARD", catalog="FAKE-NODB",
                              kind="DI", points=16, in_byte_base=0,
                              out_byte_base=0, an_in_word_base=0,
                              an_out_word_base=0)
        pts = [SimpleNamespace(module=mod, index=0, tag="LS1", direction="I",
                               description="", analog=False)]
        project = ET.Element("project")
        bom = []
        q.build_folio(project, 1, mod, pts, symbols, {}, {},
                      wire_scheme="address", wire_counters={}, bom_rows=bom)
        q.build_summary_folios(project, 2, bom)
        return project

    def _fields(self):
        return q.resolve_title_block_fields(
            {**q.PROJECT_TEMPLATE_DEFAULTS, "company": "Exxerpro Solutions"},
            "WADDING_1")

    def test_references_template_from_every_folio(self):
        project = self._project()
        template = q.load_titleblock_template()
        n = q.attach_titleblocks(project, self._fields(), template)
        diagrams = project.findall("diagram")
        self.assertEqual(n, len(diagrams))
        for d in diagrams:                       # drawing AND summary folios
            self.assertEqual(d.get("titleblocktemplate"), "exxerpro")
            self.assertIsNotNone(d.find("properties"))

    def test_unavailable_template_is_a_clean_noop(self):
        project = self._project()
        n = q.attach_titleblocks(project, self._fields(), None)
        self.assertEqual(n, 0)
        for d in project.findall("diagram"):
            self.assertIsNone(d.get("titleblocktemplate"))

    def test_folio_geometry_unchanged_no_reserved_band(self):
        """Native QET draws the block, so the folio keeps height 660 / rows 8 —
        we did NOT reserve a band (the previous hand-drawn approach did)."""
        d = self._project().find("diagram")
        self.assertEqual(d.get("height"), "660")
        self.assertEqual(d.get("rows"), "8")


class EmbedTitleblockTemplatesTest(unittest.TestCase):
    def test_template_injected_verbatim_after_project_tag(self):
        xml = '<?xml version="1.0" ?>\n<project title="t" version="0.80">\n<diagram/>\n</project>\n'
        template = '<titleblocktemplate name="exxerpro"><logos/></titleblocktemplate>\n'
        out = q.embed_titleblock_templates(xml, template)
        self.assertIn("<titleblocktemplates>", out)
        # verbatim (not reserialized): the exact template text survives
        self.assertIn('<titleblocktemplate name="exxerpro"><logos/></titleblocktemplate>',
                      out)
        # injected before the first diagram, after the project open tag
        self.assertLess(out.index("<titleblocktemplates>"), out.index("<diagram/>"))

    def test_real_template_svg_survives_byte_for_byte(self):
        """The whole point of text injection: the embedded SVG logo is NOT
        round-tripped through ElementTree (which would rewrite ns prefixes)."""
        template = q.load_titleblock_template()
        xml = '<project version="0.80">\n<diagram/>\n</project>\n'
        out = q.embed_titleblock_templates(xml, template)
        self.assertIn(template, out)             # exact substring -> verbatim


class NormalizeRevisionsTest(unittest.TestCase):
    def _fields(self, **over):
        return {**q.PROJECT_TEMPLATE_DEFAULTS, **over}

    def test_configured_revisions_are_used_and_coerced(self):
        revs = q.normalize_revisions(
            [{"rev": "00", "date": "2026-06-13", "description": "Primera emisión",
              "drawn": "ES", "approved": "JD"},
             {"rev": "01", "description": "Cambio de bornero"}],   # missing keys
            self._fields())
        self.assertEqual(len(revs), 2)
        self.assertEqual(revs[0]["approved"], "JD")
        # missing keys are blank, never fabricated
        self.assertEqual(revs[1]["date"], "")
        self.assertEqual(revs[1]["drawn"], "")
        self.assertEqual(revs[1]["description"], "Cambio de bornero")
        # every row has exactly the revision columns
        for r in revs:
            self.assertEqual(set(r), set(q.REVISION_COLUMNS))

    def test_no_config_synthesises_first_emission_from_fields(self):
        revs = q.normalize_revisions(
            None, self._fields(revision="00", date="2026-06-13", drawn_by="ES"))
        self.assertEqual(len(revs), 1)
        self.assertEqual(revs[0]["rev"], "00")
        self.assertEqual(revs[0]["date"], "2026-06-13")
        self.assertEqual(revs[0]["drawn"], "ES")
        self.assertEqual(revs[0]["description"], "Primera emisión")

    def test_empty_list_also_synthesises(self):
        self.assertEqual(len(q.normalize_revisions([], self._fields())), 1)

    def test_deterministic(self):
        f = self._fields(revision="00", date="2026-06-13")
        self.assertEqual(q.normalize_revisions(None, f),
                         q.normalize_revisions(None, f))


class ChangelogFolioTest(unittest.TestCase):
    @staticmethod
    def _rows(n):
        return [{"rev": f"{i:02d}", "date": "2026-06-13",
                 "description": f"cambio {i}", "drawn": "ES", "approved": ""}
                for i in range(n)]

    def test_emits_a_folio_with_only_text_and_shapes(self):
        project = ET.Element("project")
        n = q.build_changelog_folios(project, 1, self._rows(2))
        self.assertEqual(n, 1)
        d = project.find("diagram")
        self.assertEqual(len(d.find("elements").findall("element")), 0)
        self.assertEqual(len(d.find("conductors").findall("conductor")), 0)
        texts = [i.get("text") for i in d.find("inputs").findall("input")]
        for label in ("REV", "FECHA", "DESCRIPCIÓN", "DIBUJÓ", "APROBÓ"):
            self.assertIn(label, texts)
        self.assertIn("cambio 0", texts)

    def test_empty_revisions_appends_nothing(self):
        project = ET.Element("project")
        self.assertEqual(q.build_changelog_folios(project, 1, []), 0)
        self.assertEqual(len(project.findall("diagram")), 0)

    def test_title_is_changelog_not_bom(self):
        project = ET.Element("project")
        q.build_changelog_folios(project, 5, self._rows(1))
        d = project.find("diagram")
        self.assertEqual(d.get("order"), "5")
        self.assertIn("revisiones", d.get("title").lower())


class ParsePowerBlockTest(unittest.TestCase):
    """Pure helper: parsing the optional module_db 'power' block returns the
    groups (points / supply / common potentials / TBD pins), and degrades to NO
    power groups on missing/malformed input. Mirrors the wiring pattern."""

    def test_well_formed_block_returns_groups_with_potentials_and_pins(self):
        power = {"type": "AC", "groups": [
            {"points": [0, 1, 2, 3, 4, 5, 6, 7], "supply": "L1", "common": "N",
             "supply_pin": "TBD", "common_pin": "TBD"},
            {"points": [8, 9, 10, 11, 12, 13, 14, 15], "supply": "L1",
             "common": "N", "supply_pin": "TBD", "common_pin": "TBD"},
        ]}
        groups = q.parse_power_block(power)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["points"], [0, 1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(groups[0]["supply"], "L1")
        self.assertEqual(groups[0]["common"], "N")
        self.assertEqual(groups[0]["supply_pin"], "TBD")
        self.assertEqual(groups[0]["common_pin"], "TBD")
        self.assertEqual(groups[1]["points"][0], 8)

    def test_missing_block_yields_no_groups(self):
        self.assertEqual(q.parse_power_block(None), [])

    def test_non_dict_block_yields_no_groups(self):
        for bad in ("power", 7, ["groups"], True):
            self.assertEqual(q.parse_power_block(bad), [])

    def test_missing_or_bad_groups_yields_no_groups(self):
        self.assertEqual(q.parse_power_block({"type": "AC"}), [])
        self.assertEqual(q.parse_power_block({"groups": "oops"}), [])
        self.assertEqual(q.parse_power_block({"groups": []}), [])

    def test_group_without_points_is_dropped(self):
        power = {"groups": [
            {"supply": "L1", "common": "N"},            # no points key
            {"points": [], "supply": "L1"},             # empty points
            {"points": "x", "supply": "L1"},            # points not a list
            {"points": [0, 1], "supply": "L+", "common": "0V"},  # valid
        ]}
        groups = q.parse_power_block(power)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["points"], [0, 1])
        self.assertEqual(groups[0]["supply"], "L+")

    def test_non_int_points_are_filtered(self):
        groups = q.parse_power_block(
            {"groups": [{"points": [0, "1", 2, None, 3, True], "supply": "L1"}]})
        # str/None/bool dropped; ints kept (True is a bool, excluded)
        self.assertEqual(groups[0]["points"], [0, 2, 3])

    def test_missing_pins_default_to_tbd(self):
        groups = q.parse_power_block(
            {"groups": [{"points": [0], "supply": "L1", "common": "N"}]})
        self.assertEqual(groups[0]["supply_pin"], "TBD")
        self.assertEqual(groups[0]["common_pin"], "TBD")

    def test_group_with_both_potentials_blank_is_dropped(self):
        # non-string / missing supply AND common -> nothing to label or
        # reference -> the whole group is dropped (graceful, never a "?" terminal)
        self.assertEqual(
            q.parse_power_block(
                {"groups": [{"points": [0], "supply": 5, "common": None}]}),
            [])
        self.assertEqual(
            q.parse_power_block({"groups": [{"points": [0, 1]}]}), [])

    def test_group_with_one_valid_potential_survives_other_blanked(self):
        # a single usable potential keeps the group; the bad one is coerced blank
        groups = q.parse_power_block(
            {"groups": [{"points": [0], "supply": "L+", "common": None}]})
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["supply"], "L+")
        self.assertEqual(groups[0]["common"], "")

    def test_deterministic_repeat(self):
        power = {"groups": [{"points": [0, 1], "supply": "L1", "common": "N"}]}
        self.assertEqual(q.parse_power_block(power), q.parse_power_block(power))


class LoadModuleDbPowerTest(unittest.TestCase):
    """load_module_db must expose the parsed power structure as power_groups,
    and the shipped card files must be modelled per AC #5."""

    def test_oa16_has_two_groups_of_eight_with_l1_n_and_tbd_pins(self):
        db = q.load_module_db("1756-OA16")
        groups = db["power_groups"]
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["points"], list(range(0, 8)))
        self.assertEqual(groups[1]["points"], list(range(8, 16)))
        for g in groups:
            self.assertEqual(g["supply"], "L1")
            self.assertEqual(g["common"], "N")
            # GUARDRAIL: pins ship as TBD (render __)
            self.assertEqual(g["supply_pin"], "TBD")
            self.assertEqual(g["common_pin"], "TBD")

    def test_ia16_single_ac_group_l1_n(self):
        db = q.load_module_db("1756-IA16")
        groups = db["power_groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["points"], list(range(16)))
        self.assertEqual(groups[0]["supply"], "L1")
        self.assertEqual(groups[0]["common"], "N")

    def test_ib32_dc_group_uses_dc_potentials(self):
        db = q.load_module_db("1756-IB32")
        groups = db["power_groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["points"], list(range(32)))
        self.assertIn(groups[0]["supply"], ("L+", "24V"))
        self.assertIn(groups[0]["common"], ("0V", "N"))

    def test_analog_and_relay_cards_omit_power_block(self):
        """1756-IF16 / 1756-OX8I omit the block entirely (uncertain structure):
        graceful — no power groups, never invented."""
        for cat in ("1756-IF16", "1756-OX8I"):
            db = q.load_module_db(cat)
            self.assertEqual(db["power_groups"], [],
                             f"{cat} should ship no power block")

    def test_all_shipped_pins_are_tbd(self):
        """No physical power pin is ever guessed in a shipped card file."""
        import glob
        for f in glob.glob(str(q.MODULE_DB_DIR / "*.json")):
            db = q.load_module_db(Path(f).stem)
            for g in db["power_groups"]:
                self.assertEqual(g["supply_pin"], "TBD")
                self.assertEqual(g["common_pin"], "TBD")


class PowerPinLabelTest(unittest.TestCase):
    """The 'pin __' rule for power terminals: TBD (case-insensitive) and
    blank/missing render as the __ placeholder; a real pin renders verbatim."""

    def test_tbd_renders_placeholder_case_insensitive(self):
        for v in ("TBD", "tbd", " Tbd "):
            self.assertEqual(q._power_pin_label(v), q.PIN_PLACEHOLDER)

    def test_blank_or_missing_renders_placeholder(self):
        for v in ("", "   ", None, 7):
            self.assertEqual(q._power_pin_label(v), q.PIN_PLACEHOLDER)

    def test_real_pin_renders_verbatim(self):
        self.assertEqual(q._power_pin_label("34"), "34")
        self.assertEqual(q._power_pin_label(" L1-0 "), "L1-0")


class BuildFolioPowerRenderTest(unittest.TestCase):
    """Integration: build_folio draws one supply + one common terminal per power
    group (labelled with the potential, 'pin __', and a cross-reference), placed
    outside the I/O-row and card-box bounds; a card with no power block draws
    none."""

    @staticmethod
    def _diagram(catalog, npoints=16, kind="DO"):
        mod = SimpleNamespace(rack=1, slot=5, name="CARD", catalog=catalog,
                              kind=kind, points=npoints, in_byte_base=0,
                              out_byte_base=0, an_in_word_base=0,
                              an_out_word_base=0)
        pts = [SimpleNamespace(module=mod, index=0, tag="ZZZ_NOMATCH",
                               direction="O", description="", analog=False)]
        project = ET.Element("project")
        q.build_folio(project, 2, mod, pts, [], {}, {},
                      wire_scheme="address", wire_counters={})
        return project.find("diagram")

    def _texts(self, d):
        return [i.get("text") for i in d.find("inputs").findall("input")]

    def test_oa16_lists_two_supply_two_common_rows(self):
        d = self._diagram("1756-OA16")
        texts = self._texts(d)
        # 2 groups -> rows for each group's L1/N, suffixed so they stay distinct
        for label in ("L1 (G1)", "L1 (G2)", "N (G1)", "N (G2)"):
            self.assertIn(label, texts)
        # each row shows the placeholder pin (TBD -> __)
        self.assertGreaterEqual(texts.count(f"pin {q.PIN_PLACEHOLDER}"), 4)
        # the table header references the supply-rail folio
        self.assertIn(q.SUPPLY_FOLIO_TITLE.upper(), texts)

    def test_power_table_sits_top_right_clear_of_subheader(self):
        # DA.8: the power potentials moved OUT of the sub-header's lane into a
        # boxed table in the top-right corner. Every power-table label is right
        # of (and so never overprints) the sub-header.
        d = self._diagram("1756-OA16")
        inputs = d.find("inputs").findall("input")
        sub = next(i for i in inputs if " — " in (i.get("text") or ""))
        sub_x = float(sub.get("x"))
        pot_xs = [float(i.get("x")) for i in inputs
                  if (i.get("text") or "").startswith(("L1", "N"))]
        self.assertTrue(pot_xs)
        self.assertTrue(all(x >= q.POWER_TABLE_LEFT for x in pot_xs))
        self.assertGreater(min(pot_xs), sub_x)

    def test_isolated_groups_stay_distinct_and_draw_no_conductor(self):
        # OA16 has TWO groups -> each row carries a (G1)/(G2) suffix so the two
        # isolated L1/N groups stay distinguishable instead of collapsing.
        d = self._diagram("1756-OA16")
        rows = [t for t in self._texts(d)
                if t and (t.startswith("L1") or t.startswith("N"))]
        self.assertEqual(len(set(rows)), len(rows))     # all distinct
        # the power table draws NO conductor; the only conductor on this single-
        # generic-point card is the inline strip segment (the bornero feature).
        self.assertEqual(len(d.find("conductors").findall("conductor")), 1)

    def test_single_group_card_has_no_group_suffix(self):
        # IA16 is a single L1/N group -> no (G1) suffix (suffix only when >1)
        d = self._diagram("1756-IA16")
        texts = self._texts(d)
        self.assertIn("L1", texts)
        self.assertIn("N", texts)
        self.assertFalse(any(t and "(G" in t for t in texts))

    def test_no_power_block_draws_no_power_table(self):
        d = self._diagram("FAKE-NODB")
        texts = self._texts(d)
        self.assertNotIn(q.SUPPLY_FOLIO_TITLE.upper(), texts)

    def test_blank_potential_group_draws_no_question_mark_row(self):
        # a group whose supply is the only usable potential draws ONLY the supply
        # row; the blank common is skipped (no "?" row, no guessed potential)
        groups = [{"points": [0, 1], "supply": "L+", "common": "",
                   "supply_pin": "TBD", "common_pin": "TBD"}]
        inputs, shapes = ET.Element("i"), ET.Element("s")
        ys = q.add_power_terminals(inputs, shapes, groups)
        self.assertEqual(len(ys), 1)   # supply only
        texts = [i.get("text") for i in inputs.findall("input")]
        self.assertNotIn("?", texts)
        self.assertIn("L+", texts)

    def test_power_table_clears_content_on_two_column_card(self):
        """A 2-column card (IB32) shares the table's x-band with its right
        column, so the whole table (header + rows) must end ABOVE the first I/O
        row (ROW_Y0) and stay inside the page frame — asserting the FULL extent,
        not just the first row."""
        d = self._diagram("1756-IB32", npoints=32, kind="DI")
        inputs = d.find("inputs").findall("input")
        table = [i for i in inputs
                 if (i.get("text") or "").startswith(("L+", "0V"))
                 or i.get("text") == q.SUPPLY_FOLIO_TITLE.upper()]
        self.assertTrue(table)
        self.assertTrue(all(float(i.get("y")) < q.ROW_Y0 for i in table))
        self.assertTrue(all(float(i.get("x")) < q.SUMMARY_PAGE_WIDTH
                            for i in table))

    def test_power_table_right_of_content_on_single_column_card(self):
        # a 1-column card (OA16) keeps all I/O content left of the table, so the
        # table is clear regardless of how tall it grows.
        d = self._diagram("1756-OA16")
        pot_xs = [float(i.get("x")) for i in d.find("inputs").findall("input")
                  if (i.get("text") or "").startswith(("L1", "N"))]
        self.assertTrue(pot_xs)
        self.assertTrue(all(x >= q.POWER_TABLE_LEFT for x in pot_xs))

    def test_card_box_title_clears_the_box_top(self):
        # the box title sat on the box's top edge; it must now clear it with a
        # real gap (DA.8 follow-up). Check the card box only (not the top-right
        # power-table box, which is further left-excluded by x).
        d = self._diagram("1756-IA16")
        title = next(i for i in d.find("inputs").findall("input")
                     if (i.get("text") or "").startswith("-CARD"))
        title_y = float(title.get("y"))
        card_boxes = [s for s in d.find("shapes").findall("shape")
                      if float(s.get("x1")) < q.POWER_TABLE_LEFT]
        self.assertTrue(card_boxes)
        box_top = min(float(s.get("y1")) for s in card_boxes)
        self.assertGreaterEqual(box_top - title_y, 18)   # clean gap above the box

    def test_power_table_places_no_element(self):
        # the power potentials are documentation references, not wired terminals:
        # add_power_terminals writes ONLY text (inputs) + the table box (shapes)
        inputs, shapes = ET.Element("i"), ET.Element("s")
        q.add_power_terminals(inputs, shapes,
                              q.load_module_db("1756-OA16")["power_groups"])
        self.assertTrue(inputs.findall("input"))
        self.assertEqual(len(shapes.findall("shape")), 1)   # just the table box


class CollectSupplyRailsTest(unittest.TestCase):
    def test_includes_defaults_and_card_potentials(self):
        mods = [SimpleNamespace(catalog="1756-IB32"),
                SimpleNamespace(catalog="1756-OA16")]
        rails = q.collect_supply_rails(mods)
        for r in ("L1", "N", "L+", "24V", "0V", "PE"):
            self.assertIn(r, rails)
        # de-duplicated
        self.assertEqual(len(rails), len(set(rails)))

    def test_card_with_no_power_block_contributes_nothing(self):
        rails = q.collect_supply_rails([SimpleNamespace(catalog="1756-IF16")])
        self.assertEqual(rails, list(q.SUPPLY_DEFAULT_RAILS))

    def test_no_mods_returns_defaults(self):
        self.assertEqual(q.collect_supply_rails(None),
                         list(q.SUPPLY_DEFAULT_RAILS))


class SupplyFolioTest(unittest.TestCase):
    """The 'Alimentación' rail folio: appended after the changelog, text+shape
    primitives only, empty <elements>/<conductors>, titled 'Alimentación'."""

    def test_appends_one_folio_titled_alimentacion(self):
        project = ET.Element("project")
        n = q.build_supply_folios(project, 7, rails=["L1", "N", "L+"])
        self.assertEqual(n, 1)
        d = project.find("diagram")
        self.assertEqual(d.get("order"), "7")
        self.assertEqual(d.get("title"), "Alimentación")

    def test_only_text_and_shapes_no_elements_or_conductors(self):
        project = ET.Element("project")
        q.build_supply_folios(project, 1, rails=list(q.SUPPLY_DEFAULT_RAILS))
        d = project.find("diagram")
        self.assertEqual(len(d.find("elements").findall("element")), 0)
        self.assertEqual(len(d.find("conductors").findall("conductor")), 0)
        self.assertGreater(len(d.find("shapes").findall("shape")), 0)
        texts = [i.get("text") for i in d.find("inputs").findall("input")]
        for rail in q.SUPPLY_DEFAULT_RAILS:
            self.assertIn(rail, texts)

    def test_empty_rails_appends_nothing(self):
        project = ET.Element("project")
        self.assertEqual(q.build_supply_folios(project, 1, rails=[]), 0)
        self.assertEqual(len(project.findall("diagram")), 0)

    def test_rail_label_sits_clear_above_its_line(self):
        # DA.8: the potential label and its rail line were touching; the label
        # must now sit a clear gap ABOVE the line it names. The rails are drawn
        # top-to-bottom, so the i-th label pairs with the i-th line.
        project = ET.Element("project")
        q.build_supply_folios(project, 7, rails=["L1", "N", "L+"])
        d = project.find("diagram")
        label_ys = sorted(float(i.get("y"))
                          for i in d.find("inputs").findall("input")
                          if i.get("text") in ("L1", "N", "L+"))
        line_ys = sorted(float(s.get("y1"))
                         for s in d.find("shapes").findall("shape"))
        self.assertEqual(len(label_ys), len(line_ys))
        for label_y, line_y in zip(label_ys, line_ys):
            self.assertLessEqual(label_y + 12, line_y)   # clear gap, not touching

    def test_inherits_titleblock_when_attached(self):
        """The supply folio must be stamped by attach_titleblocks like any other
        folio (it is appended before attach_titleblocks in main)."""
        project = ET.Element("project")
        q.build_supply_folios(project, 1, rails=["L1", "N"])
        fields = q.resolve_title_block_fields(
            {**q.PROJECT_TEMPLATE_DEFAULTS, "company": "Exxerpro Solutions"}, "C")
        n = q.attach_titleblocks(project, fields, q.load_titleblock_template())
        d = project.find("diagram")
        self.assertEqual(d.get("titleblocktemplate"), "exxerpro")
        self.assertIsNotNone(d.find("properties"))


class SymbolDisplayNameTest(unittest.TestCase):
    """DA.4: localized name pulled from the embedded .elmt <names>, language
    agnostic with a graceful fallback chain (lang → en → any → description → id),
    so no locale is hardcoded and a symbol always has a legible label."""

    @staticmethod
    def _entry(names: dict, **extra):
        defn = ET.Element("definition")
        ns = ET.SubElement(defn, "names")
        for lang, text in names.items():
            ET.SubElement(ns, "name", {"lang": lang}).text = text
        return {"_definition": defn, **extra}

    def test_prefers_requested_lang(self):
        e = self._entry({"es": "Lámpara", "en": "Lamp"})
        self.assertEqual(q.symbol_display_name(e, "es"), "Lámpara")

    def test_falls_back_to_english_then_any(self):
        self.assertEqual(q.symbol_display_name(self._entry({"en": "Lamp"}), "es"),
                         "Lamp")
        self.assertEqual(q.symbol_display_name(self._entry({"de": "Lampe"}), "es"),
                         "Lampe")

    def test_falls_back_to_description_then_id(self):
        no_names = {"_definition": ET.Element("definition"),
                    "description": "Pilot light", "id": "pilot_light"}
        self.assertEqual(q.symbol_display_name(no_names, "es"), "Pilot light")
        only_id = {"_definition": ET.Element("definition"), "id": "pilot_light"}
        self.assertEqual(q.symbol_display_name(only_id, "es"), "pilot_light")

    def test_ignores_blank_names(self):
        e = self._entry({"es": "   ", "en": "Lamp"})
        self.assertEqual(q.symbol_display_name(e, "es"), "Lamp")


class SymbologyFolioTest(unittest.TestCase):
    """DA.4: the legend lists ONLY the used symbol types — one real glyph
    (embedded element) + its localized name per row — and is empty-safe."""

    @staticmethod
    def _used(*ids):
        # minimal entries good enough for placement + naming
        out = []
        for sid in ids:
            defn = ET.fromstring(
                '<definition><names><name lang="es">N-%s</name></names>'
                '<description><terminal x="0" y="0" orientation="north"/>'
                '</description></definition>' % sid)
            out.append({"id": sid, "element": f"{sid}.elmt",
                        "_definition": defn,
                        "_terminals": [(0, 0, 0)]})
        return out

    def test_empty_used_appends_nothing(self):
        project = ET.Element("project")
        self.assertEqual(q.build_symbology_folio(project, 1, []), 0)
        self.assertEqual(len(project.findall("diagram")), 0)

    def test_one_glyph_and_name_per_used_symbol(self):
        project = ET.Element("project")
        n = q.build_symbology_folio(project, 1, self._used("relay_coil", "horn"))
        self.assertEqual(n, 1)
        d = project.find("diagram")
        self.assertEqual(d.get("title"), "Simbología")
        self.assertEqual(d.get("order"), "1")
        glyphs = d.find("elements").findall("element")
        self.assertEqual(len(glyphs), 2)          # one glyph per used symbol
        self.assertEqual(len(d.find("conductors").findall("conductor")), 0)
        texts = [i.get("text") for i in d.find("inputs").findall("input")]
        self.assertIn("N-relay_coil", texts)
        self.assertIn("N-horn", texts)

    def test_terminal_ids_unique_within_folio(self):
        project = ET.Element("project")
        q.build_symbology_folio(project, 1,
                                self._used("a", "b", "c"))
        ids = [t.get("id") for t in
               project.find("diagram").find("elements").iter("terminal")]
        self.assertEqual(len(ids), len(set(ids)))

    def test_overflow_flows_to_second_column_same_folio(self):
        # DA.8: one more symbol than a single column holds flows into the 2nd
        # column on the SAME folio (column-major), never off the page bottom.
        proj = ET.Element("project")
        syms = self._used(*[f"s{i}" for i in range(q.SYM_ROWS_PER_COL + 1)])
        n = q.build_symbology_folio(proj, 1, syms)
        self.assertEqual(n, 1)
        names = {i.get("text"): float(i.get("x"))
                 for i in proj.find("diagram").find("inputs").findall("input")
                 if (i.get("text") or "").startswith("N-")}
        self.assertEqual(names["N-s0"], float(q.SYM_NAME_X))
        self.assertEqual(names[f"N-s{q.SYM_ROWS_PER_COL}"],
                         float(q.SYM_NAME_X + q.SYM_COL_DX))

    def test_all_glyphs_and_names_inside_page_frame(self):
        # a full two-column folio keeps every glyph + name inside the frame
        proj = ET.Element("project")
        syms = self._used(*[f"s{i}" for i in range(2 * q.SYM_ROWS_PER_COL)])
        q.build_symbology_folio(proj, 1, syms)
        inputs = proj.find("diagram").find("inputs").findall("input")
        self.assertTrue(all(float(i.get("y")) < q.SUMMARY_HEIGHT for i in inputs))
        self.assertTrue(all(float(i.get("x")) < q.SUMMARY_PAGE_WIDTH
                            for i in inputs))

    def test_more_than_two_columns_paginate_to_more_folios(self):
        # beyond two columns the legend paginates onto further folios
        proj = ET.Element("project")
        per_page = q.SYM_COLS_PER_PAGE * q.SYM_ROWS_PER_COL
        syms = self._used(*[f"s{i}" for i in range(per_page + 1)])
        n = q.build_symbology_folio(proj, 1, syms)
        self.assertEqual(n, 2)
        self.assertEqual([d.get("order") for d in proj.findall("diagram")],
                         ["1", "2"])


class PortadaFolioTest(unittest.TestCase):
    """DA.3: the cover (Portada) folio is one diagram of text + shapes only
    (empty <elements>/<conductors>), rendering REAL title-block metadata + the
    L5X controller name, with unset fields left blank (never invented)."""

    @staticmethod
    def _build(fields, controller="CTRL_1", order=0):
        project = ET.Element("project")
        n = q.build_portada_folio(project, order, fields, controller)
        diags = project.findall("diagram")
        return n, diags

    def test_appends_one_folio_titled_portada(self):
        n, diags = self._build({"project": "Línea 1", "company": "ACME"})
        self.assertEqual(n, 1)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].get("title"), "Portada")
        self.assertEqual(diags[0].get("order"), "0")

    def test_contains_only_text_and_shapes(self):
        _, diags = self._build({"project": "P"})
        d = diags[0]
        self.assertEqual(len(d.find("elements").findall("element")), 0)
        self.assertEqual(len(d.find("conductors").findall("conductor")), 0)
        self.assertGreater(len(d.find("inputs").findall("input")), 0)

    def test_renders_real_values_and_controller(self):
        fields = {"project": "Envasadora", "company": "ACME",
                  "drawing_number": "PL-001", "revision": "B",
                  "approved_by": "Abel"}
        _, diags = self._build(fields, controller="WADDING_1")
        texts = [i.get("text") for i in diags[0].find("inputs").findall("input")]
        for expected in ("ACME", "PL-001", "B", "Abel", "WADDING_1"):
            self.assertIn(expected, texts)
        # the project name appears upper-cased as the heading
        self.assertIn("ENVASADORA", texts)

    def test_blank_fields_are_not_invented(self):
        # only project given; drawing_number/revision/etc. unset → their VALUE
        # cells are absent (labels still render, values blank — never fabricated)
        _, diags = self._build({"project": "Linea"}, controller="C")
        texts = [i.get("text") for i in diags[0].find("inputs").findall("input")]
        self.assertNotIn("None", texts)            # no str(None) leak
        self.assertFalse(any("%{" in (t or "") for t in texts))  # no raw token
        self.assertIn("APROBÓ", texts)             # label renders even when blank
        # the project value renders once (the PROYECTO row); the heading is the
        # upper-cased form — distinct text — so no invented duplicate value.
        self.assertEqual(sum(1 for t in texts if t == "Linea"), 1)
        self.assertEqual(sum(1 for t in texts if t == "LINEA"), 1)

    def test_no_titleblock_field_tokens_without_data_path(self):
        # department/country/status/type/code have no project-template source;
        # they are intentionally omitted from the cover rows.
        labels = {lbl for lbl, _ in q.PORTADA_ROWS}
        for absent in ("DEPARTAMENTO", "PAÍS", "ESTADO"):
            self.assertNotIn(absent, labels)


class ReorderDiagramsByPositionTest(unittest.TestCase):
    """DA.2: reorder_diagrams_by_position stably re-sorts <diagram> children by
    their integer 'order' attribute, decoupling folio position from build order
    while leaving every attribute and any non-diagram child untouched."""

    @staticmethod
    def _project(orders):
        p = ET.Element("project")
        for o in orders:
            d = ET.SubElement(p, "diagram", {"title": f"t{o}"})
            if o is not None:
                d.set("order", str(o))
        return p

    def test_sorts_by_integer_order_not_string(self):
        # 100 < 101 < 200 < 900 numerically (string sort would put "1000"<"200")
        p = self._project([900, 101, 300, 100, 200])
        ordered = q.reorder_diagrams_by_position(p)
        seq = [int(d.get("order")) for d in p.findall("diagram")]
        self.assertEqual(seq, [100, 101, 200, 300, 900])
        self.assertEqual([d.get("order") for d in ordered],
                         ["100", "101", "200", "300", "900"])

    def test_stable_and_missing_order_sorts_last(self):
        p = ET.Element("project")
        a = ET.SubElement(p, "diagram", {"title": "no-order-A"})
        b = ET.SubElement(p, "diagram", {"title": "p5", "order": "5"})
        c = ET.SubElement(p, "diagram", {"title": "no-order-B"})
        q.reorder_diagrams_by_position(p)
        titles = [d.get("title") for d in p.findall("diagram")]
        # the ordered one floats to the front; the two order-less keep A-before-B
        self.assertEqual(titles, ["p5", "no-order-A", "no-order-B"])

    def test_preserves_non_diagram_children(self):
        p = self._project([200, 100])
        coll = ET.SubElement(p, "collection")  # e.g. the symbol collection
        coll.set("tag", "keep")
        q.reorder_diagrams_by_position(p)
        tags = [c.tag for c in list(p)]
        self.assertEqual(tags.count("collection"), 1)
        self.assertEqual(p.find("collection").get("tag"), "keep")
        seq = [int(d.get("order")) for d in p.findall("diagram")]
        self.assertEqual(seq, [100, 200])

    def test_no_diagrams_is_noop(self):
        p = ET.Element("project")
        ET.SubElement(p, "collection")
        self.assertEqual(q.reorder_diagrams_by_position(p), [])


class WaddingRegressionTest(unittest.TestCase):
    """End-to-end floor: the WADDING_1 fixture must still produce 10 drawing
    folios / 106 points / 75 matched / 0 false positives, with the summary +
    changelog + NEW supply folio present, the title block on every folio, no raw
    %{token} leaks, and the supply folio touching no element/conductor. Skipped
    if the fixture is absent (public-repo hygiene: it is never committed)."""

    FIXTURE = Path(__file__).resolve().parent.parent / "Fixtures" / "WADDING_1.L5X"

    def setUp(self):
        if not self.FIXTURE.is_file():
            self.skipTest("WADDING_1.L5X fixture not present")

    def _run(self):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "w.qet"
            with redirect_stderr(buf):
                rc = q.main([str(self.FIXTURE), "-o", str(out)])
            self.assertEqual(rc, 0)
            xml = out.read_text(encoding="utf-8")
        return ET.fromstring(xml), xml, buf.getvalue()

    def test_floor_folio_and_point_counts(self):
        root, _, err = self._run()
        diagrams = root.findall("diagram")
        # 10 drawing folios (one per I/O card with mapped tags)
        drawing = [d for d in diagrams
                   if d.get("title", "").startswith("R")]
        self.assertEqual(len(drawing), 10)
        # Mechanically enforce the WADDING_1 floor from main()'s own summary so a
        # future change that drops symbol matches (or adds a false positive) turns
        # this test red. Exact equality on the match count guards BOTH directions:
        # a drop is a regression, an increase past 75 is a false positive.
        m_pts = re.search(r"points\s*:\s*(\d+)\s+drawn", err)
        m_sym = re.search(r"symbols\s*:\s*(\d+)\s+matched", err)
        self.assertIsNotNone(m_pts, f"no point count in summary:\n{err}")
        self.assertIsNotNone(m_sym, f"no symbol count in summary:\n{err}")
        self.assertEqual(int(m_pts.group(1)), 106)   # floor: 106 points drawn
        self.assertEqual(int(m_sym.group(1)), 75)     # floor: 75 matched, 0 FPs
        # the new supply folio is present alongside the drawing folios
        titles = [d.get("title") for d in diagrams]
        self.assertIn("Alimentación", titles)

    def test_supply_folio_present_and_clean(self):
        root, _, _ = self._run()
        sup = [d for d in root.findall("diagram")
               if d.get("title") == "Alimentación"]
        self.assertEqual(len(sup), 1)
        s = sup[0]
        self.assertEqual(len(s.find("elements").findall("element")), 0)
        self.assertEqual(len(s.find("conductors").findall("conductor")), 0)
        # carries the title block
        self.assertEqual(s.get("titleblocktemplate"), "exxerpro")
        self.assertIsNotNone(s.find("properties"))

    def test_changelog_and_summary_folios_intact(self):
        root, _, _ = self._run()
        titles = [d.get("title") for d in root.findall("diagram")]
        self.assertTrue(any("revisiones" in (t or "").lower() for t in titles))
        self.assertTrue(any("BOM" in (t or "") for t in titles))

    def test_title_block_on_every_folio_no_raw_token_leak(self):
        root, xml, _ = self._run()
        for d in root.findall("diagram"):
            self.assertEqual(d.get("titleblocktemplate"), "exxerpro")
        # the only %{...} allowed are inside the embedded template + the %id/%total
        # folio attribute; no diagram property text should be a raw placeholder.
        for d in root.findall("diagram"):
            for prop in d.find("properties").findall("property"):
                self.assertNotIn("%{", prop.text or "")

    def test_folios_in_natural_drawing_order(self):
        # DA.2: document order == section order. The 'order' attribute (section
        # page) must be non-decreasing across the diagrams as serialized, and the
        # section anchors must fall in the natural sequence: Alimentación (100) →
        # card drawings (101..110) → borneros (200+) → BOM (300+) → Historial (900).
        root, _, _ = self._run()
        diagrams = root.findall("diagram")
        orders = [int(d.get("order")) for d in diagrams]
        self.assertEqual(orders, sorted(orders))   # serialized in section order
        titles = [d.get("title") or "" for d in diagrams]
        idx = lambda pred: next(i for i, t in enumerate(titles) if pred(t))
        i_portada = idx(lambda t: t == "Portada")
        i_simb = idx(lambda t: t == "Simbología")
        i_supply = idx(lambda t: t == "Alimentación")
        i_draw = idx(lambda t: t.startswith("R"))
        i_born = idx(lambda t: t.startswith("Bornero"))
        i_bom = idx(lambda t: "BOM" in t)
        i_chg = idx(lambda t: "revisiones" in t.lower())
        self.assertEqual(i_portada, 0)             # Portada is FIRST
        self.assertEqual(i_simb, 1)                # Simbología second
        self.assertLess(i_simb, i_supply)
        self.assertLess(i_supply, i_draw)
        self.assertLess(i_draw, i_born)
        self.assertLess(i_born, i_bom)
        self.assertLess(i_bom, i_chg)
        self.assertEqual(i_chg, len(titles) - 1)   # changelog is LAST

    def test_cajetin_shows_section_page_not_position(self):
        # DA.5b: every folio's page property equals its order zero-padded to 3
        # digits, and QET's position counter token is gone from the embedded
        # template (so the cajetín shows 000/100/101… not 1/27).
        root, xml, _ = self._run()
        self.assertNotIn("%{folio-id}", xml)
        self.assertIn("%{page}", xml)
        for d in root.findall("diagram"):
            props = {p.get("name"): p.text for p in d.find("properties")}
            self.assertEqual(props.get("page"), f"{int(d.get('order')):03d}")

    def test_list_folios_hide_grid_rulers_drawings_keep_them(self):
        # the "out of the box" fix: non-schematic list/front-matter folios hide
        # QET's grid rulers (0–16 / A–H); the card drawings keep them.
        root, _, _ = self._run()
        for d in root.findall("diagram"):
            is_drawing = (d.get("title") or "").startswith("R")
            want = "true" if is_drawing else "false"
            self.assertEqual(d.get("displaycols"), want, d.get("title"))
            self.assertEqual(d.get("displayrows"), want, d.get("title"))

    def test_symbology_lists_only_used_symbols(self):
        # DA.4: the legend carries exactly one glyph per USED symbol type, and
        # the count matches the embedded collection's symbol definitions.
        root, _, _ = self._run()
        simb = [d for d in root.findall("diagram")
                if d.get("title") == "Simbología"]
        self.assertEqual(len(simb), 1)
        glyphs = simb[0].find("elements").findall("element")
        self.assertGreater(len(glyphs), 0)
        # every glyph type resolves to an embedded definition
        collection = root.find("collection")
        def_names = {el.get("name") for el in collection.iter("element")
                     if el.find("definition") is not None}
        glyph_types = {e.get("type").split("/")[-1] for e in glyphs}
        self.assertTrue(glyph_types.issubset(def_names))
        # the legend has no conductors
        self.assertEqual(len(simb[0].find("conductors").findall("conductor")), 0)

    def test_designations_follow_printed_page(self):
        # DA.5: designations FOLLOW the printed page. Each card drawing sits on a
        # section page 101..110 and every -K<page>.<n> device on that folio uses
        # that folio's page as the prefix (so no -K1.. -K10.. survive).
        root, _, _ = self._run()
        drawing = [d for d in root.findall("diagram")
                   if (d.get("title") or "").startswith("R")]
        self.assertEqual(len(drawing), 10)
        pages = sorted(int(d.get("order")) for d in drawing)
        self.assertEqual(pages, list(range(101, 111)))   # 101..110
        seen_prefixes = set()
        for d in drawing:
            page = d.get("order")
            xml = ET.tostring(d, encoding="unicode")
            for prefix in re.findall(r"-[A-Z](\d+)\.\d+", xml):
                self.assertEqual(prefix, page,
                                 f"designation prefix {prefix} on folio {page}")
                seen_prefixes.add(prefix)
        # at least some page-prefixed designations actually exist (sanity)
        self.assertTrue(seen_prefixes)
        self.assertTrue(seen_prefixes.issubset({str(p) for p in range(101, 111)}))

    def test_structural_invariants_hold(self):
        root, _, _ = self._run()
        collection = root.find("collection")
        def_names = {el.get("name") for el in collection.iter("element")
                     if el.find("definition") is not None}
        for d in root.findall("diagram"):
            ids = [t.get("id") for t in d.find("elements").iter("terminal")]
            self.assertEqual(len(ids), len(set(ids)))   # unique per diagram
            idset = set(ids)
            for c in d.find("conductors").findall("conductor"):
                self.assertIn(c.get("terminal1"), idset)
                self.assertIn(c.get("terminal2"), idset)
            for el in d.find("elements").findall("element"):
                self.assertIn(el.get("type").split("/")[-1], def_names)


class StripTerminalLabelTest(unittest.TestCase):
    """Pure helper: the inline-strip / bornero terminal designation. -X1 resets
    per card; the terminal number IS the I/O channel (point index, 0-based)."""

    def test_label_is_designation_colon_channel(self):
        self.assertEqual(q.strip_terminal_label(0), "-X1:0")
        self.assertEqual(q.strip_terminal_label(15), "-X1:15")

    def test_channel_is_point_index_zero_based(self):
        # channel reads 1:1 against the drawn points (point-mirrored), so the
        # label for channel N is exactly N — never N+1, never a re-sequence.
        for ch in range(16):
            self.assertEqual(q.strip_terminal_label(ch), f"-X1:{ch}")

    def test_designation_is_x1_and_resets_per_card(self):
        # every card's strip is '-X1' — the helper hard-codes that designation,
        # so two different cards' channel-0 terminals carry the identical '-X1:0'.
        self.assertTrue(q.strip_terminal_label(0).startswith("-X1:"))
        self.assertEqual(q.STRIP_DESIGNATION, "-X1")

    def test_designation_override_is_honoured(self):
        self.assertEqual(q.strip_terminal_label(3, "-X9"), "-X9:3")


class BuildFolioStripInlineTest(unittest.TestCase):
    """Integration: build_folio places ONE inline strip terminal per drawn point
    (matched AND generic), labelled -X1:<channel>, and rewires the field
    conductor through it so every conductor terminal id resolves to a real
    terminal on the diagram."""

    @staticmethod
    def _diagram(pts, catalog="FAKE-NODB"):
        mod = SimpleNamespace(rack=1, slot=2, name="CARD", catalog=catalog,
                              kind="DI", points=16, in_byte_base=0,
                              out_byte_base=0, an_in_word_base=0,
                              an_out_word_base=0)
        for pt in pts:
            pt.module = mod
        project = ET.Element("project")
        q.build_folio(project, 3, mod, pts, q.load_symbol_db(), {}, {},
                      wire_scheme="address", wire_counters={})
        return project.find("diagram")

    def _texts(self, d):
        return [i.get("text") for i in d.find("inputs").findall("input")]

    def test_matched_point_gets_strip_label_and_split_conductor(self):
        # LS1 matches limit_switch -> a device symbol is placed; the strip
        # terminal -X1:0 sits between the I/O terminal and the device.
        mod_pt = SimpleNamespace(module=None, index=0, tag="LS1", direction="I",
                                 description="", analog=False)
        d = self._diagram([mod_pt])
        self.assertIn("-X1:0", self._texts(d))
        # matched point -> field conductor BROKEN into two segments
        conds = d.find("conductors").findall("conductor")
        self.assertEqual(len(conds), 2)

    def test_generic_point_gets_strip_label_and_single_conductor(self):
        # an unmatched tag stays generic: strip terminal -X1:0 + ONE conductor
        # (card terminal -> strip terminal); no device beyond.
        pt = SimpleNamespace(module=None, index=0, tag="ZZZ_NOMATCH",
                             direction="I", description="", analog=False)
        d = self._diagram([pt])
        self.assertIn("-X1:0", self._texts(d))
        conds = d.find("conductors").findall("conductor")
        self.assertEqual(len(conds), 1)

    def test_channel_label_tracks_point_index_for_both_kinds(self):
        pts = [
            SimpleNamespace(module=None, index=0, tag="LS1", direction="I",
                            description="", analog=False),      # matched
            SimpleNamespace(module=None, index=5, tag="ZZZ_NOMATCH",
                            direction="I", description="", analog=False),  # gen.
        ]
        d = self._diagram(pts)
        texts = self._texts(d)
        self.assertIn("-X1:0", texts)
        self.assertIn("-X1:5", texts)

    def test_every_conductor_endpoint_resolves_to_a_terminal(self):
        pts = [
            SimpleNamespace(module=None, index=0, tag="LS1", direction="I",
                            description="", analog=False),
            SimpleNamespace(module=None, index=1, tag="ZZZ_NOMATCH",
                            direction="I", description="", analog=False),
        ]
        d = self._diagram(pts)
        ids = {t.get("id") for t in d.find("elements").iter("terminal")}
        for c in d.find("conductors").findall("conductor"):
            self.assertIn(c.get("terminal1"), ids)
            self.assertIn(c.get("terminal2"), ids)

    def test_strip_terminal_reuses_borne_2_definition(self):
        pt = SimpleNamespace(module=None, index=0, tag="ZZZ_NOMATCH",
                             direction="I", description="", analog=False)
        d = self._diagram([pt])
        types = [el.get("type")
                 for el in d.find("elements").findall("element")]
        # the I/O terminal + the strip terminal + every SPARE reserve terminal
        # are all borne_2 (T3.2 introduces NO new element type). One mapped point
        # on a 16-channel card -> card(1) + strip(1) + spares(15) = 17 borne_2,
        # and NO other element type (the generic point places no device symbol).
        self.assertEqual(types.count(q.TERMINAL_TYPE), 17)
        self.assertEqual(set(types), {q.TERMINAL_TYPE})

    def test_sequential_scheme_numbers_generic_points_too(self):
        """Documented behaviour: under --wire-scheme sequential EVERY drawn
        point's card->strip segment consumes one W<page>.<n> slot, generic and
        matched alike (each generic point now carries a field conductor). Pin this
        so the per-page counter advance is intentional, not an accident."""
        mod = SimpleNamespace(rack=1, slot=2, name="CARD", catalog="FAKE-NODB",
                              kind="DI", points=16, in_byte_base=0,
                              out_byte_base=0, an_in_word_base=0,
                              an_out_word_base=0)
        pts = [
            SimpleNamespace(module=mod, index=0, tag="LS1", direction="I",
                            description="", analog=False),         # matched
            SimpleNamespace(module=mod, index=1, tag="ZZZ_NOMATCH",
                            direction="I", description="", analog=False),  # gen.
            SimpleNamespace(module=mod, index=2, tag="LS2", direction="I",
                            description="", analog=False),         # matched
        ]
        project = ET.Element("project")
        q.build_folio(project, 3, mod, pts, q.load_symbol_db(), {}, {},
                      wire_scheme="sequential", wire_counters={})
        d = project.find("diagram")
        nums = [c.get("num") for c in d.find("conductors").findall("conductor")
                if c.get("num")]
        # one number per drawn point, in drawn order; generic point gets W3.2
        self.assertEqual(nums, ["W3.1", "W3.2", "W3.3"])


class StripTerminalGeometryTest(unittest.TestCase):
    """Positional/visual: the FULL strip-terminal pin extent must stay clear of
    the card box, the row text, the device symbol, and on-sheet — asserted on the
    real extent (x..x+10, y±10), not the centre point, for every column."""

    @staticmethod
    def _min_device_west_offset():
        """The smallest device WEST-pin x-offset across the WHOLE symbol DB,
        computed exactly as add_symbol_element transforms pins (rotate 90° CW:
        (tx,ty)->(-ty,tx), orient +1). This is the REAL left edge a placed device
        can occupy (the photocell is the tightest); the strip terminal must clear
        THIS, not the optimistic x+SYM_X_OFF-10 constant."""
        offs = []
        for e in q.load_symbol_db():
            pins = [(-ty, tx, (to + 1) % 4) for tx, ty, to in e["_terminals"]]
            west = min(range(len(pins)),
                       key=lambda i: (pins[i][2] != 3, pins[i][0]))
            offs.append(q.SYM_X_OFF + pins[west][0])
        return min(offs)

    def test_full_extent_clears_box_text_device_and_sheet(self):
        device_west_off = self._min_device_west_offset()
        # guard the guard: this must be the photocell-tight value, not a fiction
        self.assertEqual(device_west_off, 260)
        for x in q.COL_X:
            cx = x + q.STRIP_X_OFF        # strip terminal centre x
            # borne_2 east pin reaches cx+10; N/S pins at cx; pins span y±10
            left, right = cx, cx + 10
            box_right = x + q.BOX_RIGHT           # card box right edge
            row_text_right = x + 20 + 180         # generous row-text band end
            # the REAL closest device west pin (computed from the symbol DB), not
            # an assumed x+SYM_X_OFF-10 — so a regression that slides the strip
            # into the photocell pin turns this red.
            device_west = x + device_west_off
            # clears the card box entirely (to the right of it)
            self.assertGreater(left, box_right,
                               f"strip @x={x} overlaps card box")
            # clears the busy row-text band
            self.assertGreaterEqual(left, row_text_right,
                                    f"strip @x={x} collides with row text")
            # the whole terminal sits strictly LEFT of the closest device west pin
            self.assertLess(right, device_west,
                            f"strip @x={x} (right={right}) collides with the "
                            f"closest device west pin ({device_west})")
            # on-sheet: left edge non-negative
            self.assertGreaterEqual(left, 0, f"strip @x={x} is off-sheet")

    def test_pin_extent_y_band_is_full_row_height(self):
        # the strip terminal centre sits ON the I/O row y; its pins span y±10,
        # which is comfortably inside the ROW_DY (=35) pitch, so adjacent strip
        # terminals never overlap vertically.
        self.assertLess(2 * 10, q.ROW_DY)


class BorneroFolioTest(unittest.TestCase):
    """The dedicated terminal-strip (bornero) folio, one per card: text+shape
    primitives only, empty <elements>/<conductors>, title block on every one."""

    @staticmethod
    def _card(name, indices, catalog="FAKE-NODB"):
        mod = SimpleNamespace(rack=1, slot=2, name=name, catalog=catalog,
                              kind="DI", points=16)
        pts = [SimpleNamespace(module=mod, index=i, tag=f"T{i}", direction="I",
                               description="", analog=False) for i in indices]
        return (mod, pts)

    def test_one_folio_per_card_in_order(self):
        project = ET.Element("project")
        cards = [self._card("A", [0, 1]), self._card("B", [0])]
        n = q.build_bornero_folios(project, 7, cards)
        self.assertEqual(n, 2)
        ds = project.findall("diagram")
        self.assertEqual([d.get("order") for d in ds], ["7", "8"])
        self.assertIn("-A", ds[0].get("title"))
        self.assertIn("-B", ds[1].get("title"))
        self.assertIn("-X1", ds[0].get("title"))

    def test_lists_each_strip_terminal_in_drawn_order(self):
        project = ET.Element("project")
        q.build_bornero_folios(project, 1, [self._card("A", [0, 3, 7])])
        d = project.find("diagram")
        texts = [i.get("text") for i in d.find("inputs").findall("input")]
        for ch in (0, 3, 7):
            self.assertIn(f"-X1:{ch}", texts)

    def test_text_only_no_elements_or_conductors(self):
        project = ET.Element("project")
        q.build_bornero_folios(project, 1, [self._card("A", [0, 1])])
        d = project.find("diagram")
        self.assertEqual(len(d.find("elements").findall("element")), 0)
        self.assertEqual(len(d.find("conductors").findall("conductor")), 0)
        # the header rule was removed (DA.8): the bornero folio is text-only
        self.assertEqual(len(d.find("shapes").findall("shape")), 0)

    def test_empty_card_list_appends_nothing(self):
        project = ET.Element("project")
        self.assertEqual(q.build_bornero_folios(project, 1, []), 0)
        self.assertEqual(len(project.findall("diagram")), 0)

    def test_card_with_no_points_is_skipped(self):
        project = ET.Element("project")
        n = q.build_bornero_folios(project, 1, [self._card("A", [])])
        self.assertEqual(n, 0)

    def test_bornero_folio_gets_titleblock_no_raw_tokens(self):
        project = ET.Element("project")
        q.build_bornero_folios(project, 1,
                               [self._card("A", [0]), self._card("B", [0])])
        fields = q.resolve_title_block_fields(
            {**q.PROJECT_TEMPLATE_DEFAULTS, "company": "Exxerpro Solutions"}, "C")
        q.attach_titleblocks(project, fields, q.load_titleblock_template())
        for d in project.findall("diagram"):
            self.assertEqual(d.get("titleblocktemplate"), "exxerpro")
            self.assertIsNotNone(d.find("properties"))
            for prop in d.find("properties").findall("property"):
                self.assertNotIn("%{", prop.text or "")


class WaddingBorneroFloorTest(unittest.TestCase):
    """Floor: the bornero feature must NOT regress the WADDING_1 numbers. Parses
    main()'s own stderr summary and asserts the literal floor (106 points / 75
    matched) plus the new bornero folio line and 10 untouched drawing folios."""

    FIXTURE = Path(__file__).resolve().parent.parent / "Fixtures" / "WADDING_1.L5X"

    def setUp(self):
        if not self.FIXTURE.is_file():
            self.skipTest("WADDING_1.L5X fixture not present")

    def _run(self):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "w.qet"
            with redirect_stderr(buf):
                rc = q.main([str(self.FIXTURE), "-o", str(out)])
            self.assertEqual(rc, 0)
            xml = out.read_text(encoding="utf-8")
        return ET.fromstring(xml), buf.getvalue()

    def test_floor_unchanged_and_bornero_reported(self):
        root, err = self._run()
        # literal floor from the summary
        self.assertRegex(err, r"points\s*:\s*106\s+drawn")
        self.assertRegex(err, r"symbols\s*:\s*75\s+matched")
        # exactly 10 drawing folios (one per I/O card with mapped tags)
        drawing = [d for d in root.findall("diagram")
                   if d.get("title", "").startswith("R")]
        self.assertEqual(len(drawing), 10)
        # the bornero summary line is honest about what it drew. T3.2: borneros
        # now list mapped + RESERVA terminals in channel order, so a wide card
        # paginates — REM_IN_1 (32-channel) needs a second sheet -> 11 folios for
        # the 10 cards (NOT 10). The floor (106/75) above is what must not move.
        m = re.search(r"bornero\s*:\s*(\d+)\s+terminal-strip", err)
        self.assertIsNotNone(m, f"no bornero line in summary:\n{err}")
        self.assertEqual(int(m.group(1)), 11)
        borneros = [d for d in root.findall("diagram")
                    if (d.get("title") or "").startswith("Bornero")]
        self.assertEqual(len(borneros), 11)
        # exactly one card paginated (its sheets carry an (n/total) suffix)
        paged = [d for d in borneros if "(2/" in (d.get("title") or "")]
        self.assertEqual(len(paged), 1)

    def test_bornero_folios_carry_titleblock_no_raw_tokens(self):
        root, _ = self._run()
        borneros = [d for d in root.findall("diagram")
                    if (d.get("title") or "").startswith("Bornero")]
        self.assertTrue(borneros)
        for d in borneros:
            self.assertEqual(d.get("titleblocktemplate"), "exxerpro")
            self.assertIsNotNone(d.find("properties"))
            for prop in d.find("properties").findall("property"):
                self.assertNotIn("%{", prop.text or "")
            # bornero folios touch no element/conductor instance
            self.assertEqual(len(d.find("elements").findall("element")), 0)
            self.assertEqual(len(d.find("conductors").findall("conductor")), 0)


class ContinuationRefsTest(unittest.TestCase):
    """DA.5c: prev/next continuation refs on multi-sheet sections only."""

    @staticmethod
    def _project(orders):
        project = ET.Element("project")
        for o in orders:
            d = ET.SubElement(project, "diagram", {"order": str(o)})
            ET.SubElement(d, "inputs")
        return project

    @staticmethod
    def _refs(project):
        """{order: [ref texts]} for every diagram carrying continuation text."""
        out = {}
        for d in project.findall("diagram"):
            texts = [i.get("text") for i in d.find("inputs").findall("input")
                     if "pág." in (i.get("text") or "")]
            if texts:
                out[int(d.get("order"))] = texts
        return out

    def test_middle_sheet_gets_both_first_and_last_one_each(self):
        # a 3-folio drawing section: 101 → next only, 102 → both, 103 → prev only
        project = self._project([101, 102, 103])
        n = q.add_continuation_refs(project)
        refs = self._refs(project)
        self.assertEqual(refs[101], ["pág. 102 ►"])
        self.assertCountEqual(refs[102], ["◄ pág. 101", "pág. 103 ►"])
        self.assertEqual(refs[103], ["◄ pág. 102"])
        self.assertEqual(n, 4)            # 1 + 2 + 1 lines

    def test_refs_use_section_page_not_position(self):
        # borneros 200..202 reference their SECTION pages, never 0/1/2 positions
        project = self._project([200, 201, 202])
        q.add_continuation_refs(project)
        refs = self._refs(project)
        self.assertCountEqual(refs[201], ["◄ pág. 200", "pág. 202 ►"])

    def test_single_folio_section_gets_no_refs(self):
        # a lone BOM folio (300) has no neighbour → no continuation text
        project = self._project([300])
        self.assertEqual(q.add_continuation_refs(project), 0)
        self.assertEqual(self._refs(project), {})

    def test_front_matter_and_changelog_excluded(self):
        # Portada(0), Simbología(1), Alimentación(100), Historial(900) are
        # outside CONTINUATION_RANGES — never annotated even alongside a real
        # multi-sheet drawings section.
        project = self._project([0, 1, 100, 101, 102, 900])
        q.add_continuation_refs(project)
        refs = self._refs(project)
        self.assertEqual(set(refs), {101, 102})
        for excluded in (0, 1, 100, 900):
            self.assertNotIn(excluded, refs)

    def test_sections_do_not_bleed_into_each_other(self):
        # adjacent sections must not chain: drawing 110's "next" is NOT bornero
        # 200, and bornero 200's "prev" is NOT drawing 110.
        project = self._project([109, 110, 200, 201])
        q.add_continuation_refs(project)
        refs = self._refs(project)
        self.assertEqual(refs[110], ["◄ pág. 109"])     # last drawing: prev only
        self.assertEqual(refs[200], ["pág. 201 ►"])     # first bornero: next only

    def test_refs_clear_card_box_and_stay_inside_frame(self):
        # the continuation lane sits BELOW the tallest card box (full 16-row
        # column bottom = ROW_Y0 + 15*ROW_DY + 20) and inside the 660 frame.
        box_bottom = q.ROW_Y0 + (q.POINTS_PER_COL - 1) * q.ROW_DY + 20
        self.assertGreater(q.CONTINUATION_Y, box_bottom)
        self.assertLess(q.CONTINUATION_Y, 660)
        self.assertLess(q.CONTINUATION_Y, q.SUMMARY_HEIGHT)

    def test_added_refs_do_not_change_diagram_set(self):
        # pure annotation: no diagram added/removed, only <input> text grows
        project = self._project([101, 102])
        before = len(project.findall("diagram"))
        q.add_continuation_refs(project)
        self.assertEqual(len(project.findall("diagram")), before)


class NcContactVariantTest(unittest.TestCase):
    """T3.1 — NO/NC correctness. The five field switches (level/flow/pressure/
    foot/thermostat) gained separate `_nc` symbol_db entries, exactly like the
    pre-existing limit_switch_nc / push_button_nc pattern. The matcher stays
    data-driven: an `_nc` entry only wins when the tag/description carries an
    explicit NC signal (EN "NC"/"closed" or ES "cerrado"); a plain tag still
    picks the base NO entry, and the existing NO matches are untouched."""

    NC_IDS = ("level_switch_nc", "flow_switch_nc", "pressure_switch_nc",
              "foot_switch_nc", "thermostat_nc")
    # (nc_id, base_id, base_elmt, nc_tag, nc_desc, base_tag, base_desc)
    PAIRS = (
        ("level_switch_nc", "level_switch", "level_switch.elmt",
         "LSH01", "LEVEL SWITCH NC", "LSH01", "LEVEL SWITCH"),
        ("flow_switch_nc", "flow_switch", "flow_switch.elmt",
         "FS01", "FLOW SWITCH NC", "FS01", "FLOW SWITCH"),
        ("pressure_switch_nc", "pressure_switch", "pressure_switch.elmt",
         "PS01", "PRESSURE SWITCH NC", "PS01", "PRESSURE SWITCH"),
        ("foot_switch_nc", "foot_switch", "foot_switch.elmt",
         "FT01", "FOOT SWITCH NC", "FT01", "FOOT SWITCH"),
        ("thermostat_nc", "thermostat", "thermostat.elmt",
         "TS01", "THERMOSTAT NC", "TS01", "THERMOSTAT"),
    )
    # an ES "cerrado" signal must also flip to the NC variant
    ES_NC = (
        ("level_switch_nc", "B01", "NIVEL CERRADO"),
        ("flow_switch_nc", "B01", "CAUDAL CERRADO"),
        ("pressure_switch_nc", "B01", "PRESOSTATO CERRADO"),
        ("foot_switch_nc", "S01", "PEDAL CERRADO"),
        ("thermostat_nc", "B01", "TERMOSTATO CERRADO"),
    )

    @classmethod
    def setUpClass(cls):
        cls.db = q.load_symbol_db()
        cls.by_id = {e["id"]: e for e in cls.db}

    def _term_count(self, elmt_name):
        path = (Path(q.__file__).resolve().parent
                / "symbol_db" / "elements" / elmt_name)
        defn = ET.fromstring(path.read_text(encoding="utf-8"))
        return len(list(defn.find("description").iter("terminal")))

    def test_db_includes_five_nc_entries_with_valid_definitions(self):
        for nc_id in self.NC_IDS:
            with self.subTest(nc_id=nc_id):
                self.assertIn(nc_id, self.by_id, f"{nc_id} missing from db")
                entry = self.by_id[nc_id]
                defn = entry.get("_definition")
                self.assertIsNotNone(defn, f"{nc_id} has no embedded definition")
                self.assertEqual(defn.tag, "definition")
                # element_def must be non-empty XML with a description
                self.assertIsNotNone(defn.find("description"))
                self.assertTrue(len(entry.get("_terminals", [])) > 0)

    def test_nc_terminal_count_matches_base(self):
        for nc_id, base_id, base_elmt, *_ in self.PAIRS:
            with self.subTest(nc_id=nc_id):
                nc_terms = len(self.by_id[nc_id]["_terminals"])
                base_terms = self._term_count(base_elmt)
                self.assertEqual(nc_terms, base_terms,
                                 f"{nc_id} terminal count != {base_id}")
                # each NC switch is a 2-terminal contact
                self.assertEqual(nc_terms, 2)

    def test_nc_dt_matches_base(self):
        for nc_id, base_id, *_ in self.PAIRS:
            with self.subTest(nc_id=nc_id):
                self.assertEqual(self.by_id[nc_id]["dt"],
                                 self.by_id[base_id]["dt"])

    def test_explicit_nc_signal_selects_nc_variant(self):
        for nc_id, _base, _elmt, nc_tag, nc_desc, *_ in self.PAIRS:
            with self.subTest(nc_id=nc_id):
                r = q.match_symbol(self.db, nc_tag, nc_desc, "I")
                self.assertIsNotNone(r)
                self.assertEqual(r["id"], nc_id)

    def test_spanish_cerrado_selects_nc_variant(self):
        for nc_id, tag, desc in self.ES_NC:
            with self.subTest(nc_id=nc_id):
                r = q.match_symbol(self.db, tag, desc, "I")
                self.assertIsNotNone(r)
                self.assertEqual(r["id"], nc_id)

    def test_plain_tag_still_selects_base_no_variant(self):
        for nc_id, base_id, _elmt, _nt, _nd, base_tag, base_desc in self.PAIRS:
            with self.subTest(base_id=base_id):
                r = q.match_symbol(self.db, base_tag, base_desc, "I")
                self.assertIsNotNone(r)
                self.assertEqual(r["id"], base_id,
                                 f"plain {base_tag!r} must not pick {nc_id}")

    def test_existing_no_matches_unchanged_regression(self):
        # guard: ordinary tags for all five still resolve to the NO base,
        # i.e. the new NC keywords never steal a generic match
        for _nc, base_id, _elmt, _nt, _nd, base_tag, base_desc in self.PAIRS:
            with self.subTest(base_id=base_id):
                self.assertEqual(
                    q.match_symbol(self.db, base_tag, base_desc, "I")["id"],
                    base_id)

    def test_limit_switch_nc_is_reachable(self):
        # The pre-existing limit_switch_nc keywords ("limit switch nc")
        # OVERLAP the base ("limit switch"), so both scored an identical key
        # and the base won by id-sort order -> the NC entry could NEVER fire.
        # priority=1 breaks that tie ONLY when the NC keyword actually hits;
        # a plain limit-switch tag carries no NC signal so the base still wins.
        self.assertEqual(
            q.match_symbol(self.db, "FC01", "LIMIT SWITCH NC", "I")["id"],
            "limit_switch_nc")
        self.assertEqual(
            q.match_symbol(self.db, "FC01", "FIN DE CARRERA CERRADO", "I")["id"],
            "limit_switch_nc")
        self.assertEqual(
            q.match_symbol(self.db, "FC01", "LIMIT SWITCH", "I")["id"],
            "limit_switch")


class SparePointRenderingTest(unittest.TestCase):
    """T3.2 — spare-point rendering: every UNUSED card channel (a slot in
    range(mod.points) with no mapped point) is drawn as a plain RESERVA reserve
    terminal so the physical strip is complete, WITHOUT inventing a device/tag,
    and counted SEPARATELY from the matched/mapped floor."""

    @staticmethod
    def _build(pts, *, points=16, kind="DI", name="CARD"):
        mod = SimpleNamespace(rack=1, slot=2, name=name, catalog="FAKE-NODB",
                              kind=kind, points=points, in_byte_base=0,
                              out_byte_base=0, an_in_word_base=0,
                              an_out_word_base=0)
        for pt in pts:
            pt.module = mod
        project = ET.Element("project")
        bom_rows, spare_counter = [], {}
        q.build_folio(project, 101, mod, pts, q.load_symbol_db(), {}, {},
                      wire_scheme="address", wire_counters={},
                      bom_rows=bom_rows, spare_counter=spare_counter)
        return project.find("diagram"), bom_rows, spare_counter, mod

    def _texts(self, d):
        return [i.get("text") for i in d.find("inputs").findall("input")]

    def test_spare_terminal_drawn_for_each_empty_channel(self):
        # one mapped point on a 16-channel card -> 15 spare reserve terminals,
        # each labelled -X1:<channel> with a RESERVA word, NO device symbol.
        pt = SimpleNamespace(module=None, index=0, tag="ZZZ_NOMATCH",
                             direction="I", description="", analog=False)
        d, bom_rows, spare_counter, _ = self._build([pt])
        self.assertEqual(spare_counter["CARD"], 15)
        texts = self._texts(d)
        for ch in range(1, 16):
            self.assertIn(f"-X1:{ch}", texts)
        self.assertEqual(texts.count("RESERVA"), 15)
        # spares add terminals but NO conductors (a spare carries no field wire):
        # only the one mapped generic point's single card->strip conductor.
        self.assertEqual(len(d.find("conductors").findall("conductor")), 1)

    def test_spares_never_inflate_the_floor(self):
        # spare_counter is its OWN counter — sym_counts and the points list are
        # untouched by spares (the floor lives there).
        pt = SimpleNamespace(module=None, index=0, tag="LS1", direction="I",
                             description="", analog=False)
        sym_counts = {}
        mod = SimpleNamespace(rack=1, slot=2, name="CARD", catalog="FAKE-NODB",
                              kind="DI", points=16, in_byte_base=0,
                              out_byte_base=0, an_in_word_base=0,
                              an_out_word_base=0)
        pt.module = mod
        project = ET.Element("project")
        spare_counter = {}
        q.build_folio(project, 101, mod, [pt], q.load_symbol_db(), sym_counts,
                      {}, wire_scheme="address", wire_counters={}, bom_rows=[],
                      spare_counter=spare_counter)
        # the one matched point counts in sym_counts; the 15 spares do NOT.
        self.assertEqual(sum(sym_counts.values()), 1)
        self.assertEqual(spare_counter["CARD"], 15)

    def test_spare_full_extent_inside_card_box_vertical_band(self):
        # POSITIONAL: a spare reserve terminal's FULL pin extent (the borne_2
        # pins span x..x+10, y±10 about the slot hotspot) must stay inside the
        # card box's VERTICAL band — the box is sized to FULL capacity, so even
        # the LAST channel's spare sits within it. Asserted on the real pin
        # extent (not just the hotspot) against the real box rectangle, for the
        # tightest case: a fully-empty 16-channel card (box spans all rows).
        d, _, spare_counter, mod = self._build([])  # no mapped points -> 16 spares
        self.assertEqual(spare_counter["CARD"], 16)
        # the card box rectangle (single column, full capacity)
        x = q.COL_X[0]
        y1 = q.ROW_Y0 - 20
        y2 = q.ROW_Y0 + (mod.points - 1) * q.ROW_DY + 20
        # locate the spare terminal elements and check every pin's absolute y
        terms = d.find("elements").findall("element")
        self.assertEqual(len(terms), 16)            # 16 spares, nothing else
        for el in terms:
            ex, ey = int(el.get("x")), int(el.get("y"))
            for term in el.findall("terminals/terminal"):
                py = ey + int(term.get("y"))
                self.assertGreaterEqual(py, y1,
                    f"spare pin y={py} escapes box top {y1}")
                self.assertLessEqual(py, y2,
                    f"spare pin y={py} escapes box bottom {y2}")
            # and the spare sits in the SAME strip lane as the mapped strip
            # terminals (to the RIGHT of the card box, never overlapping it).
            self.assertEqual(ex, x + q.STRIP_X_OFF)
            self.assertGreater(ex, x + q.BOX_RIGHT)

    def test_spare_bom_rows_have_no_invented_identity(self):
        # GUARDRAIL: every spare BOM row is category 'spare' with designation AND
        # catalog_or_type BLANK (never a fabricated device), description RESERVA.
        pt = SimpleNamespace(module=None, index=2, tag="LS1", direction="I",
                             description="", analog=False)
        _, bom_rows, _, _ = self._build([pt])
        spares = [r for r in bom_rows if r["category"] == "spare"]
        self.assertEqual(len(spares), 15)
        for r in spares:
            self.assertEqual(r["designation"], "")
            self.assertEqual(r["catalog_or_type"], "")
            self.assertEqual(r["description"], "RESERVA")
            self.assertTrue(r["tag"].startswith("-X1:"))
            # the unused channel's EPLAN address is derived (digital I-card)
            self.assertRegex(r["address"], r"^I\d+\.\d+$")

    def test_bornero_lists_spares_marked_reserva_in_channel_order(self):
        # the bornero folio lists mapped AND unused channels in CHANNEL order;
        # the unused ones are marked RESERVA.
        pts = [SimpleNamespace(module=None, index=i, tag=f"T{i}", direction="I",
                               description="", analog=False) for i in (1, 4)]
        mod = SimpleNamespace(rack=1, slot=2, name="A", catalog="FAKE-NODB",
                              kind="DI", points=8)
        for p in pts:
            p.module = mod
        rows = q._bornero_rows(mod, pts)
        self.assertEqual([ch for ch, _t, _s in rows], [0, 1, 2, 3, 4, 5, 6, 7])
        spare_channels = [ch for ch, _t, is_sp in rows if is_sp]
        self.assertEqual(spare_channels, [0, 2, 3, 5, 6, 7])
        for ch, text, is_sp in rows:
            if is_sp:
                self.assertEqual(text, "RESERVA")

    def test_spare_output_is_byte_identical_across_runs(self):
        # DETERMINISM: a repeat build produces byte-identical spare output.
        def render():
            pt = SimpleNamespace(module=None, index=3, tag="ZZZ_NOMATCH",
                                 direction="I", description="", analog=False)
            d, _, _, _ = self._build([pt])
            # strip the volatile element uuids before comparing (uuids are
            # random by design; the SPARE texts/labels are what must be stable)
            return [i.get("text") for i in d.find("inputs").findall("input")]
        self.assertEqual(render(), render())


class WaddingSpareFloorTest(unittest.TestCase):
    """T3.2 floor + spare totals end-to-end on the WADDING_1 fixture: the real
    matched/mapped floor (106 drawn / 75 matched / 0 FP / 10 drawing folios)
    must NOT move, and the SEPARATE spare counter must read 62 (capacity-mapped
    summed over the 10 cards), with REM_AN_IN_1 contributing 14."""

    FIXTURE = Path(__file__).resolve().parent.parent / "Fixtures" / "WADDING_1.L5X"

    def setUp(self):
        if not self.FIXTURE.is_file():
            self.skipTest("WADDING_1.L5X fixture not present")

    def _run(self):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "w.qet"
            with redirect_stderr(buf):
                rc = q.main([str(self.FIXTURE), "-o", str(out)])
            self.assertEqual(rc, 0)
            xml = out.read_text(encoding="utf-8")
        return ET.fromstring(xml), buf.getvalue()

    def test_floor_unchanged_and_spares_reported(self):
        root, err = self._run()
        # the matched/mapped FLOOR must read EXACTLY these numbers (not a proxy)
        self.assertRegex(err, r"points\s*:\s*106\s+drawn")
        self.assertRegex(err, r"symbols\s*:\s*75\s+matched")
        self.assertRegex(err, r"31\s+generic terminal")   # 0 false positives
        drawing = [d for d in root.findall("diagram")
                   if (d.get("title") or "").startswith("R")]
        self.assertEqual(len(drawing), 10)                 # 10 drawing folios
        # the SEPARATE spare counter: 62 reserves over 10 cards
        m = re.search(r"spare\s*:\s*(\d+)\s+reserve terminal", err)
        self.assertIsNotNone(m, f"no spare line in summary:\n{err}")
        self.assertEqual(int(m.group(1)), 62)

    def test_spare_count_matches_capacity_minus_mapped(self):
        # compute the expected spare total straight from the I/O map and assert
        # the per-card REM_AN_IN_1 = 14 plus the project total = 62.
        import logix_to_eplan_csv as l2e
        controller, modules, ctrl_tags, program_tags = l2e.load_l5x(
            str(self.FIXTURE))
        io_mods = l2e.assign_racks_and_addresses(modules)
        points, _ = l2e.collect_points(modules, ctrl_tags, program_tags)
        per = {}
        seen = set()
        for pt in points:
            k = (pt.module.name, pt.direction, pt.index, pt.analog)
            if k in seen:
                continue
            seen.add(k)
            per.setdefault(pt.module.name, []).append(pt)
        total, an_in_1 = 0, None
        for m in io_mods:
            pts = per.get(m.name)
            if not pts:
                continue
            spare = m.points - len({p.index for p in pts})
            total += spare
            if m.name == "REM_AN_IN_1":
                an_in_1 = spare
        self.assertEqual(an_in_1, 14)
        self.assertEqual(total, 62)

    def test_spare_rows_present_in_bom_and_summary(self):
        root, err = self._run()
        # the BOM breakdown line counts spares as their own category
        self.assertRegex(err, r"bom\s*:\s*\d+\s+rows.*\b62 spare\b")
        # a spare row reaches the summary (BOM / device index) folios as a
        # RESERVA line — confirm RESERVA text appears on a BOM folio.
        bom_folios = [d for d in root.findall("diagram")
                      if (d.get("title") or "").startswith("BOM")]
        self.assertTrue(bom_folios)
        reserva_seen = any(
            (i.get("text") or "") == "RESERVA"
            for d in bom_folios for i in d.find("inputs").findall("input"))
        self.assertTrue(reserva_seen, "no RESERVA row reached a BOM summary folio")


if __name__ == "__main__":
    unittest.main()
