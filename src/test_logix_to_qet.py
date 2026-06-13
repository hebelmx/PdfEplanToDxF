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


if __name__ == "__main__":
    unittest.main()
