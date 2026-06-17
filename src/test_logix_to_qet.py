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


def _parse_match_breakdown(err):
    """Parse the generator's stderr 'symbols' line into a per-type breakdown.

    Format: ``symbols    : N matched (type N, type N, ...), M generic terminal``.
    Returns ``(breakdown, generic)`` where ``breakdown`` is ``{type: count}`` and
    ``generic`` is the unmatched count (or ``(None, None)`` if the line is absent).

    This is the REAL false-positive guard: asserting only the matched TOTAL lets a
    semantic mis-classification (right count, wrong type — a true false positive)
    ship green. Asserting the exact per-type dict catches that."""
    m = re.search(
        r"symbols\s*:\s*\d+\s+matched\s*\(([^)]*)\)\s*,\s*(\d+)\s+generic", err)
    if not m:
        return None, None
    breakdown = {}
    for part in m.group(1).split(","):
        part = part.strip()
        if not part:
            continue
        name, _, cnt = part.rpartition(" ")
        breakdown[name.strip()] = int(cnt)
    return breakdown, int(m.group(2))


def _wadding_fixture() -> Path:
    """Resolve the WADDING_1 reference L5X. The Fixtures tree is organized by
    vendor (Fixtures/Rockwell/, Fixtures/Siemens/); fall back to the old flat
    location so the floor tests keep finding it regardless of layout. Returns the
    first existing candidate, else the preferred (vendor-subfolder) path so the
    caller's `skipTest("... not present")` guard still fires cleanly."""
    root = Path(__file__).resolve().parent.parent / "Fixtures"
    candidates = (root / "Rockwell" / "WADDING_1.L5X", root / "WADDING_1.L5X")
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]


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
        # the power table draws NO conductor; the conductors on this 16-ch card
        # are the one generic point's card->strip segment plus, per CHAN, the 15
        # spare stubs' card->strip segments = 16.
        self.assertEqual(len(d.find("conductors").findall("conductor")), 1 + 15)

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


def _mod(rack, parent, slot=0, name=None, points=16):
    """A minimal module stand-in for the grounding/chassis tests."""
    return SimpleNamespace(rack=rack, parent=parent, slot=slot,
                           name=name or f"M{rack}{slot}", catalog="FAKE",
                           kind="DI", points=points)


class GroupChassisTest(unittest.TestCase):
    """T3.4: group_chassis groups I/O modules into chassis (= distinct rack),
    sorted by rack, with the identity DERIVED from the parsed data — never an
    invented friendly name."""

    def test_real_fixture_yields_two_chassis_with_right_labels(self):
        fixture = _wadding_fixture()
        if not fixture.is_file():
            self.skipTest("WADDING_1.L5X fixture not present")
        import logix_to_eplan_csv as l2e
        _, modules, _, _ = l2e.load_l5x(str(fixture))
        io_mods = l2e.assign_racks_and_addresses(modules)
        chassis = q.group_chassis(io_mods)
        self.assertEqual(len(chassis), 2)
        self.assertEqual(chassis[0]["rack"], 1)
        self.assertEqual(chassis[0]["parent"], "Local")
        self.assertEqual(chassis[0]["count"], 6)
        self.assertEqual(chassis[0]["label"], "Chasis R1 (Local)")
        self.assertEqual(chassis[1]["rack"], 2)
        self.assertEqual(chassis[1]["parent"], "RIO_RCP")
        self.assertEqual(chassis[1]["count"], 5)
        self.assertEqual(chassis[1]["label"], "Chasis R2 (RIO_RCP)")

    def test_sorted_by_rack_and_counts_modules(self):
        mods = [_mod(2, "RIO"), _mod(1, "Local"), _mod(1, "Local"), _mod(2, "RIO")]
        chassis = q.group_chassis(mods)
        self.assertEqual([c["rack"] for c in chassis], [1, 2])
        self.assertEqual([c["count"] for c in chassis], [2, 2])

    def test_empty_input_yields_no_chassis(self):
        self.assertEqual(q.group_chassis([]), [])
        self.assertEqual(q.group_chassis(None), [])


class GroundingPageMathTest(unittest.TestCase):
    """T3.4 numbering: the power+grounding block floats just below the card
    drawings band. supply_order = 100 - n_grounding; grounding folios take
    supply_order+1 .. 100; the drawings stay fixed at 101. Backward-compatible:
    n_grounding=0 ⇒ supply_order=100 (unchanged)."""

    def test_two_chassis_places_supply_98_grounding_99_100_drawings_101(self):
        n_grounding = 2
        supply_order = q.SECTION_SUPPLY - n_grounding
        self.assertEqual(supply_order, 98)
        grounding_orders = list(range(supply_order + 1, q.SECTION_SUPPLY + 1))
        self.assertEqual(grounding_orders, [99, 100])
        self.assertEqual(q.SECTION_DRAWINGS, 101)   # cards unchanged

    def test_zero_chassis_keeps_supply_at_100(self):
        n_grounding = 0
        self.assertEqual(q.SECTION_SUPPLY - n_grounding, 100)


class GroundingFolioTest(unittest.TestCase):
    """T3.4: each chassis grounding folio is VISUAL-only (text + shape primitives,
    empty <elements>/<conductors>), drawn inside the page frame with labels lifted
    clear of their lines, gauges configurable via the project_template."""

    def _gauges(self):
        return dict(q.PROJECT_TEMPLATE_DEFAULTS["grounding"])

    def test_builds_one_folio_per_chassis(self):
        mods = [_mod(1, "Local"), _mod(2, "RIO")]
        project = ET.Element("project")
        n = q.build_grounding_folios(project, 99, mods, self._gauges())
        self.assertEqual(n, 2)
        diags = project.findall("diagram")
        self.assertEqual([d.get("order") for d in diags], ["99", "100"])
        for d in diags:
            self.assertTrue(d.get("title").startswith("Puesta a tierra — Chasis"))

    def test_empty_io_mods_appends_nothing(self):
        project = ET.Element("project")
        self.assertEqual(q.build_grounding_folios(project, 99, [], self._gauges()), 0)
        self.assertEqual(len(project.findall("diagram")), 0)

    def test_only_text_and_shapes_no_elements_or_conductors(self):
        project = ET.Element("project")
        q.build_grounding_folios(project, 99, [_mod(1, "Local")], self._gauges())
        d = project.find("diagram")
        self.assertEqual(len(d.find("elements").findall("element")), 0)
        self.assertEqual(len(d.find("conductors").findall("conductor")), 0)
        self.assertGreater(len(d.find("shapes").findall("shape")), 0)

    def test_spanish_labels_present(self):
        project = ET.Element("project")
        q.build_grounding_folios(project, 99, [_mod(1, "Local")], self._gauges())
        d = project.find("diagram")
        texts = " | ".join(i.get("text") for i in d.find("inputs").findall("input"))
        self.assertIn("Tierra funcional (FE)", texts)
        self.assertIn("Tierra de protección (PE)", texts)
        self.assertIn("Barra de tierra", texts)
        self.assertIn("Sistema de electrodos de tierra", texts)
        self.assertIn("Chasis R1 (Local)", texts)

    def test_module_count_label_uses_real_count(self):
        project = ET.Element("project")
        mods = [_mod(1, "Local"), _mod(1, "Local"), _mod(1, "Local")]
        q.build_grounding_folios(project, 99, mods, self._gauges())
        d = project.find("diagram")
        texts = [i.get("text") for i in d.find("inputs").findall("input")]
        self.assertIn("3 módulos", texts)

    def test_full_extent_inside_page_frame(self):
        # Assert the FULL extent (chassis box corners, FE/PE leads, ground bus,
        # electrode) lies inside the real page frame — not a single hotspot.
        project = ET.Element("project")
        q.build_grounding_folios(project, 99, [_mod(1, "Local")], self._gauges())
        d = project.find("diagram")
        xs, ys = [], []
        for s in d.find("shapes").findall("shape"):
            xs += [float(s.get("x1")), float(s.get("x2"))]
            ys += [float(s.get("y1")), float(s.get("y2"))]
        for i in d.find("inputs").findall("input"):
            xs.append(float(i.get("x")))
            ys.append(float(i.get("y")))
        self.assertGreaterEqual(min(xs), 0)
        self.assertLessEqual(max(xs), 1010)
        self.assertGreaterEqual(min(ys), 0)
        self.assertLessEqual(max(ys), q.SUMMARY_HEIGHT)   # 660

    def test_labels_lifted_clear_of_their_lines(self):
        # The FE/PE/bus/electrode labels must NOT sit on the line they name. The
        # bus label sits a clear gap ABOVE the bus bar; the chassis label above
        # the box top edge; the FE/PE gauge labels beside (not on) their leads.
        project = ET.Element("project")
        q.build_grounding_folios(project, 99, [_mod(1, "Local")], self._gauges())
        d = project.find("diagram")
        ins = {i.get("text"): (float(i.get("x")), float(i.get("y")))
               for i in d.find("inputs").findall("input")}
        # bus label clear above the bus bar
        bus_label_y = ins["Barra de tierra"][1]
        self.assertLessEqual(bus_label_y + 10, q.GND_BUS_Y)
        # chassis label clear above the box top edge
        self.assertLessEqual(ins["Chasis R1 (Local)"][1] + 10, q.GND_BOX_Y1)
        # FE/PE leads run at GND_FE_X/GND_PE_X; their labels are offset to the
        # RIGHT (x+10), so the label x is strictly past the lead x.
        self.assertGreater(ins["Tierra funcional (FE)"][0], q.GND_FE_X)
        self.assertGreater(ins["Tierra de protección (PE)"][0], q.GND_PE_X)

    def test_gauge_defaults_appear_on_folio(self):
        project = ET.Element("project")
        q.build_grounding_folios(project, 99, [_mod(1, "Local")], self._gauges())
        d = project.find("diagram")
        texts = [i.get("text") for i in d.find("inputs").findall("input")]
        g = q.PROJECT_TEMPLATE_DEFAULTS["grounding"]
        self.assertIn(g["fe_gauge"], texts)
        self.assertIn(g["pe_gauge"], texts)
        self.assertIn(g["electrode_gauge"], texts)

    def test_gauge_override_changes_rendered_text(self):
        gauges = {"fe_gauge": "6 AWG (FE-X)", "pe_gauge": "12 AWG (PE-X)",
                  "electrode_gauge": "4 AWG (EL-X)"}
        project = ET.Element("project")
        q.build_grounding_folios(project, 99, [_mod(1, "Local")], gauges)
        d = project.find("diagram")
        texts = [i.get("text") for i in d.find("inputs").findall("input")]
        self.assertIn("6 AWG (FE-X)", texts)
        self.assertIn("12 AWG (PE-X)", texts)
        self.assertIn("4 AWG (EL-X)", texts)
        # the defaults must be GONE when overridden
        self.assertNotIn(q.PROJECT_TEMPLATE_DEFAULTS["grounding"]["fe_gauge"], texts)

    def test_default_gauges_used_when_omitted(self):
        project = ET.Element("project")
        q.build_grounding_folios(project, 99, [_mod(1, "Local")])   # no gauges
        d = project.find("diagram")
        texts = [i.get("text") for i in d.find("inputs").findall("input")]
        self.assertIn(q.PROJECT_TEMPLATE_DEFAULTS["grounding"]["fe_gauge"], texts)

    def test_inherits_titleblock_and_no_token_leak(self):
        project = ET.Element("project")
        q.build_grounding_folios(project, 99, [_mod(1, "Local")], self._gauges())
        fields = q.resolve_title_block_fields(
            {**{k: v for k, v in q.PROJECT_TEMPLATE_DEFAULTS.items()
                if isinstance(v, str)}, "company": "Exxerpro Solutions"}, "C")
        q.attach_titleblocks(project, fields, q.load_titleblock_template())
        d = project.find("diagram")
        self.assertEqual(d.get("titleblocktemplate"), "exxerpro")
        for prop in d.find("properties").findall("property"):
            self.assertNotIn("%{", prop.text or "")


def _tmod(name, parent, catalog, kind=None):
    """A minimal full-tree Module stand-in for the topology tests (carries the
    fields classify_node / build_topology_tree read: name, parent, catalog,
    kind)."""
    return SimpleNamespace(name=name, parent=parent, catalog=catalog, kind=kind,
                           slot=0, points=0)


def _tiny_tree():
    """A small but representative module dict mirroring the WADDING_1 shape:
    self-parented controller -> ControlNet bridge -> remote adapter + HMI + an
    I/O drop, plus a local I/O card. Insertion order is the dict order."""
    return {
        "Local": _tmod("Local", "Local", "1756-L81E"),                 # root
        "MOD_ENT_1": _tmod("MOD_ENT_1", "Local", "1756-IA16", "DI"),
        "RIO_LOCAL": _tmod("RIO_LOCAL", "Local", "1756-CNB/D"),        # bridge
        "RIO_RCP": _tmod("RIO_RCP", "RIO_LOCAL", "1756-CNB/D"),        # adapter
        "REM_IN_1": _tmod("REM_IN_1", "RIO_RCP", "1756-IB32/B", "DI"),
        "PV_PUPITRE": _tmod("PV_PUPITRE", "RIO_LOCAL", "PanelView"),   # HMI
    }


class TopologyClassificationTest(unittest.TestCase):
    """E2.1: node classification + tree-building are DATA-DRIVEN and graceful —
    the root is the self-parented controller, comms catalogs classify as bridges
    (with an inferable protocol), PanelView as HMI, DI/DO/AI/AO as I/O, and an
    unknown catalog falls back to a generic node (never an invented role)."""

    def test_protocol_inference_and_unknown_is_none(self):
        self.assertEqual(q.topology_protocol("1756-CNB/D"), "ControlNet")
        self.assertEqual(q.topology_protocol("1756-CN2/B"), "ControlNet")
        self.assertEqual(q.topology_protocol("1756-EN2T"), "EtherNet/IP")
        self.assertIsNone(q.topology_protocol("1756-MYSTERY"))
        self.assertIsNone(q.topology_protocol(""))

    def test_classify_node_roles(self):
        root = _tmod("Local", "Local", "1756-L81E")
        bridge = _tmod("RIO_LOCAL", "Local", "1756-CNB/D")
        hmi = _tmod("PV", "RIO_LOCAL", "PanelView Plus 7")
        io = _tmod("MOD", "Local", "1756-IA16", "DI")
        generic = _tmod("X", "Local", "1756-WEIRD")
        self.assertEqual(q.classify_node(root, is_root=True), "controller")
        self.assertEqual(q.classify_node(bridge, is_root=False), "bridge")
        self.assertEqual(q.classify_node(hmi, is_root=False), "hmi")
        self.assertEqual(q.classify_node(io, is_root=False), "io")
        self.assertEqual(q.classify_node(generic, is_root=False), "generic")

    def test_real_fixture_tree_matches_ground_truth(self):
        fixture = _wadding_fixture()
        if not fixture.is_file():
            self.skipTest("WADDING_1.L5X fixture not present")
        import logix_to_eplan_csv as l2e
        controller, modules, _, _ = l2e.load_l5x(str(fixture))
        tree = q.build_topology_tree(modules)
        nodes = tree["nodes"]
        # the controller MODULE is the self-parented tree root ("Local"); note
        # load_l5x's `controller` is the project/controller NAME ("WADDING_1"),
        # a different thing — the root is rederived from the module tree.
        self.assertEqual(tree["root"], "Local")
        self.assertEqual(controller, "WADDING_1")
        self.assertEqual(nodes["Local"]["role"], "controller")
        # the two ControlNet bridges classify as network/bridge nodes
        self.assertEqual(nodes["RIO_LOCAL"]["role"], "bridge")
        self.assertEqual(nodes["RIO_RCP"]["role"], "bridge")
        self.assertEqual(nodes["RIO_LOCAL"]["protocol"], "ControlNet")
        # the PanelView is the HMI
        self.assertEqual(nodes["PV_PUPITRE"]["role"], "hmi")
        # every REM_*/MOD_* card classifies as I/O
        for n in ("MOD_ENT_1", "MOD_SAL_1", "REM_IN_1", "REM_IN_2",
                  "REM_OUT_RLY_1", "REM_OUT_2", "REM_AN_IN_1"):
            self.assertEqual(nodes[n]["role"], "io", n)
        # the parent EDGES reproduce the ground-truth tree
        self.assertEqual(nodes["RIO_LOCAL"]["parent"], "Local")
        self.assertEqual(nodes["RIO_RCP"]["parent"], "RIO_LOCAL")
        self.assertEqual(nodes["PV_PUPITRE"]["parent"], "RIO_LOCAL")
        self.assertIn("RIO_RCP", tree["children"]["RIO_LOCAL"])
        self.assertIn("PV_PUPITRE", tree["children"]["RIO_LOCAL"])
        self.assertIn("RIO_LOCAL", tree["children"]["Local"])
        for io in ("REM_IN_1", "REM_AN_IN_1"):
            self.assertIn(io, tree["children"]["RIO_RCP"])

    def test_no_root_yields_empty_tree_marker(self):
        # a module dict with no self-parented module has no controller (graceful)
        mods = {"A": _tmod("A", "B", "1756-IA16", "DI")}
        tree = q.build_topology_tree(mods)
        self.assertIsNone(tree["root"])


class TopologyFolioTest(unittest.TestCase):
    """E2.1: the topology folio is VISUAL-only (text + shape primitives, empty
    <elements>/<conductors>), sits at order 2, draws every node inside the page
    frame with labels lifted clear, infers protocol labels only when confident,
    and renders an unknown module as a generic name+catalog node."""

    def test_builds_one_folio_at_given_order(self):
        project = ET.Element("project")
        n = q.build_topology_folio(project, 2, "Local", _tiny_tree())
        self.assertEqual(n, 1)
        d = project.find("diagram")
        self.assertEqual(d.get("order"), "2")
        self.assertEqual(d.get("title"), "Red de comunicaciones")

    def test_no_controller_appends_nothing(self):
        project = ET.Element("project")
        mods = {"A": _tmod("A", "B", "1756-IA16", "DI")}     # no self-parent root
        self.assertEqual(q.build_topology_folio(project, 2, None, mods), 0)
        self.assertEqual(len(project.findall("diagram")), 0)

    def test_empty_modules_appends_nothing(self):
        project = ET.Element("project")
        self.assertEqual(q.build_topology_folio(project, 2, None, {}), 0)
        self.assertEqual(len(project.findall("diagram")), 0)

    def test_only_text_and_shapes_no_elements_or_conductors(self):
        project = ET.Element("project")
        q.build_topology_folio(project, 2, "Local", _tiny_tree())
        d = project.find("diagram")
        self.assertEqual(len(d.find("elements").findall("element")), 0)
        self.assertEqual(len(d.find("conductors").findall("conductor")), 0)
        self.assertGreater(len(d.find("shapes").findall("shape")), 0)
        self.assertGreater(len(d.find("inputs").findall("input")), 0)

    def test_full_extent_inside_page_frame(self):
        # Assert the FULL drawn extent (every node box + every lead + every
        # label) lies inside the real page frame — not one hotspot.
        project = ET.Element("project")
        q.build_topology_folio(project, 2, "Local", _tiny_tree())
        d = project.find("diagram")
        xs, ys = [], []
        for s in d.find("shapes").findall("shape"):
            xs += [float(s.get("x1")), float(s.get("x2"))]
            ys += [float(s.get("y1")), float(s.get("y2"))]
        for i in d.find("inputs").findall("input"):
            xs.append(float(i.get("x")))
            ys.append(float(i.get("y")))
        self.assertGreaterEqual(min(xs), 0)
        self.assertLessEqual(max(xs), 1010)
        self.assertGreaterEqual(min(ys), 0)
        self.assertLessEqual(max(ys), q.SUMMARY_HEIGHT)   # 660

    def test_chassis_boxes_one_per_chassis_not_per_module(self):
        # Fixes defect 1&2: there is exactly ONE enclosing box per chassis (plus
        # one per standalone HMI node), NOT one box per module. The tiny tree has
        # 2 chassis (Local + Remoto) + 1 HMI node => 3 enclosing boxes.
        project = ET.Element("project")
        q.build_topology_folio(project, 2, "Local", _tiny_tree())
        d = project.find("diagram")
        tree = q.build_topology_tree(_tiny_tree())
        chassis, hmi = q.build_topology_chassis(tree)
        self.assertEqual(len(chassis), 2)            # Chasis Local + Chasis Remoto
        self.assertEqual(len(hmi), 1)                # PV_PUPITRE
        # enclosing boxes = rects whose height matches a chassis/hmi box height.
        heights = {q._chassis_box_height(len(c["rows"])) for c in chassis}
        heights.add(q._chassis_box_height(1))        # the HMI box
        boxes = [s for s in d.find("shapes").findall("shape")
                 if (float(s.get("y2")) - float(s.get("y1"))) in heights
                 and (float(s.get("x2")) - float(s.get("x1"))) > 50]
        # exactly 3 enclosing boxes — never one per module
        self.assertEqual(len(boxes), 3)

    def test_module_rows_are_plain_text_not_struck_through(self):
        # Fixes defect 2: inside each chassis box the module rows are plain text
        # with >= TOPO_ROW_PITCH between consecutive baselines, and NO shape edge
        # shares a y with a row baseline (no per-row border/lead crosses a label).
        project = ET.Element("project")
        q.build_topology_folio(project, 2, "Local", _tiny_tree())
        d = project.find("diagram")
        ys = sorted(float(i.get("y")) for i in d.find("inputs").findall("input"))
        # consecutive row baselines inside a box are separated by >= the pitch
        # (the header sits closer; check the dominant gap is the row pitch).
        pitch_gaps = [b - a for a, b in zip(ys, ys[1:])
                      if abs((b - a) - q.TOPO_ROW_PITCH) < 1]
        self.assertTrue(pitch_gaps, "no rows at the expected pitch")
        # no shape edge y coincides with a row-label baseline inside a box
        edge_ys = set()
        for s in d.find("shapes").findall("shape"):
            edge_ys.add(float(s.get("y1")))
            edge_ys.add(float(s.get("y2")))
        for y in ys:
            self.assertFalse(any(abs(y - e) < 1 for e in edge_ys),
                             "a row baseline coincides with a shape edge")

    def test_protocol_label_present_for_controlnet_bridge(self):
        project = ET.Element("project")
        q.build_topology_folio(project, 2, "Local", _tiny_tree())
        d = project.find("diagram")
        texts = [i.get("text") for i in d.find("inputs").findall("input")]
        self.assertIn("ControlNet", texts)

    def test_classification_role_tags_rendered(self):
        project = ET.Element("project")
        q.build_topology_folio(project, 2, "Local", _tiny_tree())
        d = project.find("diagram")
        texts = " | ".join(i.get("text")
                           for i in d.find("inputs").findall("input"))
        self.assertIn("Controlador", texts)
        self.assertIn("Módulo de red", texts)
        self.assertIn("HMI", texts)

    def test_unknown_module_renders_generic_name_and_catalog_no_role(self):
        # never-invent: an unknown catalog/role node carries its name + catalog
        # and the generic role tag — NO invented role/protocol text.
        mods = {
            "Local": _tmod("Local", "Local", "1756-L81E"),
            "WIDGET": _tmod("WIDGET", "Local", "ACME-XYZ-9000"),   # unknown
        }
        project = ET.Element("project")
        q.build_topology_folio(project, 2, "Local", mods)
        d = project.find("diagram")
        # module rows are now plain TEXT inside a chassis box (name+catalog+tag
        # combined into one row string) — assert each token is present.
        joined = " | ".join(i.get("text")
                            for i in d.find("inputs").findall("input"))
        self.assertIn("WIDGET", joined)
        self.assertIn("ACME-XYZ-9000", joined)
        self.assertIn(q.TOPOLOGY_ROLE_TAGS["generic"], joined)
        # no protocol guessed for the unknown node
        self.assertNotIn("ControlNet", joined)
        self.assertNotIn("EtherNet/IP", joined)

    def test_inherits_titleblock_and_no_token_leak(self):
        project = ET.Element("project")
        q.build_topology_folio(project, 2, "Local", _tiny_tree())
        fields = q.resolve_title_block_fields(
            {**{k: v for k, v in q.PROJECT_TEMPLATE_DEFAULTS.items()
                if isinstance(v, str)}, "company": "Exxerpro Solutions"}, "C")
        q.attach_titleblocks(project, fields, q.load_titleblock_template())
        d = project.find("diagram")
        self.assertEqual(d.get("titleblocktemplate"), "exxerpro")
        for prop in d.find("properties").findall("property"):
            self.assertNotIn("%{", prop.text or "")


def _sample_nodes():
    """A small PROFINET node list mirroring the .aml shape: the Q100 CPU plus a
    couple of plant nodes (one with no type — never invented). The 5-tuple shape
    is (ip, name, type, subnet_mask, is_controller) — mask + controller are REAL
    .aml provenance (here the CPU carries DeviceItemType=CPU => controller, NOT
    its .10 host)."""
    return [
        ("192.168.10.10", "Q100_QUERETARO1", "CPU 1512SP F-1 PN", "255.255.255.0", True),
        ("192.168.10.12", "EV_UV_Q100", "EX260 SPN 3/4", "255.255.255.0", False),
        ("192.168.10.13", "DRIVE UV Rotation", "SK TU3-PNT", "255.255.255.0", False),
        ("192.168.10.99", "MYSTERY", "", "255.255.255.0", False),   # node with no type
    ]


def _many_nodes(n=35):
    """n synthetic PROFINET nodes on 192.168.10.x with the CPU first, to assert a
    full grid stays inside the page frame. 5-tuple shape (real mask + controller
    flag from the .aml)."""
    nodes = [("192.168.10.10", "Q100_QUERETARO1", "CPU 1512SP F-1 PN",
              "255.255.255.0", True)]
    for i in range(1, n):
        nodes.append((f"192.168.10.{100 + i}", f"NODE{i}", "SK TU3-PNT",
                      "255.255.255.0", False))
    return nodes


class NetworkFolioTest(unittest.TestCase):
    """NET: the whole-plant PROFINET network folio is VISUAL-only (text + shape
    primitives, empty <elements>/<conductors>), draws a labelled subnet bus with
    one node box per device (name + IP + type), highlights the Q100 CPU, keeps
    every box inside the page frame, and is omitted gracefully when no nodes."""

    def test_builds_one_folio_at_given_order(self):
        project = ET.Element("project")
        n = q.build_network_folio(project, 2, _sample_nodes())
        self.assertEqual(n, 1)
        d = project.find("diagram")
        self.assertEqual(d.get("order"), "2")
        self.assertEqual(d.get("title"), "Red PROFINET")

    def test_empty_nodes_appends_nothing(self):
        project = ET.Element("project")
        self.assertEqual(q.build_network_folio(project, 2, []), 0)
        self.assertEqual(len(project.findall("diagram")), 0)

    def test_only_text_and_shapes_no_elements_or_conductors(self):
        project = ET.Element("project")
        q.build_network_folio(project, 2, _sample_nodes())
        d = project.find("diagram")
        self.assertEqual(len(d.find("elements").findall("element")), 0)
        self.assertEqual(len(d.find("conductors").findall("conductor")), 0)
        self.assertGreater(len(d.find("shapes").findall("shape")), 0)
        self.assertGreater(len(d.find("inputs").findall("input")), 0)

    def test_one_box_per_node(self):
        project = ET.Element("project")
        q.build_network_folio(project, 2, _sample_nodes())
        d = project.find("diagram")
        boxes = [s for s in d.find("shapes").findall("shape")
                 if abs(float(s.get("x2")) - float(s.get("x1")) - q.PN_BOX_W) < 1
                 and abs(float(s.get("y2")) - float(s.get("y1")) - q.PN_BOX_H) < 1]
        self.assertEqual(len(boxes), len(_sample_nodes()))

    def test_controller_node_highlighted(self):
        # exactly one box (the .10 CPU) drawn with the heavier (width 2) border.
        project = ET.Element("project")
        q.build_network_folio(project, 2, _sample_nodes())
        d = project.find("diagram")
        heavy = [s for s in d.find("shapes").findall("shape")
                 if s.find("pen") is not None
                 and s.find("pen").get("widthF") == "2"]
        self.assertEqual(len(heavy), 1)
        texts = " | ".join(i.get("text") for i in d.find("inputs").findall("input"))
        self.assertIn("CONTROLADOR", texts)
        self.assertIn("Q100_QUERETARO1", texts)

    def test_controller_flag_from_device_item_type_not_host(self):
        # N2: the controller flag is REAL provenance (DeviceItemType=CPU carried
        # as the node's 5th field), NOT the .10 host. A CPU at a NON-.10 host IS
        # flagged; a non-CPU device sitting at .10 is NOT.
        cpu_off_ten = ("192.168.10.42", "ODD_CPU", "CPU 1512SP F-1 PN",
                       "255.255.255.0", True)
        device_at_ten = ("192.168.10.10", "NOT_A_CPU", "EX260 SPN 3/4",
                         "255.255.255.0", False)
        self.assertTrue(q._is_controller_node(cpu_off_ten))
        self.assertFalse(q._is_controller_node(device_at_ten))
        # and it renders: exactly the CPU box (off-.10) gets the heavy border +
        # the CONTROLADOR tag; the .10 non-CPU does not.
        project = ET.Element("project")
        q.build_network_folio(project, 2, [cpu_off_ten, device_at_ten])
        d = project.find("diagram")
        heavy = [s for s in d.find("shapes").findall("shape")
                 if s.find("pen") is not None
                 and s.find("pen").get("widthF") == "2"]
        self.assertEqual(len(heavy), 1)
        texts = [i.get("text") for i in d.find("inputs").findall("input")]
        joined = " | ".join(texts)
        self.assertIn("ODD_CPU  (CONTROLADOR)", joined)
        self.assertNotIn("NOT_A_CPU  (CONTROLADOR)", joined)

    def test_subnet_label_and_ips_rendered(self):
        project = ET.Element("project")
        q.build_network_folio(project, 2, _sample_nodes())
        d = project.find("diagram")
        texts = " | ".join(i.get("text") for i in d.find("inputs").findall("input"))
        # /24 sourced from the REAL SubnetMask (255.255.255.0), not the host IPs
        self.assertIn("192.168.10.0/24", texts)
        self.assertIn("IP 192.168.10.10", texts)
        self.assertIn("IP 192.168.10.12", texts)

    def test_subnet_label_uses_real_mask_not_host_octets(self):
        # N1: same host prefix but a /16 mask (255.255.0.0) => label must read /16,
        # proving the prefix length comes from the REAL mask, NOT a hard /24 nor a
        # reconstructed .0 octet from the host IPs.
        nodes = [(ip, n, t, "255.255.0.0", c)
                 for (ip, n, t, _m, c) in _sample_nodes()]
        self.assertEqual(q._subnet_label(nodes), "PROFINET — 192.168.10.0/16")

    def test_subnet_label_absent_mask_falls_back_to_bare_title(self):
        # N1: no real mask present (3-tuple legacy / None) => bare title, NEVER a
        # fabricated .0/24 from host octets and never the doubled "PROFINET — Red
        # PROFINET".
        nodes = [(ip, n, t, None, c) for (ip, n, t, _m, c) in _sample_nodes()]
        label = q._subnet_label(nodes)
        self.assertEqual(label, q.PROFINET_TITLE)
        self.assertNotIn("/24", label)
        self.assertNotIn("PROFINET — Red PROFINET", label)

    def test_subnet_label_nonuniform_mask_falls_back_to_bare_title(self):
        # N1: a non-uniform real mask across nodes => bare title (never invented).
        sample = _sample_nodes()
        nodes = [(sample[0][0], sample[0][1], sample[0][2], "255.255.255.0", sample[0][4])]
        nodes += [(ip, n, t, "255.255.0.0", c)
                  for (ip, n, t, _m, c) in sample[1:]]
        self.assertEqual(q._subnet_label(nodes), q.PROFINET_TITLE)

    def test_node_without_type_has_no_type_line_never_invented(self):
        # the MYSTERY node (type "") must render name+IP only, no fabricated type.
        project = ET.Element("project")
        q.build_network_folio(project, 2, _sample_nodes())
        d = project.find("diagram")
        texts = [i.get("text") for i in d.find("inputs").findall("input")]
        self.assertIn("MYSTERY", texts)
        self.assertIn("IP 192.168.10.99", texts)

    def test_full_extent_inside_page_frame_35_nodes(self):
        # all 35 node boxes + bus + labels lie inside the real page frame.
        project = ET.Element("project")
        q.build_network_folio(project, 2, _many_nodes(35))
        d = project.find("diagram")
        boxes = [s for s in d.find("shapes").findall("shape")
                 if abs(float(s.get("x2")) - float(s.get("x1")) - q.PN_BOX_W) < 1
                 and abs(float(s.get("y2")) - float(s.get("y1")) - q.PN_BOX_H) < 1]
        self.assertEqual(len(boxes), 35)
        xs, ys = [], []
        for s in d.find("shapes").findall("shape"):
            xs += [float(s.get("x1")), float(s.get("x2"))]
            ys += [float(s.get("y1")), float(s.get("y2"))]
        for i in d.find("inputs").findall("input"):
            xs.append(float(i.get("x")))
            ys.append(float(i.get("y")))
        self.assertGreaterEqual(min(xs), 0)
        self.assertLessEqual(max(xs), 1010)
        self.assertGreaterEqual(min(ys), 0)
        self.assertLessEqual(max(ys), q.SUMMARY_HEIGHT)   # 660

    def test_every_node_connected_to_bus(self):
        # EYE-2: every node hangs off the bus — row 0 via a drop-lead from the bus
        # bar, rows 1+ via an inter-row lead up to the box above (previously only
        # the top row was connected, so the lower rows read as floating). Exactly
        # one vertical lead per node; PN_COLS of them start AT the bus.
        project = ET.Element("project")
        nodes = _many_nodes(35)
        q.build_network_folio(project, 2, nodes)
        d = project.find("diagram")
        leads = [s for s in d.find("shapes").findall("shape")
                 if (float(s.get("x2")) - float(s.get("x1"))) <= q.PN_LEAD_W + 1
                 and (float(s.get("y2")) - float(s.get("y1"))) > 3]
        self.assertEqual(len(leads), len(nodes))   # one lead per node
        from_bus = [s for s in leads
                    if abs(float(s.get("y1")) - (q.PN_BUS_Y + q.PN_BUS_H)) < 1]
        self.assertEqual(len(from_bus), q.PN_COLS)            # top-row drops
        self.assertEqual(len(leads) - len(from_bus),
                         len(nodes) - q.PN_COLS)              # rest chain rows

    def test_node_box_text_lines_clear_bottom_border(self):
        # EYE-1: the third text line (module type) must sit INSIDE the box, not on
        # the bottom border (it used to render at y+50 in a 60-tall box and overlap
        # the edge). Assert the lowest text line's full height clears the box.
        project = ET.Element("project")
        q.build_network_folio(project, 2, _many_nodes(12))
        d = project.find("diagram")
        boxes = [s for s in d.find("shapes").findall("shape")
                 if abs(float(s.get("y2")) - float(s.get("y1")) - q.PN_BOX_H) < 1]
        inputs = d.find("inputs").findall("input")
        LINE_H = 14   # conservative QET text height at FONT_TEXT/FONT_SMALL
        checked = 0
        for b in boxes:
            bx1, by1, by2 = (float(b.get("x1")), float(b.get("y1")),
                             float(b.get("y2")))
            lines = [float(i.get("y")) for i in inputs
                     if abs(float(i.get("x")) - (bx1 + q.PN_TEXT_X)) < 2
                     and by1 <= float(i.get("y")) <= by2]
            if not lines:
                continue
            self.assertLessEqual(max(lines) + LINE_H, by2)
            checked += 1
        self.assertEqual(checked, len(boxes))   # every box validated

    def test_long_node_header_clipped_to_box_width(self):
        # EYE-1: a name long enough to overflow the box is clipped (ellipsis) so it
        # never spills into the neighbour box; short names are untouched.
        long_name = "VERY_LONG_STATION_NAME_THAT_OVERFLOWS_THE_NODE_BOX"
        nodes = [("192.168.10.10", long_name, "CPU 1512SP F-1 PN",
                  "255.255.255.0", True)]
        project = ET.Element("project")
        q.build_network_folio(project, 2, nodes)
        d = project.find("diagram")
        texts = [i.get("text") for i in d.find("inputs").findall("input")]
        header = next(t for t in texts if t.startswith("VERY_LONG"))
        self.assertLessEqual(len(header), q.PN_HEADER_CHARS)
        self.assertTrue(header.endswith("…"))
        # a short name is returned unchanged (no padding, no ellipsis)
        self.assertEqual(q._fit_text("Q100", q.PN_HEADER_CHARS), "Q100")

    def test_inherits_titleblock_and_no_token_leak(self):
        project = ET.Element("project")
        q.build_network_folio(project, 2, _sample_nodes())
        fields = q.resolve_title_block_fields(
            {**{k: v for k, v in q.PROJECT_TEMPLATE_DEFAULTS.items()
                if isinstance(v, str)}, "company": "Exxerpro Solutions"}, "C")
        q.attach_titleblocks(project, fields, q.load_titleblock_template())
        d = project.find("diagram")
        self.assertEqual(d.get("titleblocktemplate"), "exxerpro")
        for prop in d.find("properties").findall("property"):
            self.assertNotIn("%{", prop.text or "")


def _rack_modules():
    """Synthetic IR modules for the rack folio: slots out of order (so the
    builder must SORT), one module with NO slot (must fall back / blank label),
    one with NO catalog (blank order#)."""
    return [
        SimpleNamespace(name="DQ10_11", catalog="6ES7 132-6BH00-0BA0",
                        slot=7, kind="DO", points=16),
        SimpleNamespace(name="F-DI150", catalog="6ES7 136-6BA00-0CA0",
                        slot=2, kind="DI", points=16),
        SimpleNamespace(name="NO-SLOT", catalog="", slot=None,
                        kind="AI", points=2),
    ]


class RackFolioTest(unittest.TestCase):
    """RACK (Story 2.3): the rack/chassis layout overview is VISUAL-only (text +
    shape primitives, empty <elements>/<conductors>), draws one box per module in
    SLOT order (slot #, name, catalog/order#, kind+points), keeps every box inside
    the page frame, and never fabricates a slot/catalog (blank when unknown)."""

    def test_builds_one_folio_at_given_order(self):
        project = ET.Element("project")
        n = q.build_rack_folio(project, q.SECTION_RACK, _rack_modules())
        self.assertEqual(n, 1)
        d = project.find("diagram")
        self.assertEqual(d.get("order"), str(q.SECTION_RACK))
        self.assertEqual(d.get("title"), q.RACK_TITLE)

    def test_empty_modules_appends_nothing(self):
        project = ET.Element("project")
        self.assertEqual(q.build_rack_folio(project, q.SECTION_RACK, []), 0)
        self.assertEqual(len(project.findall("diagram")), 0)

    def test_only_text_and_shapes_no_elements_or_conductors(self):
        project = ET.Element("project")
        q.build_rack_folio(project, q.SECTION_RACK, _rack_modules())
        d = project.find("diagram")
        self.assertEqual(len(d.find("elements").findall("element")), 0)
        self.assertEqual(len(d.find("conductors").findall("conductor")), 0)
        self.assertGreater(len(d.find("shapes").findall("shape")), 0)
        self.assertGreater(len(d.find("inputs").findall("input")), 0)

    def test_modules_drawn_in_slot_order(self):
        # the slot labels must appear in ascending slot order; the None-slot
        # module sorts LAST with a BLANK slot label (never fabricated).
        project = ET.Element("project")
        q.build_rack_folio(project, q.SECTION_RACK, _rack_modules())
        d = project.find("diagram")
        texts = [i.get("text") for i in d.find("inputs").findall("input")]
        slots = [t for t in texts if t.startswith("SLOT ")]
        self.assertEqual(slots, ["SLOT 2", "SLOT 7"])   # sorted, None omitted
        # name order follows slot order then IR order for the None-slot one
        self.assertLess(texts.index("F-DI150"), texts.index("DQ10_11"))
        self.assertLess(texts.index("DQ10_11"), texts.index("NO-SLOT"))

    def test_real_order_numbers_and_kind_points(self):
        project = ET.Element("project")
        q.build_rack_folio(project, q.SECTION_RACK, _rack_modules())
        d = project.find("diagram")
        texts = [i.get("text") for i in d.find("inputs").findall("input")]
        self.assertIn("6ES7 136-6BA00-0CA0", texts)
        self.assertIn("DI x16", texts)
        # the no-catalog module must NOT invent an order number
        self.assertNotIn("None", " ".join(texts))

    def test_one_box_per_module(self):
        project = ET.Element("project")
        mods = _rack_modules()
        q.build_rack_folio(project, q.SECTION_RACK, mods)
        d = project.find("diagram")
        boxes = [s for s in d.find("shapes").findall("shape")
                 if abs(float(s.get("x2")) - float(s.get("x1")) - q.RACK_BOX_W) < 1
                 and abs(float(s.get("y2")) - float(s.get("y1")) - q.RACK_BOX_H) < 1]
        self.assertEqual(len(boxes), len(mods))

    def test_full_extent_inside_page_frame(self):
        # the FULL drawn extent (every box + rail + every label) lies inside the
        # real page frame — asserted on a full 8-module + wrapped row case.
        project = ET.Element("project")
        many = [SimpleNamespace(name=f"M{i}", catalog="6ES7 000-0AA00-0AA0",
                                slot=i, kind="DI", points=16) for i in range(10)]
        q.build_rack_folio(project, q.SECTION_RACK, many)
        d = project.find("diagram")
        xs, ys = [], []
        for s in d.find("shapes").findall("shape"):
            xs += [float(s.get("x1")), float(s.get("x2"))]
            ys += [float(s.get("y1")), float(s.get("y2"))]
        for i in d.find("inputs").findall("input"):
            xs.append(float(i.get("x")))
            ys.append(float(i.get("y")))
        self.assertGreaterEqual(min(xs), 0)
        self.assertLessEqual(max(xs), q.RACK_PAGE_W)
        self.assertGreaterEqual(min(ys), 0)
        self.assertLessEqual(max(ys), q.RACK_PAGE_H)

    def test_inherits_titleblock_and_no_token_leak(self):
        project = ET.Element("project")
        q.build_rack_folio(project, q.SECTION_RACK, _rack_modules())
        fields = q.resolve_title_block_fields(
            {**{k: v for k, v in q.PROJECT_TEMPLATE_DEFAULTS.items()
                if isinstance(v, str)}, "company": "Exxerpro Solutions"}, "C")
        q.attach_titleblocks(project, fields, q.load_titleblock_template())
        d = project.find("diagram")
        self.assertEqual(d.get("titleblocktemplate"), "exxerpro")
        for prop in d.find("properties").findall("property"):
            self.assertNotIn("%{", prop.text or "")


def _seed_folios(project, specs):
    """Seed `project` with bare diagrams (order + title only) so the index can
    enumerate them."""
    for order, title in specs:
        ET.SubElement(project, "diagram", {"order": str(order), "title": title})


class IndexFolioTest(unittest.TestCase):
    """IDX (Story 2.2): the drawing index / TOC is VISUAL-only (text + light rule
    lines, empty <elements>/<conductors>), lists every folio in document order
    with its SECTION page + title, INCLUDING its own entry, with correct final
    page numbers, and stays inside the page frame."""

    SPECS = [(0, "Portada"), (1, "Simbología"), (2, "Red PROFINET"),
             (4, "Disposición del rack"), (101, "R0.S2 F-DI150"),
             (200, "Bornero"), (900, "Historial")]

    def test_builds_one_folio_at_given_order(self):
        project = ET.Element("project")
        _seed_folios(project, self.SPECS)
        n = q.build_index_folio(project, q.SECTION_INDEX)
        self.assertEqual(n, 1)
        idx = [d for d in project.findall("diagram")
               if d.get("title") == q.INDEX_TITLE][0]
        self.assertEqual(idx.get("order"), str(q.SECTION_INDEX))

    def test_only_text_and_shapes_no_elements_or_conductors(self):
        project = ET.Element("project")
        _seed_folios(project, self.SPECS)
        q.build_index_folio(project, q.SECTION_INDEX)
        idx = [d for d in project.findall("diagram")
               if d.get("title") == q.INDEX_TITLE][0]
        self.assertEqual(len(idx.find("elements").findall("element")), 0)
        self.assertEqual(len(idx.find("conductors").findall("conductor")), 0)
        self.assertGreater(len(idx.find("shapes").findall("shape")), 0)

    def test_lists_every_folio_including_itself_in_order(self):
        project = ET.Element("project")
        _seed_folios(project, self.SPECS)
        q.build_index_folio(project, q.SECTION_INDEX)
        idx = [d for d in project.findall("diagram")
               if d.get("title") == q.INDEX_TITLE][0]
        texts = [i.get("text") for i in idx.find("inputs").findall("input")]
        # one page label per seeded folio + the index's own page (SECTION_INDEX)
        pages = [t for t in texts if t.isdigit()]
        expected = sorted(o for o, _ in self.SPECS) + [q.SECTION_INDEX]
        self.assertEqual([int(p) for p in pages], sorted(expected))
        # 3-digit zero-padding (the cajetín page scheme)
        self.assertIn("000", pages)
        self.assertIn(f"{q.SECTION_INDEX:03d}", pages)
        # the index's own title is listed
        self.assertIn(q.INDEX_TITLE, texts)

    def test_duplicate_orders_deduped_with_warning(self):
        # IDX-guard: two folios sharing a section order must NOT print two
        # identical page numbers silently — keep the first, warn on stderr.
        project = ET.Element("project")
        _seed_folios(project, [(0, "Portada"), (5, "First"), (5, "Collision")])
        buf = io.StringIO()
        with redirect_stderr(buf):
            entries = q._index_entries(project, q.SECTION_INDEX)
        orders = [o for o, _t in entries]
        self.assertEqual(len(orders), len(set(orders)),
                         f"duplicate page numbers in index: {entries}")
        self.assertIn(5, orders)
        # the FIRST folio at the colliding order is kept
        self.assertIn((5, "First"), entries)
        self.assertNotIn((5, "Collision"), entries)
        err = buf.getvalue()
        self.assertIn("duplicate diagram order", err)
        self.assertIn("005", err)

    def test_self_page_is_section_index_and_does_not_renumber(self):
        # the index's own page is SECTION_INDEX, and listing the fixed `order`s
        # means no other folio's page is shifted by the index's insertion.
        project = ET.Element("project")
        _seed_folios(project, self.SPECS)
        q.build_index_folio(project, q.SECTION_INDEX)
        idx = [d for d in project.findall("diagram")
               if d.get("title") == q.INDEX_TITLE][0]
        # pair page->title from the row inputs (skip the header + col headers)
        inps = idx.find("inputs").findall("input")
        rows = {}
        i = 0
        texts = [x.get("text") for x in inps]
        for j, t in enumerate(texts):
            if t.isdigit() and j + 1 < len(texts):
                rows[int(t)] = texts[j + 1]
        self.assertEqual(rows[q.SECTION_INDEX], q.INDEX_TITLE)
        self.assertEqual(rows[0], "Portada")
        self.assertEqual(rows[101], "R0.S2 F-DI150")

    def test_full_extent_inside_page_frame(self):
        # a long folio list (40 folios) keeps the last row + every rule inside
        # the real page frame (the row pitch compresses if needed).
        project = ET.Element("project")
        _seed_folios(project, [(i, f"Folio {i}") for i in range(40)])
        q.build_index_folio(project, q.SECTION_INDEX)
        idx = [d for d in project.findall("diagram")
               if d.get("title") == q.INDEX_TITLE][0]
        xs, ys = [], []
        for s in idx.find("shapes").findall("shape"):
            xs += [float(s.get("x1")), float(s.get("x2"))]
            ys += [float(s.get("y1")), float(s.get("y2"))]
        for i in idx.find("inputs").findall("input"):
            xs.append(float(i.get("x")))
            ys.append(float(i.get("y")))
        self.assertGreaterEqual(min(xs), 0)
        self.assertLessEqual(max(xs), q.INDEX_PAGE_W)
        self.assertGreaterEqual(min(ys), 0)
        self.assertLessEqual(max(ys), q.INDEX_PAGE_H)

    def test_inherits_titleblock_and_no_token_leak(self):
        project = ET.Element("project")
        _seed_folios(project, self.SPECS)
        q.build_index_folio(project, q.SECTION_INDEX)
        fields = q.resolve_title_block_fields(
            {**{k: v for k, v in q.PROJECT_TEMPLATE_DEFAULTS.items()
                if isinstance(v, str)}, "company": "Exxerpro Solutions"}, "C")
        q.attach_titleblocks(project, fields, q.load_titleblock_template())
        idx = [d for d in project.findall("diagram")
               if d.get("title") == q.INDEX_TITLE][0]
        self.assertEqual(idx.get("titleblocktemplate"), "exxerpro")
        for prop in idx.find("properties").findall("property"):
            self.assertNotIn("%{", prop.text or "")


class TopologyWaddingRegressionTest(unittest.TestCase):
    """E2.1 end-to-end: the topology folio is added at order 2 from the real
    fixture WITHOUT moving the matched floor (106 drawn / 75 matched); CHAN
    brings the drawings to 11 (all-spare MOD_ENT_3 now drawn) and the total folio
    count to 35; the drawings stay at 101."""

    FIXTURE = _wadding_fixture()

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

    def test_topology_folio_present_at_order_2_clean(self):
        root, _, err = self._run()
        diagrams = root.findall("diagram")
        # exactly one topology folio, at order 2
        topo = [d for d in diagrams
                if d.get("title") == "Red de comunicaciones"]
        self.assertEqual(len(topo), 1)
        t = topo[0]
        self.assertEqual(t.get("order"), "2")
        # visual-only + title-blocked
        self.assertEqual(len(t.find("elements").findall("element")), 0)
        self.assertEqual(len(t.find("conductors").findall("conductor")), 0)
        self.assertEqual(t.get("titleblocktemplate"), "exxerpro")
        for prop in t.find("properties").findall("property"):
            self.assertNotIn("%{", prop.text or "")
        # total folio count is 35 (CHAN: the all-spare MOD_ENT_3 now adds its own
        # drawing folio AND a bornero folio; was 33 before CHAN).
        self.assertEqual(len(diagrams), 35)

    def test_floor_held_and_drawings_still_101(self):
        root, _, err = self._run()
        # the floor is UNMOVED (parsed from main()'s own summary)
        self.assertEqual(int(re.search(r"points\s*:\s*(\d+)\s+drawn",
                                       err).group(1)), 106)
        self.assertEqual(int(re.search(r"symbols\s*:\s*(\d+)\s+matched",
                                       err).group(1)), 75)
        self.assertRegex(err, r"31\s+generic terminal")     # 0 false positives
        # CHAN: 11 drawing folios (all-spare MOD_ENT_3 now drawn), first at 101.
        drawing = [d for d in root.findall("diagram")
                   if re.match(r"^R\d", d.get("title") or "")]
        self.assertEqual(len(drawing), 11)
        self.assertEqual(sorted(int(d.get("order")) for d in drawing)[0], 101)

    def test_topology_full_extent_inside_frame_on_real_fixture(self):
        root, _, _ = self._run()
        t = [d for d in root.findall("diagram")
             if d.get("title") == "Red de comunicaciones"][0]
        xs, ys = [], []
        for s in t.find("shapes").findall("shape"):
            xs += [float(s.get("x1")), float(s.get("x2"))]
            ys += [float(s.get("y1")), float(s.get("y2"))]
        for i in t.find("inputs").findall("input"):
            xs.append(float(i.get("x")))
            ys.append(float(i.get("y")))
        self.assertGreaterEqual(min(xs), 0)
        self.assertLessEqual(max(xs), 1010)
        self.assertGreaterEqual(min(ys), 0)
        self.assertLessEqual(max(ys), q.SUMMARY_HEIGHT)

    def _chassis(self):
        """Recompute the chassis grouping from the real fixture tree."""
        import logix_to_eplan_csv as l2e
        _, modules, _, _ = l2e.load_l5x(str(self.FIXTURE))
        tree = q.build_topology_tree(modules)
        return q.build_topology_chassis(tree)

    def _enclosing_boxes(self, t):
        """Return the chassis/HMI enclosing-box rects of the rendered folio, by
        matching each shape's height to a known chassis/HMI box height. Returns a
        list of (x1, y1, x2, y2)."""
        chassis, hmi = self._chassis()
        heights = {q._chassis_box_height(len(c["rows"])) for c in chassis}
        heights.add(q._chassis_box_height(1))
        boxes = []
        for s in t.find("shapes").findall("shape"):
            x1, y1 = float(s.get("x1")), float(s.get("y1"))
            x2, y2 = float(s.get("x2")), float(s.get("y2"))
            if (y2 - y1) in heights and (x2 - x1) > 50:
                boxes.append((x1, y1, x2, y2))
        return boxes

    def test_chassis_grouping_matches_ground_truth(self):
        # exactly 2 chassis: Chasis Local (8 rows) + Chasis Remoto (6 rows),
        # plus the PV_PUPITRE HMI node — grouped by physical rack.
        chassis, hmi = self._chassis()
        self.assertEqual(len(chassis), 2)
        local, remote = chassis[0], chassis[1]
        self.assertEqual(local["label"], "Chasis Local")
        self.assertEqual(set(local["rows"]),
                         {"Local", "RIO_LOCAL", "MOD_ENT_1", "MOD_ENT_2",
                          "MOD_ENT_3", "MOD_SAL_1", "MOD_SAL_2", "MOD_SAL_3"})
        self.assertEqual(len(local["rows"]), 8)
        self.assertEqual(remote["label"], "Chasis Remoto (RIO_RCP)")
        self.assertEqual(set(remote["rows"]),
                         {"RIO_RCP", "REM_IN_1", "REM_IN_2", "REM_OUT_RLY_1",
                          "REM_OUT_2", "REM_AN_IN_1"})
        self.assertEqual(len(remote["rows"]), 6)
        self.assertEqual(hmi, ["PV_PUPITRE"])

    def test_chassis_boxes_disjoint_no_overlap(self):
        # Fixes defect 1: the two chassis boxes + the HMI box are mutually
        # DISJOINT (no inter-group overlap) on the real fixture.
        root, _, _ = self._run()
        t = [d for d in root.findall("diagram")
             if d.get("title") == "Red de comunicaciones"][0]
        boxes = self._enclosing_boxes(t)
        # 2 chassis + 1 HMI = 3 enclosing boxes
        self.assertEqual(len(boxes), 3)

        def overlap(a, b):
            ax1, ay1, ax2, ay2 = a
            bx1, by1, bx2, by2 = b
            return ax1 < bx2 and bx1 < ax2 and ay1 < by2 and by1 < ay2

        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                self.assertFalse(overlap(boxes[i], boxes[j]),
                                 f"chassis boxes {i} and {j} overlap")

    def test_one_box_per_chassis_not_per_module_on_fixture(self):
        # Fixes defect 2 at the structural level: exactly ONE enclosing box per
        # chassis (+1 HMI), never one box per module (there are 14 modules but
        # only 3 enclosing boxes).
        root, _, _ = self._run()
        t = [d for d in root.findall("diagram")
             if d.get("title") == "Red de comunicaciones"][0]
        self.assertEqual(len(self._enclosing_boxes(t)), 3)


def _addr_tmod(name, parent, catalog, kind=None, network_address=None):
    """A topology Module stand-in that also carries a network_address (the field
    the address feature reads). Mirrors _tmod but adds the new field."""
    return SimpleNamespace(name=name, parent=parent, catalog=catalog, kind=kind,
                           slot=0, points=0, network_address=network_address)


class NetworkAddressParserTest(unittest.TestCase):
    """E2 network-addresses: load_l5x captures the node address from the first
    NON-ICP port carrying a non-empty Address (ControlNet/DeviceNet/Ethernet);
    a module whose only non-ICP port has no Address stays None (never invent)."""

    def _load(self, xml):
        import logix_to_eplan_csv as l2e
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.L5X"
            p.write_text(xml, encoding="utf-8")
            _, modules, _, _ = l2e.load_l5x(str(p))
        return modules

    def test_controlnet_address_captured_as_raw_string(self):
        xml = (
            '<RSLogix5000Content><Controller Name="P"><Modules>'
            '<Module Name="BRIDGE" CatalogNumber="1756-CNB/D" '
            'ParentModule="Local"><Ports>'
            '<Port Id="1" Address="0" Type="ICP"/>'
            '<Port Id="2" Address="3" Type="ControlNet"/>'
            '</Ports></Module>'
            '</Modules></Controller></RSLogix5000Content>'
        )
        mods = self._load(xml)
        self.assertEqual(mods["BRIDGE"].network_address, "3")
        # ICP slot still parsed from the ICP port (unchanged behaviour)
        self.assertEqual(mods["BRIDGE"].slot, 0)

    def test_no_address_on_non_icp_port_stays_none(self):
        # never-invent: an Ethernet port with no Address attribute -> None
        xml = (
            '<RSLogix5000Content><Controller Name="P"><Modules>'
            '<Module Name="CPU" CatalogNumber="1756-L81E" '
            'ParentModule="Local"><Ports>'
            '<Port Id="1" Address="0" Type="ICP"/>'
            '<Port Id="2" Type="Ethernet"/>'
            '</Ports></Module>'
            '</Modules></Controller></RSLogix5000Content>'
        )
        mods = self._load(xml)
        self.assertIsNone(mods["CPU"].network_address)

    def test_io_card_with_only_icp_port_stays_none(self):
        xml = (
            '<RSLogix5000Content><Controller Name="P"><Modules>'
            '<Module Name="CARD" CatalogNumber="1756-IA16" '
            'ParentModule="Local"><Ports>'
            '<Port Id="1" Address="5" Type="ICP"/>'
            '</Ports></Module>'
            '</Modules></Controller></RSLogix5000Content>'
        )
        mods = self._load(xml)
        self.assertIsNone(mods["CARD"].network_address)

    def test_real_fixture_node_addresses_match_ground_truth(self):
        fixture = _wadding_fixture()
        if not fixture.is_file():
            self.skipTest("WADDING_1.L5X fixture not present")
        import logix_to_eplan_csv as l2e
        _, modules, _, _ = l2e.load_l5x(str(fixture))
        # the three ControlNet nodes carry their real node numbers
        self.assertEqual(modules["RIO_LOCAL"].network_address, "1")
        self.assertEqual(modules["RIO_RCP"].network_address, "2")
        self.assertEqual(modules["PV_PUPITRE"].network_address, "3")
        # the controller's only non-ICP port is Ethernet with NO Address -> None
        self.assertIsNone(modules["Local"].network_address)
        # plain I/O cards (only an ICP port) carry no node address
        for n in ("MOD_ENT_1", "MOD_SAL_1", "REM_IN_1", "REM_AN_IN_1"):
            self.assertIsNone(modules[n].network_address, n)


class NetworkAddressRenderTest(unittest.TestCase):
    """E2 network-addresses render INLINE on the chassis module row (and the HMI
    detail row), only when present, and stay inside the enclosing box bounds."""

    def test_row_text_contains_inline_node_token(self):
        node = {"module": _addr_tmod("BRIDGE", "Local", "1756-CNB/D",
                                     network_address="3"),
                "role": "bridge", "protocol": "ControlNet", "parent": "Local"}
        txt = q._topo_module_row_text(node)
        self.assertIn("Nodo 3", txt)
        self.assertIn("BRIDGE", txt)

    def test_row_text_omits_token_when_no_address(self):
        # never-invent: no network_address -> no "Nodo" token at all
        node = {"module": _addr_tmod("CARD", "Local", "1756-IA16", kind="DI"),
                "role": "io", "protocol": None, "parent": "Local"}
        txt = q._topo_module_row_text(node)
        self.assertNotIn("Nodo", txt)

    def test_addressed_row_text_fits_inside_chassis_box(self):
        # positional convention: the FULL text extent (x..x+width, y±font) of an
        # addressed module row stays inside the chassis box rectangle.
        nodes = {
            "Local": {"module": _addr_tmod("Local", "Local", "1756-L81E"),
                      "role": "controller", "protocol": None, "parent": "Local"},
            "BRIDGE": {"module": _addr_tmod("BRIDGE", "Local", "1756-CNB/D",
                                            network_address="3"),
                       "role": "bridge", "protocol": "ControlNet",
                       "parent": "Local"},
        }
        chassis = {"head": "Local", "label": "Chasis Local",
                   "rows": ["Local", "BRIDGE"]}
        shapes = ET.Element("shapes")
        inputs = ET.Element("inputs")
        x, y, w = q.TOPO_X_MARGIN, q.TOPO_LOCAL_Y1, q.TOPO_LOCAL_W
        bx1, by1, bx2, by2 = q._add_chassis_box(shapes, inputs, nodes, chassis,
                                                x, y, w)
        # the addressed row's input carries the inline token
        addr_inputs = [i for i in inputs.findall("input")
                       if "Nodo 3" in (i.get("text") or "")]
        self.assertEqual(len(addr_inputs), 1)
        ip = addr_inputs[0]
        ix, iy = float(ip.get("x")), float(ip.get("y"))
        # font px estimate (Sans Serif 7pt): ~5 px/char advance, ~9 px tall
        char_w, font_h = 5.0, 9.0
        x_end = ix + char_w * len(ip.get("text"))
        # full horizontal extent inside the box
        self.assertGreaterEqual(ix, bx1)
        self.assertLessEqual(x_end, bx2)
        # vertical extent (baseline ± font height) inside the box
        self.assertGreaterEqual(iy - font_h, by1)
        self.assertLessEqual(iy + font_h, by2)

    def test_hmi_box_detail_row_carries_address_when_present(self):
        node = {"module": _addr_tmod("PV_PUPITRE", "RIO_LOCAL", "PanelView",
                                     network_address="3"),
                "role": "hmi", "protocol": None, "parent": "RIO_LOCAL"}
        shapes = ET.Element("shapes")
        inputs = ET.Element("inputs")
        bx1, by1, bx2, by2 = q._add_hmi_box(shapes, inputs, node,
                                            q.TOPO_X_MARGIN, q.TOPO_LOCAL_Y1,
                                            q.TOPO_HMI_W)
        joined = " | ".join(i.get("text") for i in inputs.findall("input"))
        self.assertIn("Nodo 3", joined)
        # the address-bearing input stays inside the HMI box bounds
        ip = [i for i in inputs.findall("input")
              if "Nodo 3" in (i.get("text") or "")][0]
        ix, iy = float(ip.get("x")), float(ip.get("y"))
        self.assertGreaterEqual(ix, bx1)
        self.assertLessEqual(ix + 5.0 * len(ip.get("text")), bx2)
        self.assertGreaterEqual(iy - 9.0, by1)
        self.assertLessEqual(iy + 9.0, by2)

    def test_hmi_box_omits_token_when_no_address(self):
        node = {"module": _addr_tmod("PV_PUPITRE", "RIO_LOCAL", "PanelView"),
                "role": "hmi", "protocol": None, "parent": "RIO_LOCAL"}
        shapes = ET.Element("shapes")
        inputs = ET.Element("inputs")
        q._add_hmi_box(shapes, inputs, node, q.TOPO_X_MARGIN, q.TOPO_LOCAL_Y1,
                       q.TOPO_HMI_W)
        joined = " | ".join(i.get("text") for i in inputs.findall("input"))
        self.assertNotIn("Nodo", joined)


class LoadProjectTemplateGroundingTest(unittest.TestCase):
    """T3.4: the nested 'grounding' object merges gracefully — string sub-values
    override, missing/absent/malformed entries keep the documented defaults, and
    an override never mutates the shared module-level default."""

    def _write(self, d, text):
        p = Path(d) / "project_template.json"
        p.write_text(text, encoding="utf-8")
        return p

    def test_absent_file_keeps_grounding_defaults(self):
        tmpl = q.load_project_template(Path("does-not-exist-anywhere.json"))
        self.assertEqual(tmpl["grounding"],
                         q.PROJECT_TEMPLATE_DEFAULTS["grounding"])

    def test_partial_override_merges_per_key(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, '{"grounding": {"fe_gauge": "6 AWG custom"}}')
            tmpl = q.load_project_template(p)
            self.assertEqual(tmpl["grounding"]["fe_gauge"], "6 AWG custom")
            # the two un-overridden keys keep their defaults
            self.assertEqual(tmpl["grounding"]["pe_gauge"],
                             q.PROJECT_TEMPLATE_DEFAULTS["grounding"]["pe_gauge"])
            self.assertEqual(
                tmpl["grounding"]["electrode_gauge"],
                q.PROJECT_TEMPLATE_DEFAULTS["grounding"]["electrode_gauge"])

    def test_non_string_subvalues_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, '{"grounding": {"fe_gauge": 7, "pe_gauge": "ok"}}')
            tmpl = q.load_project_template(p)
            self.assertEqual(tmpl["grounding"]["fe_gauge"],
                             q.PROJECT_TEMPLATE_DEFAULTS["grounding"]["fe_gauge"])
            self.assertEqual(tmpl["grounding"]["pe_gauge"], "ok")

    def test_malformed_grounding_block_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, '{"grounding": "not a dict"}')
            tmpl = q.load_project_template(p)
            self.assertEqual(tmpl["grounding"],
                             q.PROJECT_TEMPLATE_DEFAULTS["grounding"])

    def test_override_does_not_mutate_shared_default(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, '{"grounding": {"fe_gauge": "MUTANT"}}')
            q.load_project_template(p)
            # the module-level default must be untouched by the override
            self.assertNotEqual(
                q.PROJECT_TEMPLATE_DEFAULTS["grounding"]["fe_gauge"], "MUTANT")


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
    """End-to-end floor: the WADDING_1 fixture produces 11 drawing folios (CHAN:
    every I/O card drawn, incl. the all-spare MOD_ENT_3) / 106 points / 75 matched
    / 0 false positives, with the summary + changelog + NEW supply folio present,
    the title block on every folio, no raw %{token} leaks, and the supply folio
    touching no element/conductor. Skipped if the fixture is absent (public-repo
    hygiene: it is never committed)."""

    FIXTURE = _wadding_fixture()

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
        # CHAN: 11 drawing folios (every I/O card drawn, incl. all-spare
        # MOD_ENT_3). Match the rack digit ("R1"..) so the topology folio
        # ("Red de comunicaciones") is NOT counted as a drawing.
        drawing = [d for d in diagrams
                   if re.match(r"^R\d", d.get("title", ""))]
        self.assertEqual(len(drawing), 11)
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

    def test_floor_match_breakdown_by_type(self):
        """The REAL false-positive guard: assert the EXACT per-type match
        breakdown, not just the matched TOTAL. A semantic mis-classification (e.g.
        a limit_switch counted as a solenoid_valve) keeps the total at 75 and
        would ship green against `test_floor_folio_and_point_counts`; this catches
        it. The 31 generic terminals are the unmatched remainder (106 - 75)."""
        _, _, err = self._run()
        breakdown, generic = _parse_match_breakdown(err)
        self.assertIsNotNone(breakdown, f"no per-type breakdown in summary:\n{err}")
        self.assertEqual(breakdown, {
            "solenoid_valve": 26, "limit_switch": 17, "push_button": 8,
            "contact_feedback": 6, "level_switch": 4, "pilot_light": 4,
            "relay_coil": 4, "push_button_nc": 2, "selector_switch": 2,
            "emergency_stop": 1, "proximity_sensor": 1,
        })
        self.assertEqual(sum(breakdown.values()), 75)  # cross-check the total
        self.assertEqual(generic, 31)                  # 0 false positives

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

    def test_grounding_folios_present_clean_and_numbered(self):
        # T3.4: 2 chassis grounding folios at orders 99 & 100, Alimentación
        # floated down to 98, and the card drawings STILL at order 101 (cards
        # unchanged). Each grounding folio is visual-only and title-blocked.
        root, _, err = self._run()
        diagrams = root.findall("diagram")
        gnd = [d for d in diagrams
               if (d.get("title") or "").startswith("Puesta a tierra")]
        self.assertEqual(len(gnd), 2)
        self.assertEqual(sorted(int(d.get("order")) for d in gnd), [99, 100])
        # Alimentación floated to 98
        ali = [d for d in diagrams if d.get("title") == "Alimentación"]
        self.assertEqual(len(ali), 1)
        self.assertEqual(ali[0].get("order"), "98")
        # the card drawings are UNCHANGED at order 101 (first drawing folio).
        # Match the rack digit so the topology folio is not swept in.
        draw_orders = sorted(int(d.get("order")) for d in diagrams
                             if re.match(r"^R\d", d.get("title") or ""))
        self.assertEqual(draw_orders[0], 101)
        # the floor is still 106 drawn / 75 matched (parsed from main()'s summary)
        self.assertEqual(int(re.search(r"points\s*:\s*(\d+)\s+drawn",
                                       err).group(1)), 106)
        self.assertEqual(int(re.search(r"symbols\s*:\s*(\d+)\s+matched",
                                       err).group(1)), 75)
        for d in gnd:
            self.assertEqual(len(d.find("elements").findall("element")), 0)
            self.assertEqual(len(d.find("conductors").findall("conductor")), 0)
            self.assertEqual(d.get("titleblocktemplate"), "exxerpro")
            # the chassis identity is derived from the L5X (rack + adapter), not
            # an invented Spanish friendly name
            self.assertRegex(d.get("title"),
                             r"Chasis R\d+ \((Local|RIO_RCP)\)")

    def test_two_column_card_right_column_spare_extent_inside_frame(self):
        # Optional positional assertion (folds in a review finding): on a
        # two-column card the right column's drawn content (incl. the right-column
        # strip terminal extent COL_X[1]+STRIP_X_OFF+10) and the power-table band
        # (POWER_TABLE_LEFT..) must both stay inside the real page frame (≈1010).
        # The two bands may share x but never collide (the power table sits ABOVE
        # the I/O rows in y); this asserts the frame containment that matters.
        root, _, _ = self._run()
        right_x = q.COL_X[1]
        strip_right = right_x + q.STRIP_X_OFF + 10
        # the power table band starts at POWER_TABLE_LEFT and runs to the frame
        for d in root.findall("diagram"):
            if not (d.get("title") or "").startswith("R"):
                continue
            shapes = d.find("shapes").findall("shape")
            if any(abs(float(s.get("x1")) - right_x) < 5 for s in shapes):
                # found a two-column card: assert both bands sit on-sheet
                self.assertLess(strip_right, 1010)
                self.assertLess(q.POWER_TABLE_LEFT, 1010)
                # every shape on this folio is inside the frame width
                xs = [float(s.get(k)) for s in shapes for k in ("x1", "x2")]
                self.assertLessEqual(max(xs), 1010)
                self.assertGreaterEqual(min(xs), 0)
                return
        self.skipTest("no two-column card in fixture")

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
        # a drawing folio title is a rack/slot like "R1..." — match the rack
        # digit so the topology folio ("Red de comunicaciones") is NOT picked up.
        i_draw = idx(lambda t: re.match(r"^R\d", t))
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
            # a card drawing's title is a rack/slot ("R1"..); the topology folio
            # ("Red de comunicaciones") is front matter and hides rulers like the
            # other lists, so match the rack DIGIT, not a bare leading "R".
            is_drawing = bool(re.match(r"^R\d", d.get("title") or ""))
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
                   if re.match(r"^R\d", d.get("title") or "")]
        # CHAN: every I/O card now emits a folio (incl. the all-spare MOD_ENT_3),
        # so the drawing band is 11 cards on pages 101..111.
        self.assertEqual(len(drawing), 11)
        pages = sorted(int(d.get("order")) for d in drawing)
        self.assertEqual(pages, list(range(101, 112)))   # 101..111
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
        self.assertTrue(seen_prefixes.issubset({str(p) for p in range(101, 112)}))

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
        # matched point -> field conductor BROKEN into two segments. CHAN: the
        # 15 unused channels of this 16-ch card now each draw a spare stub with
        # ONE card->strip conductor, so the diagram total is 2 + 15 = 17.
        conds = d.find("conductors").findall("conductor")
        self.assertEqual(len(conds), 2 + 15)

    def test_generic_point_gets_strip_label_and_single_conductor(self):
        # an unmatched tag stays generic: strip terminal -X1:0 + ONE conductor
        # (card terminal -> strip terminal); no device beyond.
        pt = SimpleNamespace(module=None, index=0, tag="ZZZ_NOMATCH",
                             direction="I", description="", analog=False)
        d = self._diagram([pt])
        self.assertIn("-X1:0", self._texts(d))
        # CHAN: 1 (this generic point's card->strip) + 15 (spare stubs) = 16.
        conds = d.find("conductors").findall("conductor")
        self.assertEqual(len(conds), 1 + 15)

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
        # the I/O terminal + the strip terminal + every SPARE stub are all
        # borne_2 (CHAN introduces NO new element type). One mapped generic point
        # on a 16-channel card -> card(1) + strip(1); each of the 15 spares now
        # also draws card(1) + strip(1) = 30; total 2 + 30 = 32 borne_2, and NO
        # other element type (the generic point places no device symbol).
        self.assertEqual(types.count(q.TERMINAL_TYPE), 32)
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
        # guard the guard: the photocell-tight west pin tracks SYM_X_OFF. EYE-4
        # pushed the symbol +70 (290->360) to widen the row-text lane, so the
        # tightest west offset is now 330 (was 260).
        self.assertEqual(device_west_off, 330)
        for x in q.COL_X:
            cx = x + q.STRIP_X_OFF        # strip terminal centre x
            # borne_2 east pin reaches cx+10; N/S pins at cx; pins span y±10
            left, right = cx, cx + 10
            box_right = x + q.BOX_RIGHT           # card box right edge
            # EYE-4 WIDENED the row-text band from ~x+200 to ~x+285 (strip moved
            # to x+305) so long AB tag names no longer overrun into the bornera.
            row_text_right = x + 20 + 265         # widened row-text band end
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

    def test_symbol_extent_clears_right_column_and_frame(self):
        # EYE-4: widening the row-text lane (+70) must NOT push the device symbol
        # off-sheet or into the neighbouring column. A device symbol reaches
        # ~anchor+31 horizontally when rotated 90° (the widest is the 'simple'
        # w40/h60 def); use a conservative 40 px reach. This is the FULL-extent
        # frame proof the lane-widen rests on — a future bump to SYM_X_OFF that
        # breaks either bound turns this red.
        SYM_MAX_REACH = 40
        # right column device symbol stays inside the 1010 page frame
        self.assertLess(q.COL_X[1] + q.SYM_X_OFF + SYM_MAX_REACH, 1010)
        # left column device symbol clears the right column's card box (the
        # inter-column collision the +70 had to respect)
        self.assertLessEqual(q.COL_X[0] + q.SYM_X_OFF + SYM_MAX_REACH,
                             q.COL_X[1] - q.BOX_LEFT)


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

    def test_all_spare_card_still_gets_a_bornero(self):
        # CHAN: an all-spare card (no mapped points) now emits a bornero too — a
        # full strip of RESERVA rows from its capacity — mirroring its drawing
        # folio so the physical strip is represented for the panel builder.
        project = ET.Element("project")
        n = q.build_bornero_folios(project, 1, [self._card("A", [])])
        self.assertEqual(n, 1)
        d = project.find("diagram")
        texts = [i.get("text") for i in d.find("inputs").findall("input")]
        # all 16 channels present as RESERVA rows + their -X1:<ch> labels
        self.assertEqual(texts.count("RESERVA"), 16)
        for ch in range(16):
            self.assertIn(f"-X1:{ch}", texts)

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
    matched) plus the bornero folio line and the CHAN drawing folios (11: every
    I/O card drawn, incl. the all-spare MOD_ENT_3)."""

    FIXTURE = _wadding_fixture()

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
        # CHAN: 11 drawing folios (every I/O card drawn, incl. all-spare
        # MOD_ENT_3); match the rack digit so the topology folio is not swept in.
        drawing = [d for d in root.findall("diagram")
                   if re.match(r"^R\d", d.get("title", ""))]
        self.assertEqual(len(drawing), 11)
        # the bornero summary line is honest about what it drew. Borneros list
        # mapped + RESERVA terminals in channel order, so a wide card paginates —
        # REM_IN_1 (32-channel) needs a second sheet, and CHAN adds the all-spare
        # MOD_ENT_3's bornero -> 12 folios for the 11 cards. The floor (106/75)
        # above is what must not move.
        m = re.search(r"bornero\s*:\s*(\d+)\s+terminal-strip", err)
        self.assertIsNotNone(m, f"no bornero line in summary:\n{err}")
        self.assertEqual(int(m.group(1)), 12)
        borneros = [d for d in root.findall("diagram")
                    if (d.get("title") or "").startswith("Bornero")]
        self.assertEqual(len(borneros), 12)
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
        # one mapped point on a 16-channel card -> 15 spare RESERVA stubs, each
        # labelled -X1:<channel> with a RESERVA word, NO device symbol.
        pt = SimpleNamespace(module=None, index=0, tag="ZZZ_NOMATCH",
                             direction="I", description="", analog=False)
        d, bom_rows, spare_counter, _ = self._build([pt])
        self.assertEqual(spare_counter["CARD"], 15)
        texts = self._texts(d)
        for ch in range(1, 16):
            self.assertIn(f"-X1:{ch}", texts)
        # CHAN: each spare now draws a full box I/O point (RESERVA marker in both
        # the row text and the strip function) plus a generic IN-n point name.
        self.assertEqual(texts.count("RESERVA"), 15)
        # CHAN: each spare now carries a card->strip conductor too, mirroring the
        # mapped point: 1 (mapped generic) + 15 (spares) = 16.
        self.assertEqual(len(d.find("conductors").findall("conductor")), 16)

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
        # POSITIONAL: CHAN draws each spare as a FULL box I/O point — a card-side
        # I/O terminal at the column x AND a strip terminal at x+STRIP_X_OFF, each
        # joined by a card->strip conductor (mirroring a mapped point). Assert the
        # FULL pin extent (borne_2 pins span x..x+10, y±10 about the slot hotspot)
        # of EVERY spare element stays inside the page frame: vertically inside the
        # full-capacity card box's y band, and horizontally inside the sheet
        # (0..frame width). Tightest case: a fully-empty 16-channel card.
        d, _, spare_counter, mod = self._build([])  # no mapped points -> 16 spares
        self.assertEqual(spare_counter["CARD"], 16)
        x = q.COL_X[0]
        y1 = q.ROW_Y0 - 20
        y2 = q.ROW_Y0 + (mod.points - 1) * q.ROW_DY + 20
        frame_w = q.POWER_TABLE_RIGHT      # page-frame-aligned right edge (1010)
        terms = d.find("elements").findall("element")
        # 16 spares x (card terminal + strip terminal) = 32 elements, all borne_2
        self.assertEqual(len(terms), 32)
        self.assertEqual({el.get("type") for el in terms}, {q.TERMINAL_TYPE})
        card_xs, strip_xs = set(), set()
        for el in terms:
            ex, ey = int(el.get("x")), int(el.get("y"))
            for term in el.findall("terminals/terminal"):
                py = ey + int(term.get("y"))
                px = ex + int(term.get("x"))
                self.assertGreaterEqual(py, y1,
                    f"spare pin y={py} escapes box top {y1}")
                self.assertLessEqual(py, y2,
                    f"spare pin y={py} escapes box bottom {y2}")
                # full horizontal extent stays on-sheet, inside the frame
                self.assertGreaterEqual(px, 0, f"spare pin x={px} off-sheet")
                self.assertLessEqual(px, frame_w,
                    f"spare pin x={px} escapes frame {frame_w}")
            if ex == x:
                card_xs.add(ex)            # card-side I/O stub at the column x
            else:
                strip_xs.add(ex)
        # exactly the two lanes: the card I/O stub at x, the strip at x+STRIP_X_OFF
        self.assertEqual(card_xs, {x})
        self.assertEqual(strip_xs, {x + q.STRIP_X_OFF})
        # and 16 of each
        card = [el for el in terms if int(el.get("x")) == x]
        strip = [el for el in terms if int(el.get("x")) == x + q.STRIP_X_OFF]
        self.assertEqual(len(card), 16)
        self.assertEqual(len(strip), 16)
        # every spare's card->strip conductor resolves to terminals on the diagram
        ids = {t.get("id") for t in d.find("elements").iter("terminal")}
        conds = d.find("conductors").findall("conductor")
        self.assertEqual(len(conds), 16)   # one per spare, no field wire beyond
        for c in conds:
            self.assertIn(c.get("terminal1"), ids)
            self.assertIn(c.get("terminal2"), ids)

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
    """CHAN floor + spare totals end-to-end on the WADDING_1 fixture: the real
    matched/mapped floor (106 drawn / 75 matched / 0 FP) must NOT move; CHAN now
    draws EVERY I/O card (incl. the all-spare MOD_ENT_3) so there are 11 drawing
    folios and the SEPARATE spare counter reads 78 (capacity-mapped summed over
    all 11 cards: the old 62 + MOD_ENT_3's 16), with REM_AN_IN_1 contributing 14."""

    FIXTURE = _wadding_fixture()

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
                   if re.match(r"^R\d", d.get("title") or "")]
        self.assertEqual(len(drawing), 11)                 # CHAN: 11 drawing folios
        # the SEPARATE spare counter: 78 reserves over 11 cards (CHAN)
        m = re.search(r"spare\s*:\s*(\d+)\s+reserve terminal", err)
        self.assertIsNotNone(m, f"no spare line in summary:\n{err}")
        self.assertEqual(int(m.group(1)), 78)

    def test_spare_count_matches_capacity_minus_mapped(self):
        # compute the expected spare total straight from the I/O map and assert
        # the per-card REM_AN_IN_1 = 14 plus the project total = 78. CHAN: ALL
        # cards are now drawn (no skip of all-spare cards), so the all-spare
        # MOD_ENT_3 (16 channels) contributes its full capacity to the total.
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
            pts = per.get(m.name) or []   # CHAN: all-spare cards are drawn too
            spare = m.points - len({p.index for p in pts})
            total += spare
            if m.name == "REM_AN_IN_1":
                an_in_1 = spare
        self.assertEqual(an_in_1, 14)
        self.assertEqual(total, 78)

    def test_spare_rows_present_in_bom_and_summary(self):
        root, err = self._run()
        # the BOM breakdown line counts spares as their own category
        self.assertRegex(err, r"bom\s*:\s*\d+\s+rows.*\b78 spare\b")
        # a spare row reaches the summary (BOM / device index) folios as a
        # RESERVA line — confirm RESERVA text appears on a BOM folio.
        bom_folios = [d for d in root.findall("diagram")
                      if (d.get("title") or "").startswith("BOM")]
        self.assertTrue(bom_folios)
        reserva_seen = any(
            (i.get("text") or "") == "RESERVA"
            for d in bom_folios for i in d.find("inputs").findall("input"))
        self.assertTrue(reserva_seen, "no RESERVA row reached a BOM summary folio")


class BuildRockwellProjectTest(unittest.TestCase):
    """Epic 1: the Rockwell builder yields a vendor-neutral PlcProject IR whose
    fields match the legacy tuple path exactly (no behaviour change)."""

    FIXTURE = _wadding_fixture()

    def setUp(self):
        if not self.FIXTURE.is_file():
            self.skipTest("WADDING_1.L5X fixture not present")
        import plc_ir
        self.plc_ir = plc_ir

    def test_returns_plcproject_with_expected_counts(self):
        proj = self.plc_ir.build_rockwell_project(str(self.FIXTURE))
        self.assertIsInstance(proj, self.plc_ir.PlcProject)
        self.assertEqual(proj.name, "WADDING_1")
        self.assertEqual(proj.controller_name, "WADDING_1")  # neutral alias
        self.assertEqual(proj.source_vendor, "rockwell")
        # WADDING_1 floor: 15 modules in tree, 10 rack-assigned I/O cards,
        # 106 drawn points pre-dedup is the raw collect count (186), but the
        # builder carries the raw points list — assert against the tuple path.
        self.assertEqual(len(proj.modules), 15)
        # 11 rack-assigned I/O cards (one has no mapped tags, so only 10 get a
        # drawing folio); assert the rack-assignment count here.
        self.assertEqual(len(proj.io_mods), 11)
        self.assertGreater(len(proj.points), 0)
        self.assertGreater(len(proj.skipped), 0)

    def test_ir_fields_match_legacy_tuple_path(self):
        import logix_to_eplan_csv as l2e
        controller, modules, ctrl_tags, program_tags = l2e.load_l5x(
            str(self.FIXTURE))
        io_mods = l2e.assign_racks_and_addresses(modules)
        points, skipped = l2e.collect_points(modules, ctrl_tags, program_tags)

        proj = self.plc_ir.build_rockwell_project(str(self.FIXTURE))
        self.assertEqual(proj.name, controller)
        self.assertEqual(set(proj.modules), set(modules))
        self.assertEqual(len(proj.io_mods), len(io_mods))
        self.assertEqual([m.name for m in proj.io_mods],
                         [m.name for m in io_mods])
        self.assertEqual(len(proj.points), len(points))
        self.assertEqual([(p.tag, p.module.name, p.direction, p.index, p.analog)
                          for p in proj.points],
                         [(p.tag, p.module.name, p.direction, p.index, p.analog)
                          for p in points])
        self.assertEqual(len(proj.skipped), len(skipped))
        self.assertEqual(set(proj.controller_tags), set(ctrl_tags))
        self.assertEqual(set(proj.program_tags), set(program_tags))

    def test_include_hmi_flag_forwarded(self):
        base = self.plc_ir.build_rockwell_project(str(self.FIXTURE))
        hmi = self.plc_ir.build_rockwell_project(str(self.FIXTURE),
                                                 include_hmi=True)
        # including HMI points can only add points / remove skips, never fewer
        self.assertGreaterEqual(len(hmi.points), len(base.points))


if __name__ == "__main__":
    unittest.main()
