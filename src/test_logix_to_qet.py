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

    def test_header_rule_clamped_inside_page_frame(self):
        diagram = self._diagram([q.generic_bom_row(1, tag="T", address="I0.0")])
        shapes = diagram.find("shapes").findall("shape")
        self.assertTrue(shapes)
        for sh in shapes:
            self.assertLessEqual(int(float(sh.get("x2"))), q.SUMMARY_PAGE_WIDTH)

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


if __name__ == "__main__":
    unittest.main()
