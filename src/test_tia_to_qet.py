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
        # CHAN: every I/O card now renders, including the all-spare F-DQ1500 [DI]
        # half. This filter catches the F-D* card titles (drawing AND bornero):
        # 4 drawing (F-DI150, F-DI156, F-DQ1500 [DO], F-DQ1500 [DI]) + 4 matching
        # borneros = 8 (was 6 when the 0-mapped F-DQ1500 [DI] was skipped).
        io_folios = [t for t in titles
                     if "F-DI" in t or "F-DQ" in t or t.startswith("DI")
                     or t.startswith("DQ")]
        self.assertEqual(len(io_folios), 8, f"I/O folios: {io_folios}")
        # the all-spare F-DQ1500 [DI] half now renders a drawing folio
        self.assertTrue(any(t.startswith("R0") and "F-DQ1500 [DI]" in t
                            for t in titles),
                        "F-DQ1500 [DI] all-spare half did not render")


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

    def test_seven_drawing_folios_and_drawn_spares(self):
        _, err = self._run()
        # CHAN: 7 drawing folios — every I/O card drawn, incl. the all-spare
        # F-DQ1500 [DI] half (was 6 when that 0-mapped half was skipped).
        self.assertRegex(err, r"folios\s*:\s*7\b")
        # the SEPARATE drawn-spare counter now reads the full 40: every unused
        # channel is drawn, so the drawn reserves match the IR-level RESERVA.
        m = re.search(r"spare\s*:\s*(\d+)\s+reserve terminal", err)
        self.assertIsNotNone(m, f"no spare line:\n{err}")
        self.assertEqual(int(m.group(1)), 40)

    def test_floor_match_breakdown_by_type(self):
        """The REAL false-positive guard for the Siemens pipeline: assert the
        EXACT per-type match breakdown, not just the matched total. The IMV1
        vocabulary matches only `push_button` today (never-invent: the other tags
        have no confident symbol) → 2 matched, 46 generic (48 drawn - 2). A future
        change that mis-classifies a Siemens tag onto a Rockwell symbol turns this
        red rather than shipping a wrong-type match silently."""
        _, err = self._run()
        breakdown, generic = _parse_match_breakdown(err)
        self.assertIsNotNone(breakdown, f"no per-type breakdown in summary:\n{err}")
        self.assertEqual(breakdown, {"push_button": 2})
        self.assertEqual(sum(breakdown.values()), 2)
        self.assertEqual(generic, 46)   # 48 drawn - 2 matched, 0 false positives

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
        # was 21 (NET) -> 23 with RACK + IDX added
        self.assertEqual(len(root.findall("diagram")), 23)

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
        self.assertEqual(len(pages), len(diagrams))   # 23 == 23 (self-counted)
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
    """Sibling PLCTags*.xlsx auto-discovery prefers the S7-1200 table."""

    FIXTURE = _imv1_io_channels()

    def setUp(self):
        if not self.FIXTURE.is_file():
            self.skipTest("IMV1 IO_Channels.xml fixture not present")

    def test_prefers_s71200_table(self):
        found = tia_to_qet._discover_tags(str(self.FIXTURE))
        self.assertIsNotNone(found, "no PLCTags*.xlsx discovered next to fixture")
        self.assertIn("S71200", Path(found).name)

    def test_absent_dir_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            xml = Path(d) / "x_IO_Channels.xml"
            xml.write_text("<Project/>", encoding="utf-8")
            self.assertIsNone(tia_to_qet._discover_tags(str(xml)))


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


if __name__ == "__main__":
    unittest.main()
