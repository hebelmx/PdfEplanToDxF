#!/usr/bin/env python3
"""Integration tests for the Siemens command (tia_to_qet) + the shared
render_project seam it calls.

Stdlib-only (unittest). Run from src/:
    python -m unittest test_tia_to_qet

Two groups:
  * RenderProjectVendorGateTest — render_project's emit_vendor_folios knob is a
    pure refactor seam; assert it gates the Rockwell-only folios both via the
    explicit knob and via the IR's source_vendor (belt-and-suspenders), and that
    the Rockwell IR still emits them (no regression).
  * Siemens*Test — drive tia_to_qet.main() end-to-end on the IMV1 fixture and
    assert the structural invariants (WADDING_1 gate applied to Siemens), the
    Rockwell-specific folio OMISSION, and the stderr floor (48 mapped / 40
    RESERVA / 88 channels). Gated on the fixture existing.
"""

import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tia_to_qet
import plc_ir
import logix_to_qet as q


def _parse_match_breakdown(err):
    """Parse the generator's stderr 'symbols' line into a per-type breakdown
    ``({type: count}, generic)`` — the REAL false-positive guard (a semantic
    mis-classification keeps the matched total but changes the per-type dict).
    Returns ``(None, None)`` if the line is absent (an empty IR uses no symbols).
    Mirrors the same helper in test_logix_to_qet (kept local so the test modules
    stay independent, like the per-file fixture resolvers)."""
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


def _imv1_io_channels() -> Path:
    root = Path(__file__).resolve().parent.parent / "Fixtures" / "Siemens" / "TiaPortal"
    return root / "IMV1_QRO001_08AGO21_V15_IO_Channels.xml"


def _imv1_aml() -> Path:
    root = Path(__file__).resolve().parent.parent / "Fixtures" / "Siemens" / "TiaPortal"
    return root / "IMV1_QRO001_08AGO21_V15.aml"


def _wadding_fixture() -> Path:
    root = Path(__file__).resolve().parent.parent / "Fixtures"
    for c in (root / "Rockwell" / "WADDING_1.L5X", root / "WADDING_1.L5X"):
        if c.is_file():
            return c
    return root / "Rockwell" / "WADDING_1.L5X"


# --------------------------------------------------------------------------
# render_project vendor gate — the shared seam
# --------------------------------------------------------------------------
class RenderProjectVendorGateTest(unittest.TestCase):
    """render_project gates topología / supply / grounding off for non-Rockwell
    IRs (and via the explicit emit_vendor_folios knob), while the Rockwell IR
    keeps emitting them. Uses a tiny synthetic IR so it needs no fixture."""

    @staticmethod
    def _render(project_ir, **kw):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "x.qet"
            with redirect_stderr(buf):
                rc = q.render_project(project_ir, str(out), **kw)
            xml = out.read_text(encoding="utf-8")
        return rc, ET.fromstring(xml), buf.getvalue()

    @staticmethod
    def _titles(root):
        return [d.get("title") or "" for d in root.findall("diagram")]

    def test_siemens_vendor_omits_rockwell_folios(self):
        ir = plc_ir.PlcProject(name="EMPTY", source_vendor="siemens")
        rc, root, err = self._render(ir, emit_vendor_folios=False)
        self.assertEqual(rc, 0)
        titles = self._titles(root)
        # cover present (legend is data-driven: an EMPTY IR uses no symbols, so
        # no Simbología folio — that's exercised on the real fixture below); the
        # three Rockwell-only folios ABSENT
        self.assertIn("Portada", titles)
        self.assertNotIn("Red de comunicaciones", titles)       # topología
        self.assertFalse(any(t.startswith("Alimentación") for t in titles))
        self.assertFalse(any(t.startswith("Puesta a tierra") for t in titles))
        # the Rockwell-only summary lines are omitted too
        self.assertNotRegex(err, r"\bsupply\s*:")
        self.assertNotRegex(err, r"\bgrounding\s*:")
        self.assertNotRegex(err, r"\btopología\s*:")

    def test_non_rockwell_forces_gate_off_even_if_knob_true(self):
        # belt-and-suspenders: a siemens IR can NEVER emit the Rockwell folios,
        # even if a caller mistakenly passes emit_vendor_folios=True.
        ir = plc_ir.PlcProject(name="EMPTY", source_vendor="siemens")
        _, root, _ = self._render(ir, emit_vendor_folios=True)
        titles = self._titles(root)
        self.assertNotIn("Red de comunicaciones", titles)
        self.assertFalse(any(t.startswith("Alimentación") for t in titles))


class RockwellStillEmitsVendorFoliosTest(unittest.TestCase):
    """The refactor must NOT regress the Rockwell path: render_project(rockwell)
    still emits topología + Alimentación + grounding."""

    FIXTURE = _wadding_fixture()

    def setUp(self):
        if not self.FIXTURE.is_file():
            self.skipTest("WADDING_1.L5X fixture not present")

    def test_rockwell_keeps_all_vendor_folios(self):
        ir = plc_ir.build_rockwell_project(str(self.FIXTURE))
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "w.qet"
            with redirect_stderr(buf):
                rc = q.render_project(ir, str(out))
            root = ET.fromstring(out.read_text(encoding="utf-8"))
        self.assertEqual(rc, 0)
        titles = [d.get("title") or "" for d in root.findall("diagram")]
        self.assertIn("Red de comunicaciones", titles)
        self.assertTrue(any(t.startswith("Alimentación") for t in titles))
        self.assertTrue(any(t.startswith("Puesta a tierra") for t in titles))


# --------------------------------------------------------------------------
# Siemens command end-to-end (gated on the IMV1 fixture)
# --------------------------------------------------------------------------
class SiemensRenderTestBase(unittest.TestCase):
    FIXTURE = _imv1_io_channels()

    def setUp(self):
        if not self.FIXTURE.is_file():
            self.skipTest("IMV1 IO_Channels.xml fixture not present")

    def _run(self):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "tia.qet"
            with redirect_stderr(buf):
                rc = tia_to_qet.main([str(self.FIXTURE), "-o", str(out)])
            self.assertEqual(rc, 0)
            xml = out.read_text(encoding="utf-8")
        return ET.fromstring(xml), buf.getvalue()


class SiemensStructuralTest(SiemensRenderTestBase):
    """The WADDING_1 structural invariants, applied to the Siemens .qet."""

    def test_terminal_ids_unique_and_conductors_resolve(self):
        root, _ = self._run()
        for d in root.findall("diagram"):
            elements = d.find("elements")
            conductors = d.find("conductors")
            if elements is None:
                continue
            term_ids = []
            for el in elements.findall("element"):
                # terminals are nested under <terminals> inside each <element>
                for t in el.iter("terminal"):
                    term_ids.append(t.get("id"))
            self.assertEqual(len(term_ids), len(set(term_ids)),
                             f"duplicate terminal id in {d.get('title')!r}")
            idset = set(term_ids)
            if conductors is not None:
                for c in conductors.findall("conductor"):
                    for end in ("terminal1", "terminal2"):
                        tid = c.get(end)
                        self.assertIn(tid, idset,
                                      f"conductor {end}={tid} unresolved in "
                                      f"{d.get('title')!r}")

    def test_every_element_type_has_embedded_definition(self):
        root, _ = self._run()
        types = set()
        for d in root.findall("diagram"):
            elements = d.find("elements")
            if elements is None:
                continue
            for el in elements.findall("element"):
                types.add(el.get("type"))
        self.assertTrue(types, "no element types drawn on the Siemens set")
        # the collection embeds one <element name=...> carrying a <definition> for
        # each referenced .elmt; assert every DRAWN type resolves to such a def.
        coll = root.find("collection")
        self.assertIsNotNone(coll, "no <collection> in the Siemens project")
        embedded = {el.get("name") for el in coll.iter("element")
                    if el.find("definition") is not None}
        for tp in types:
            leaf = tp.rsplit("/", 1)[-1]   # ...path/borne_2.elmt -> borne_2.elmt
            self.assertIn(leaf, embedded,
                          f"drawn type {tp!r} has no embedded <definition>")

    def test_iso_titleblock_on_every_folio_no_raw_tokens(self):
        root, _ = self._run()
        diagrams = root.findall("diagram")
        self.assertTrue(diagrams)
        for d in diagrams:
            self.assertEqual(d.get("titleblocktemplate"), "exxerpro",
                             f"folio {d.get('title')!r} missing ISO titleblock")
            props = d.find("properties")
            self.assertIsNotNone(props, f"folio {d.get('title')!r} has no properties")
            for prop in props.findall("property"):
                # %{...} is allowed only inside the embedded titleblock template,
                # never raw in a folio PROPERTY value.
                self.assertNotIn("%{", prop.text or "",
                                 f"raw token in property of {d.get('title')!r}")

    def test_rockwell_folios_absent_core_folios_present(self):
        root, _ = self._run()
        titles = [d.get("title") or "" for d in root.findall("diagram")]
        # OMITTED (Siemens): topología / supply / grounding
        self.assertNotIn("Red de comunicaciones", titles)
        self.assertFalse(any(t.startswith("Alimentación") for t in titles))
        self.assertFalse(any(t.startswith("Puesta a tierra") for t in titles))
        # PRESENT: portada, símbología, per-card I/O, bornero, BOM, changelog
        self.assertIn("Portada", titles)
        self.assertIn("Simbología", titles)
        self.assertTrue(any(t.startswith("Bornero") for t in titles),
                        "no bornero folio on the Siemens set")
        self.assertTrue(any(t.startswith("BOM") for t in titles),
                        "no BOM folio on the Siemens set")
        self.assertTrue(any(t.startswith("Historial") for t in titles),
                        "no changelog folio on the Siemens set")
        # EYE-3: the F-DQ1500 split halves now share ONE drawing folio. This
        # filter catches the F-D* card titles (drawing AND bornero): 3 drawing
        # (F-DI150, F-DI156, the merged F-DQ1500 [DO+DI]) + 4 matching borneros
        # (the per-half borneros are NOT merged) = 7 (was 8 with two F-DQ1500
        # drawing folios).
        io_folios = [t for t in titles
                     if "F-DI" in t or "F-DQ" in t or t.startswith("DI")
                     or t.startswith("DQ")]
        self.assertEqual(len(io_folios), 7, f"I/O folios: {io_folios}")
        # the merged drawing folio carries BOTH halves on one page ([DO+DI]); the
        # all-spare F-DQ1500 [DI] half is the RIGHT card of that single folio.
        self.assertTrue(any(t.startswith("R0") and "F-DQ1500" in t
                            and "[DO+DI]" in t for t in titles),
                        "F-DQ1500 split halves did not merge onto one folio")


class SiemensStderrFloorTest(SiemensRenderTestBase):
    """The Siemens stderr floor: 48 mapped points, 40 RESERVA channels, 88
    channels total — derived from the summary. CHAN: every I/O card now renders
    (including the all-spare F-DQ1500 [DI] half), so 'spare' (drawn reserves)
    reads the full 40 — all 88 channels are represented and the drawn-spare count
    matches the IR-level RESERVA (the '40 skipped' on the points line)."""

    def test_mapped_reserva_and_channel_floor(self):
        _, err = self._run()
        # 48 mapped points drawn
        m_pts = re.search(r"points\s*:\s*(\d+)\s+drawn,\s*(\d+)\s+skipped", err)
        self.assertIsNotNone(m_pts, f"no points line:\n{err}")
        mapped = int(m_pts.group(1))
        reserva = int(m_pts.group(2))     # IR-level RESERVA (spares)
        self.assertEqual(mapped, 48)
        self.assertEqual(reserva, 40)             # 40 RESERVA channels
        self.assertEqual(mapped + reserva, 88)    # 88 channels total

    def test_six_drawing_folios_and_drawn_spares(self):
        _, err = self._run()
        # EYE-3: 6 drawing folios — the two F-DQ1500 split halves now SHARE one
        # folio (was 7 when each half drew its own). Every channel is still drawn
        # (the merged folio carries both halves side-by-side).
        self.assertRegex(err, r"folios\s*:\s*6\b")
        # the SEPARATE drawn-spare counter now reads the full 40: every unused
        # channel is drawn, so the drawn reserves match the IR-level RESERVA.
        m = re.search(r"spare\s*:\s*(\d+)\s+reserve terminal", err)
        self.assertIsNotNone(m, f"no spare line:\n{err}")
        self.assertEqual(int(m.group(1)), 40)

    def test_floor_match_breakdown_by_type(self):
        """The REAL false-positive guard for the Siemens pipeline: assert the
        EXACT per-type match breakdown, not just the matched total.

        E6: SiemensRenderTestBase._run drives main() from the fixture dir with NO
        --tags, so coverage-based selection now picks PLCTagsS71500.xlsx (47/48)
        and REAL English descriptions populate the points. Symbol matching keys
        off those descriptions, so the IMV1 vocabulary now confidently matches a
        rich set (door/position -> limit_switch, lamps -> pilot_light, E-stops,
        NO/NC push-buttons, a horn). Was 2/46 when the wrong S71200 table left
        every description blank.

        Non-device signals are deliberately EXCLUDED (Abel, 2026-06-17): the 10
        supply-monitor channels ('VS_'/'Vsupply ...') and the 1 permit
        ('Permission to Open ...') carry no device symbol (no_symbol -> generic),
        so the confident set is 19 matched / 29 generic. This is the EXACT
        breakdown; a future change that mis-classifies a tag (e.g. re-admits a
        supply monitor as a device) turns it red."""
        _, err = self._run()
        breakdown, generic = _parse_match_breakdown(err)
        self.assertIsNotNone(breakdown, f"no per-type breakdown in summary:\n{err}")
        self.assertEqual(breakdown, {
            "limit_switch": 8,
            "pilot_light": 5,
            "emergency_stop": 2,
            "push_button_nc": 2,
            "push_button": 1,
            "horn": 1,
        })
        self.assertEqual(sum(breakdown.values()), 19)
        self.assertEqual(generic, 29)   # 48 drawn - 19 matched, 0 false positives

    def test_rockwell_summary_lines_omitted(self):
        _, err = self._run()
        self.assertNotRegex(err, r"\bsupply\s*:")
        self.assertNotRegex(err, r"\bgrounding\s*:")
        self.assertNotRegex(err, r"\btopología\s*:")


class SiemensIRFloorTest(SiemensRenderTestBase):
    """Cross-check the floor straight off the IR (independent of the stderr
    formatting): 88 channels = 48 tagged points + 40 spares."""

    def test_ir_channel_breakdown(self):
        ir = plc_ir.build_tia_project(str(self.FIXTURE))
        self.assertEqual(ir.source_vendor, "siemens")
        self.assertEqual(len(ir.points), 48)
        spares = [s for s in ir.skipped if len(s) > 2 and s[2] == "spare"]
        self.assertEqual(len(spares), 40)
        channels = sum(m.points for m in ir.io_mods)
        self.assertEqual(channels, 88)


class SiemensRackIndexTest(unittest.TestCase):
    """RACK+IDX end-to-end (gated on BOTH the IMV1 IO_Channels.xml and .aml):
    the Siemens render gains a rack-layout folio (modules in slot order with real
    slots + order#s) and a drawing-index folio (all folios with correct pages),
    and the I/O folio titles now show the real slot (not 'Slot None')."""

    IO = _imv1_io_channels()
    AML = _imv1_aml()

    def setUp(self):
        if not (self.IO.is_file() and self.AML.is_file()):
            self.skipTest("IMV1 IO_Channels.xml or .aml fixture not present")

    def _run(self):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "tia.qet"
            with redirect_stderr(buf):
                rc = tia_to_qet.main([str(self.IO), "--aml", str(self.AML),
                                      "-o", str(out)])
            self.assertEqual(rc, 0)
            xml = out.read_text(encoding="utf-8")
        return ET.fromstring(xml), buf.getvalue()

    def test_rack_and_index_folios_present(self):
        root, _ = self._run()
        titles = [d.get("title") or "" for d in root.findall("diagram")]
        # T2: assert the .aml-derived folios BY TITLE (not just the count 23)
        self.assertIn(q.PROFINET_TITLE, titles)
        self.assertIn(q.RACK_TITLE, titles)
        self.assertIn(q.INDEX_TITLE, titles)
        # was 21 (NET) -> 23 with RACK + IDX added -> 22 once the F-DQ1500 split
        # halves (EYE-3) merge onto ONE drawing folio (7 drawing folios -> 6).
        self.assertEqual(len(root.findall("diagram")), 22)

    def test_cover_controller_tag_is_tia_not_l5x(self):
        # TIA-FIX-2: the Siemens cover must NOT carry the Rockwell '(L5X)' format
        # tag; it reads '(TIA)' for the real source export. No invented value.
        root, _ = self._run()
        portada = [d for d in root.findall("diagram")
                   if d.get("title") == q.PORTADA_TITLE][0]
        labels = " | ".join(i.get("text")
                            for i in portada.find("inputs").findall("input"))
        self.assertIn("CONTROLADOR (TIA)", labels)
        self.assertNotIn("(L5X)", labels)

    def test_network_folio_present_with_35_nodes_by_title(self):
        # T2: a POSITIVE NET assertion BY TITLE, with the real 35 PROFINET nodes
        # and the controller (Q100 CPU) highlighted via DeviceItemType=CPU.
        root, err = self._run()
        titles = [d.get("title") or "" for d in root.findall("diagram")]
        self.assertIn(q.PROFINET_TITLE, titles)
        net = [d for d in root.findall("diagram")
               if d.get("title") == q.PROFINET_TITLE][0]
        # one node box per device (35); a node box has the PN_BOX_W x PN_BOX_H size
        boxes = [s for s in net.find("shapes").findall("shape")
                 if abs(float(s.get("x2")) - float(s.get("x1")) - q.PN_BOX_W) < 1
                 and abs(float(s.get("y2")) - float(s.get("y1")) - q.PN_BOX_H) < 1]
        self.assertEqual(len(boxes), 35)
        # the subnet label reads /24 sourced from the REAL SubnetMask
        net_texts = " | ".join(i.get("text")
                               for i in net.find("inputs").findall("input"))
        self.assertIn("192.168.10.0/24", net_texts)
        # N2: the controller(s) are flagged by REAL DeviceItemType=CPU, not a host
        # IP. This plant genuinely carries TWO CPUs on the subnet (Q100 CPU 1512SP
        # F-1 @ .10 AND a CPU 1214C @ .95) — both are real CPUs, so both get the
        # heavy border + CONTROLADOR tag. The documented Q100 CPU is among them.
        heavy = [s for s in net.find("shapes").findall("shape")
                 if s.find("pen") is not None
                 and s.find("pen").get("widthF") == "2"]
        self.assertEqual(len(heavy), 2)   # two real CPUs in the .aml (never invented)
        self.assertIn("Q100_QUERETARO1  (CONTROLADOR)", net_texts)
        self.assertRegex(err, r"red PN\s*:.*35 PROFINET node")

    def test_rack_shows_modules_in_slot_order_with_real_data(self):
        root, _ = self._run()
        rack = [d for d in root.findall("diagram")
                if d.get("title") == q.RACK_TITLE][0]
        texts = [i.get("text") for i in rack.find("inputs").findall("input")]
        slots = [int(t.split()[1]) for t in texts if t.startswith("SLOT ")]
        # the 6 physical Q100 modules occupy slots 2..7 (F-DQ1500 split -> slot 4
        # appears twice); ascending (sorted) order, no fabricated slot.
        self.assertEqual(slots, sorted(slots))
        self.assertEqual(slots, [2, 3, 4, 4, 5, 6, 7])
        # real order numbers present (never invented)
        self.assertIn("6ES7 136-6BA00-0CA0", texts)   # F-DI150
        # visual-only
        self.assertEqual(len(rack.find("elements").findall("element")), 0)
        self.assertEqual(len(rack.find("conductors").findall("conductor")), 0)

    def test_index_lists_all_folios_including_itself(self):
        root, _ = self._run()
        diagrams = root.findall("diagram")
        idx = [d for d in diagrams if d.get("title") == q.INDEX_TITLE][0]
        texts = [i.get("text") for i in idx.find("inputs").findall("input")]
        pages = [int(t) for t in texts if t.isdigit()]
        # one page per folio, including the index's own SECTION_INDEX page
        self.assertEqual(sorted(pages),
                         sorted(int(d.get("order")) for d in diagrams))
        self.assertIn(q.SECTION_INDEX, pages)
        self.assertEqual(len(pages), len(diagrams))   # 22 == 22 (self-counted)
        # the index lists its own title + the rack title
        self.assertIn(q.INDEX_TITLE, texts)
        self.assertIn(q.RACK_TITLE, texts)

    def test_io_folio_titles_show_real_slot_not_none(self):
        root, _ = self._run()
        io_titles = [d.get("title") for d in root.findall("diagram")
                     if (d.get("title") or "").startswith("R0.S")]
        self.assertTrue(io_titles, "no I/O drawing folios found")
        for t in io_titles:
            self.assertNotIn("Slot None", t)
            self.assertNotIn("R0.S ", t)   # blank slot would show "R0.S "
        # F-DI150 sits at slot 2
        self.assertTrue(any("R0.S2 F-DI150" in t for t in io_titles))


class TagDiscoveryTest(unittest.TestCase):
    """Sibling PLCTags*.xlsx auto-discovery picks the HIGHER-COVERAGE table.

    E6 fix: selection is coverage-based (the table whose Names cover the
    station's I/O tags), not alphabetically-first nor a hard-coded S71200
    preference. For the IMV1 fixture the correct table is PLCTagsS71500.xlsx
    (47/48 tags matched) even though PLCTagsS71200.xlsx sorts first and matches 0.
    """

    FIXTURE = _imv1_io_channels()

    def setUp(self):
        if not self.FIXTURE.is_file():
            self.skipTest("IMV1 IO_Channels.xml fixture not present")

    def test_picks_higher_coverage_s71500_table(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            found = tia_to_qet._discover_tags(str(self.FIXTURE))
        self.assertIsNotNone(found, "no PLCTags*.xlsx discovered next to fixture")
        self.assertIn("S71500", Path(found).name)
        self.assertNotIn("S71200", Path(found).name)
        # the stderr note reports the chosen table + its coverage
        note = buf.getvalue()
        self.assertRegex(note, r"tags\s*:\s*selected\s+PLCTagsS71500\.xlsx")
        self.assertRegex(note, r"47/48 tags matched")

    def test_absent_dir_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            xml = Path(d) / "x_IO_Channels.xml"
            xml.write_text("<Project/>", encoding="utf-8")
            self.assertIsNone(tia_to_qet._discover_tags(str(xml)))


class CoverageSelectionUnitTest(unittest.TestCase):
    """Synthetic unit test of the coverage-based selection helper, with no real
    fixture: builds two tiny PLCTags*.xlsx (one matching the station's tags, one
    not) in a tempfile dir and asserts the matcher picks the matching one,
    tie-breaks alphabetically, and that --tags still overrides auto-selection."""

    @staticmethod
    def _write_xlsx(path: Path, names):
        """Write a minimal valid .xlsx with a Name+Comment header and rows
        (inline strings, so no sharedStrings needed)."""
        import zipfile

        def _esc(s):
            return (s.replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;"))

        def _row(cells):
            cs = "".join(
                f'<c t="inlineStr"><is><t>{_esc(v)}</t></is></c>' for v in cells)
            return f"<row>{cs}</row>"

        rows = [_row(["Name", "Comment"])]
        for n in names:
            rows.append(_row([n, f"desc {n}"]))
        sheet = (
            '<?xml version="1.0"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main"><sheetData>'
            + "".join(rows) + "</sheetData></worksheet>")
        content_types = (
            '<?xml version="1.0"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/'
            '2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats'
            '-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType='
            '"application/vnd.openxmlformats-officedocument.spreadsheetml.'
            'worksheet+xml"/></Types>')
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("[Content_Types].xml", content_types)
            zf.writestr("xl/worksheets/sheet1.xml", sheet)

    @staticmethod
    def _write_io(path: Path, tags):
        chans = "".join(
            f"<IOChannel><Address>%I0.{i}</Address><Tag>{t}</Tag></IOChannel>"
            for i, t in enumerate(tags))
        path.write_text(
            f'<Stations><Station Name="S"><Rack Name="R"><Module Name="M">'
            f'{chans}</Module></Rack></Station></Stations>',
            encoding="utf-8")

    def test_picks_matching_table_over_nonmatching(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            io_xml = dd / "x_IO_Channels.xml"
            self._write_io(io_xml, ["alpha", "beta", "gamma"])
            # 'PLCTags_a' sorts first but matches 0; 'PLCTags_z' matches all 3
            self._write_xlsx(dd / "PLCTags_a.xlsx", ["none1", "none2"])
            self._write_xlsx(dd / "PLCTags_z.xlsx", ["alpha", "beta", "gamma"])
            buf = io.StringIO()
            with redirect_stderr(buf):
                found = tia_to_qet._discover_tags(str(io_xml))
            self.assertEqual(Path(found).name, "PLCTags_z.xlsx")
            self.assertRegex(buf.getvalue(), r"3/3 tags matched")

    def test_tie_breaks_alphabetically(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            io_xml = dd / "x_IO_Channels.xml"
            self._write_io(io_xml, ["alpha", "beta"])
            # both match the same number (1); alphabetically-first wins
            self._write_xlsx(dd / "PLCTags_a.xlsx", ["alpha", "x"])
            self._write_xlsx(dd / "PLCTags_b.xlsx", ["beta", "y"])
            with redirect_stderr(io.StringIO()):
                found = tia_to_qet._discover_tags(str(io_xml))
            self.assertEqual(Path(found).name, "PLCTags_a.xlsx")

    def test_explicit_tags_flag_overrides_auto_selection(self):
        # main() uses (args.tags or _discover_tags(...)) — an explicit --tags
        # short-circuits auto-selection. Verify the override path is taken by
        # passing a bogus --tags and confirming it reaches the front-end (no
        # auto-discovery note, descriptions stay "" for the non-matching table).
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            io_xml = dd / "x_IO_Channels.xml"
            self._write_io(io_xml, ["alpha"])
            self._write_xlsx(dd / "PLCTags_match.xlsx", ["alpha"])
            explicit = dd / "PLCTags_explicit.xlsx"
            self._write_xlsx(explicit, ["other"])
            out = dd / "out.qet"
            buf = io.StringIO()
            with redirect_stderr(buf):
                rc = tia_to_qet.main(
                    [str(io_xml), "--tags", str(explicit), "-o", str(out)])
            self.assertEqual(rc, 0)
            # auto-selection note is NOT emitted when --tags is explicit
            self.assertNotIn("tags : selected", buf.getvalue())


class SiemensNoAmlOmissionTest(unittest.TestCase):
    """T2: the REAL 'omits without --aml' guarantee. SiemensRenderTestBase runs
    main() from the fixture's own directory, where _discover_aml finds the sibling
    fixture .aml — so those tests run WITH the .aml. Here we copy ONLY the
    IO_Channels.xml into an isolated temp dir (NO sibling .aml), so nothing is
    discovered, and assert the PROFINET network folio is OMITTED."""

    FIXTURE = _imv1_io_channels()

    def setUp(self):
        if not self.FIXTURE.is_file():
            self.skipTest("IMV1 IO_Channels.xml fixture not present")

    def test_no_aml_omits_network_rack_and_index_folios(self):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            # copy ONLY the IO_Channels.xml — no sibling .aml, no PLCTags*.xlsx
            iso_xml = Path(d) / self.FIXTURE.name
            iso_xml.write_bytes(self.FIXTURE.read_bytes())
            out = Path(d) / "tia.qet"
            # sanity: nothing auto-discoverable in the isolated dir
            self.assertIsNone(tia_to_qet._discover_aml(str(iso_xml)))
            with redirect_stderr(buf):
                rc = tia_to_qet.main([str(iso_xml), "-o", str(out)])
            self.assertEqual(rc, 0)
            root = ET.fromstring(out.read_text(encoding="utf-8"))
        titles = [d.get("title") or "" for d in root.findall("diagram")]
        # NET + RACK are .aml-derived => omitted without an .aml. (IDX is NOT
        # .aml-gated — the drawing index enumerates whatever folios exist, so it
        # still renders; it simply has no NET/RACK rows to list.)
        self.assertNotIn(q.PROFINET_TITLE, titles)
        self.assertNotIn(q.RACK_TITLE, titles)
        # and the index, present, does NOT list a (non-existent) PROFINET folio
        idx = [d for d in root.findall("diagram")
               if d.get("title") == q.INDEX_TITLE][0]
        idx_texts = [i.get("text") for i in idx.find("inputs").findall("input")]
        self.assertNotIn(q.PROFINET_TITLE, idx_texts)
        self.assertNotIn(q.RACK_TITLE, idx_texts)
        # the network-folio stderr line is emitted ONLY when a folio is drawn, so
        # with no .aml there is NO 'red PN' line (and no PROFINET_TITLE mention).
        err = buf.getvalue()
        self.assertNotIn("red PN", err)
        self.assertNotIn(q.PROFINET_TITLE, err)
        # core folios still present (cover/símbología/I-O/bornero/BOM/changelog)
        self.assertIn("Portada", titles)
        self.assertIn("Simbología", titles)


class AmlDiscoveryTest(unittest.TestCase):
    """T1: sibling *.aml auto-discovery (mirror of TagDiscoveryTest)."""

    def test_sibling_aml_discovered_deterministically(self):
        # a temp dir with sibling .aml files returns the FIRST (sorted) one.
        with tempfile.TemporaryDirectory() as d:
            xml = Path(d) / "x_IO_Channels.xml"
            xml.write_text("<Stations/>", encoding="utf-8")
            (Path(d) / "b_plant.aml").write_text("<CAEXFile/>", encoding="utf-8")
            (Path(d) / "a_plant.aml").write_text("<CAEXFile/>", encoding="utf-8")
            found = tia_to_qet._discover_aml(str(xml))
            self.assertIsNotNone(found)
            self.assertEqual(Path(found).name, "a_plant.aml")  # sorted-first

    def test_absent_dir_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            xml = Path(d) / "x_IO_Channels.xml"
            xml.write_text("<Stations/>", encoding="utf-8")
            self.assertIsNone(tia_to_qet._discover_aml(str(xml)))


class SplitSiblingPredicateTest(unittest.TestCase):
    """EYE-3 unit test for the sibling-detection predicate: two `[DO]`/`[DI]`
    modules sharing physical name + parent + slot + catalog are a pair; two
    unrelated modules are not. No fixture needed (pure predicate)."""

    @staticmethod
    def _mod(name, parent="Rack0", slot=4, catalog="6ES7 136-6DB00-0CA0"):
        return q.l2e.Module(name=name, catalog=catalog, parent=parent,
                            slot=slot, kind="DO", points=4, rack=0)

    def test_split_halves_are_paired(self):
        do = self._mod("F-DQ1500 [DO]")
        di = self._mod("F-DQ1500 [DI]")
        self.assertTrue(q._is_split_sibling_pair(do, di))

    def test_unrelated_modules_not_paired(self):
        a = self._mod("F-DQ1500 [DO]", slot=4)
        b = self._mod("F-DI150 [DI]", slot=2, catalog="6ES7 136-6BA00-0CA0")
        self.assertFalse(q._is_split_sibling_pair(a, b))

    def test_same_phys_but_different_slot_not_paired(self):
        # same physical name + catalog but DIFFERENT slot => not one card
        a = self._mod("F-DQ1500 [DO]", slot=4)
        b = self._mod("F-DQ1500 [DI]", slot=5)
        self.assertFalse(q._is_split_sibling_pair(a, b))

    def test_plain_modules_without_suffix_not_paired(self):
        # two plain (un-suffixed) modules sharing parent/slot/catalog are NOT a
        # split pair — the predicate requires the [KIND] suffix on both names.
        a = self._mod("M1")
        b = self._mod("M1")
        self.assertFalse(q._is_split_sibling_pair(a, b))


class SiemensSplitCardFolioTest(unittest.TestCase):
    """EYE-3 end-to-end: the F-DQ1500 split halves render on ONE folio (not two),
    side-by-side, with BOTH `[DO]` and `[DI]` headers, two card boxes at COL_X[0]
    and COL_X[1], all inside the page frame; and the Siemens total folio floor
    drops to 22 (was 23) while the bornero count stays 7 (per-half borneros are
    unchanged). Gated on BOTH the IMV1 IO_Channels.xml and .aml."""

    IO = _imv1_io_channels()
    AML = _imv1_aml()

    def setUp(self):
        if not (self.IO.is_file() and self.AML.is_file()):
            self.skipTest("IMV1 IO_Channels.xml or .aml fixture not present")

    def _run(self):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "tia.qet"
            with redirect_stderr(buf):
                rc = tia_to_qet.main([str(self.IO), "--aml", str(self.AML),
                                      "-o", str(out)])
            self.assertEqual(rc, 0)
            xml = out.read_text(encoding="utf-8")
        return ET.fromstring(xml), buf.getvalue()

    def test_split_card_on_one_folio_with_both_headers_and_two_boxes(self):
        root, _ = self._run()
        # exactly ONE folio merges the F-DQ1500 halves (title carries [DO+DI])
        merged = [d for d in root.findall("diagram")
                  if "[DO+DI]" in (d.get("title") or "")]
        self.assertEqual(len(merged), 1)
        folio = merged[0]
        # and the un-merged per-half drawing folios are GONE: no drawing folio
        # title is bare "F-DQ1500 [DO]"/"[DI]" any more.
        all_titles = [d.get("title") or "" for d in root.findall("diagram")]
        self.assertFalse(any(t.endswith("[DO])") or t.endswith("[DI])")
                             for t in all_titles))
        # BOTH half headers present inside the merged folio
        texts = [i.get("text") or "" for i in folio.find("inputs").findall("input")]
        self.assertIn("-F-DQ1500 [DO]", texts)
        self.assertIn("-F-DQ1500 [DI]", texts)
        # two card boxes, left at COL_X[0]-BOX_LEFT and right at COL_X[1]-BOX_LEFT
        rects = [s for s in folio.find("shapes").findall("shape")
                 if s.get("type") == "Rectangle"]
        left_edges = sorted({float(r.get("x1")) for r in rects})
        self.assertEqual(left_edges,
                         sorted({float(q.COL_X[0] - q.BOX_LEFT),
                                 float(q.COL_X[1] - q.BOX_LEFT)}))
        # full drawn extent inside the page frame (x<=1010, y<=660) over
        # shapes + inputs + elements
        xs, ys = [], []
        for i in folio.find("inputs").findall("input"):
            xs.append(float(i.get("x"))); ys.append(float(i.get("y")))
        for e in folio.find("elements").findall("element"):
            xs.append(float(e.get("x"))); ys.append(float(e.get("y")))
        for r in rects:
            xs.append(float(r.get("x2"))); ys.append(float(r.get("y2")))
        self.assertLessEqual(max(xs), 1010)
        self.assertLessEqual(max(ys), 660)

    def test_total_folio_floor_22_bornero_still_7(self):
        root, err = self._run()
        self.assertEqual(len(root.findall("diagram")), 22)
        self.assertRegex(err, r"bornero\s*:\s*7\b")

    def test_both_halves_contribute_bom_module_rows(self):
        # both halves still emit their own (module) BOM row — the merged folio
        # indexes every drawn point. The summary's "bom : N rows (M module, ...)"
        # must count BOTH F-DQ1500 halves (7 module rows total across 7 cards).
        _, err = self._run()
        m = re.search(r"bom\s*:\s*\d+\s+rows\s*\((\d+)\s+module", err)
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1)), 7)


class SiemensPowerFolioIntegrationTest(unittest.TestCase):
    """ALIM (E5) end-to-end (gated on BOTH the IMV1 IO_Channels.xml and .aml):
    with --power-config the Siemens render gains the config-driven 'Alimentación'
    one-line folio (22 -> 23 diagrams, listed BY TITLE and in the drawing index);
    without the flag it stays 22. The temp config is written to a tempfile dir,
    never into the repo."""

    IO = _imv1_io_channels()
    AML = _imv1_aml()

    POWER = {
        "system_voltage": "120 VAC",
        "input_breaker":  {"label": "Q1", "rating": "2 A"},
        "power_supply":   {"label": "PS1", "rating": "10 A"},
        "output_breaker": {"label": "Q2", "rating": "10 A"},
        "loads": "Control / PLC",
        "transformer": None,
        "ups": None,
    }

    def setUp(self):
        if not (self.IO.is_file() and self.AML.is_file()):
            self.skipTest("IMV1 IO_Channels.xml or .aml fixture not present")

    def _run(self, with_power):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "tia.qet"
            argv = [str(self.IO), "--aml", str(self.AML)]
            if with_power:
                cfg = Path(d) / "power.json"
                cfg.write_text(json.dumps(self.POWER), encoding="utf-8")
                argv += ["--power-config", str(cfg)]
            argv += ["-o", str(out)]
            with redirect_stderr(buf):
                rc = tia_to_qet.main(argv)
            self.assertEqual(rc, 0)
            xml = out.read_text(encoding="utf-8")
        return ET.fromstring(xml), buf.getvalue()

    def test_with_power_config_adds_alimentacion_folio(self):
        root, err = self._run(with_power=True)
        diagrams = root.findall("diagram")
        self.assertEqual(len(diagrams), 23)   # was 22, +1 'Alimentación'
        titles = [d.get("title") or "" for d in diagrams]
        self.assertIn(q.POWER_TITLE, titles)
        self.assertRegex(err, r"alim\s*:\s*1\b")
        # the drawing-index folio lists the new folio's section page
        idx = [d for d in diagrams if d.get("title") == q.INDEX_TITLE][0]
        idx_pages = [int(t) for t in
                     (i.get("text") for i in idx.find("inputs").findall("input"))
                     if t.isdigit()]
        self.assertIn(q.SECTION_ALIM, idx_pages)
        # index still self-counts every folio (including ALIM and itself)
        self.assertEqual(len(idx_pages), len(diagrams))

    def test_without_power_config_stays_22(self):
        root, err = self._run(with_power=False)
        diagrams = root.findall("diagram")
        self.assertEqual(len(diagrams), 22)
        titles = [d.get("title") or "" for d in diagrams]
        self.assertNotIn(q.POWER_TITLE, titles)
        self.assertRegex(err, r"alim\s*:\s*0\b")


class SiemensDescriptionRenderTest(unittest.TestCase):
    """E6 headline fix end-to-end (gated on BOTH the IMV1 IO_Channels.xml and
    .aml): with NO --tags, coverage-based selection picks PLCTagsS71500.xlsx, so
    real descriptions now render on the I/O folios (they were blank when the
    alphabetically-first S71200 table — 0 matches — was chosen). The Siemens
    floor (48 drawn / 40 RESERVA) is UNCHANGED — descriptions are additive text."""

    IO = _imv1_io_channels()
    AML = _imv1_aml()

    def setUp(self):
        if not (self.IO.is_file() and self.AML.is_file()):
            self.skipTest("IMV1 IO_Channels.xml or .aml fixture not present")

    def test_descriptions_render_and_floor_unchanged(self):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "tia.qet"
            with redirect_stderr(buf):
                rc = tia_to_qet.main(
                    [str(self.IO), "--aml", str(self.AML), "-o", str(out)])
            self.assertEqual(rc, 0)
            xml = out.read_text(encoding="utf-8")
        err = buf.getvalue()
        # coverage-based selection picked the S71500 table
        self.assertRegex(err, r"tags\s*:\s*selected\s+PLCTagsS71500\.xlsx")
        # a known description from PLCTagsS71500.xlsx now appears in the .qet
        self.assertIn("UV Door 1 Open", xml,
                      "I/O descriptions did not render (tag-table selection bug)")
        # floor unchanged: descriptions are additive TEXT, not points/spares
        m_pts = re.search(r"points\s*:\s*(\d+)\s+drawn,\s*(\d+)\s+skipped", err)
        self.assertIsNotNone(m_pts, f"no points line:\n{err}")
        self.assertEqual(int(m_pts.group(1)), 48)   # 48 drawn
        self.assertEqual(int(m_pts.group(2)), 40)   # 40 RESERVA


class ControllerCpuTest(unittest.TestCase):
    """E6 Part B: the IR carries the owning-PLC CPU type, derived from real .aml
    data (controller PROFINET node whose IP == the station's network_address).
    Never invented: None without an .aml."""

    IO = _imv1_io_channels()
    AML = _imv1_aml()

    def test_controller_cpu_populated_for_imv1(self):
        if not (self.IO.is_file() and self.AML.is_file()):
            self.skipTest("IMV1 IO_Channels.xml or .aml fixture not present")
        ir = plc_ir.build_tia_project(str(self.IO), None, str(self.AML))
        # Q100-Cooling1/UV @ 192.168.10.10 -> CPU 1512SP F-1 PN
        self.assertEqual(ir.controller_cpu, "CPU 1512SP F-1 PN")

    def test_controller_cpu_none_without_aml(self):
        if not self.IO.is_file():
            self.skipTest("IMV1 IO_Channels.xml fixture not present")
        ir = plc_ir.build_tia_project(str(self.IO), None, None)
        self.assertIsNone(ir.controller_cpu)   # never invented

    def test_controller_cpu_none_for_rockwell(self):
        # default field => Rockwell IR is unaffected (None)
        ir = plc_ir.PlcProject(name="X", source_vendor="rockwell")
        self.assertIsNone(ir.controller_cpu)


if __name__ == "__main__":
    unittest.main()
