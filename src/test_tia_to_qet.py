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


def _imv1_io_channels() -> Path:
    root = Path(__file__).resolve().parent.parent / "Fixtures" / "Siemens" / "TiaPortal"
    return root / "IMV1_QRO001_08AGO21_V15_IO_Channels.xml"


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
        # per-card I/O folios: 6 cards with mapped tags (F-DQ1500 [DI] is 0-mapped
        # → skipped by the 'one folio per card WITH mapped tags' rule).
        io_folios = [t for t in titles
                     if "F-DI" in t or "F-DQ" in t or t.startswith("DI")
                     or t.startswith("DQ")]
        self.assertEqual(len(io_folios), 6, f"I/O folios: {io_folios}")


class SiemensStderrFloorTest(SiemensRenderTestBase):
    """The Siemens stderr floor: 48 mapped points, 40 RESERVA channels, 88
    channels total — derived from the summary. 'spare' (drawn reserves) reads 36
    because the 0-mapped F-DQ1500 [DI] card is skipped (Rockwell rule), so its 4
    spare channels are not DRAWN; the 4 still appear in the 40 IR-level RESERVA
    (the '40 skipped' on the points line)."""

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
        # 6 drawing folios (0-mapped card skipped per the Rockwell rule)
        self.assertRegex(err, r"folios\s*:\s*6\b")
        # the SEPARATE drawn-spare counter: 36 reserves over 5 cards (the 0-mapped
        # card contributes neither a folio nor a drawn reserve).
        m = re.search(r"spare\s*:\s*(\d+)\s+reserve terminal", err)
        self.assertIsNotNone(m, f"no spare line:\n{err}")
        self.assertEqual(int(m.group(1)), 36)

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


if __name__ == "__main__":
    unittest.main()
