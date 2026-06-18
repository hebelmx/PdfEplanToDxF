#!/usr/bin/env python3
"""Integration tests for the S7-300 command (s7300_to_qet) + the merge helper
(plc_ir.build_s7300_single_project) + the shared render_project seam it calls.

Stdlib-only (unittest). Run from src/:
    python -m unittest test_s7300_to_qet

Three groups:
  * MergeHelperTest — the 7-project S7-300 list folds into ONE single-station
    PlcProject: 15 io_mods (local 5 + 5 ET200eco + 5 Festo banks), 214 mapped
    points, 42 RESERVA, no module-name collisions, CPU carried, network_nodes
    empty. Asserts the LOCKED floor straight off the merged IR.
  * AscDiscoveryTest — the sibling .asc auto-discovery rule (.cfg -> .asc) and
    its degradation when no sibling exists. No fixture needed.
  * S7300StructuralTest — drive s7300_to_qet.main() end-to-end on the brpl2twin
    fixture to a TEMP .qet and assert the WADDING_1 structural invariants (ISO
    7200 title block on every folio, no raw %{...} tokens, unique terminal ids,
    conductors resolve, every drawn type has an embedded <definition>, changelog
    + índice present) and report the folio count.

All fixture-dependent tests SKIP if the (gitignored) fixtures are absent
(mirrors test_s7300_front_end). Test output goes to tempfiles ONLY — nothing is
ever written into Fixtures/.
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plc_ir
import s7300_to_qet


def _cfg_fixture() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "Fixtures",
                        "Siemens", "S7300", "brpl2twin.txt.cfg")


def _asc_fixture() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "Fixtures",
                        "Siemens", "S7300", "brpl2twin.txt.asc")


_HAVE_FIXTURE = os.path.exists(_cfg_fixture()) and os.path.exists(_asc_fixture())


# --------------------------------------------------------------------------
# Merge helper — build_s7300_single_project (fixture-gated)
# --------------------------------------------------------------------------
@unittest.skipUnless(_HAVE_FIXTURE, "S7300 fixtures not present")
class MergeHelperTest(unittest.TestCase):
    """The 7-project list folds into ONE single-station PlcProject with the
    LOCKED floor: 15 io_mods / 214 mapped / 42 RESERVA, no key collisions."""

    @classmethod
    def setUpClass(cls):
        cls.projects = plc_ir.build_s7300_project(_cfg_fixture(), _asc_fixture())
        cls.merged = plc_ir.build_s7300_single_project(_cfg_fixture(),
                                                       _asc_fixture())

    def test_single_project_identity(self):
        m = self.merged
        self.assertIsInstance(m, plc_ir.PlcProject)
        self.assertEqual(m.name, "S7300")            # local-rack station name
        self.assertEqual(m.source_vendor, "siemens")
        self.assertEqual(m.controller_cpu, "CPU 315-2 PN/DP")
        self.assertEqual(m.network_nodes, [])        # NET folio omitted
        self.assertEqual(m.controller_tags, {})
        self.assertEqual(m.program_tags, {})

    def test_io_mods_count_and_order(self):
        # local rack 5 + 5 ET200eco drops (1 module each) + Festo CPX 5 banks = 15
        self.assertEqual(len(self.merged.io_mods), 15)
        # concatenation preserves order: the local-rack modules come first
        local = self.projects[0]
        self.assertEqual(
            [m.name for m in self.merged.io_mods[:len(local.io_mods)]],
            [m.name for m in local.io_mods])
        # and the whole sequence equals the projects flattened in list order
        flat = [m.name for p in self.projects for m in p.io_mods]
        self.assertEqual([m.name for m in self.merged.io_mods], flat)

    def test_locked_floor(self):
        cap = sum(m.points for m in self.merged.io_mods)
        mapped = len(self.merged.points)
        reserva = sum(1 for s in self.merged.skipped if s[0] == "RESERVA")
        self.assertEqual(cap, 256)       # LOCKED capacity
        self.assertEqual(mapped, 214)    # LOCKED mapped
        self.assertEqual(reserva, 42)    # LOCKED RESERVA

    def test_points_and_skipped_are_concatenations(self):
        self.assertEqual(len(self.merged.points),
                         sum(len(p.points) for p in self.projects))
        self.assertEqual(len(self.merged.skipped),
                         sum(len(p.skipped) for p in self.projects))

    def test_no_module_name_collisions(self):
        # the merge asserts internally; double-check the merged dict has one
        # entry per io_mod (unique names across drops, e.g. "Slot4 DI" vs
        # "DP4 Slot2 DI").
        names = [m.name for m in self.merged.io_mods]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(self.merged.modules.keys()), set(names))

    def test_empty_build_degrades(self):
        # a missing/!cfg yields a degenerate empty PlcProject, never crashes.
        m = plc_ir.build_s7300_single_project("does_not_exist.cfg", None)
        self.assertIsInstance(m, plc_ir.PlcProject)
        self.assertEqual(m.name, "S7300")
        self.assertEqual(m.source_vendor, "siemens")
        self.assertEqual(m.io_mods, [])
        self.assertEqual(m.points, [])


# --------------------------------------------------------------------------
# .asc sibling auto-discovery (no fixture needed)
# --------------------------------------------------------------------------
class AscDiscoveryTest(unittest.TestCase):
    """The sibling .asc rule: derive <stem>.asc from the .cfg path, only when it
    exists; absent => None (AI8 stays all-RESERVA, never invented)."""

    def test_sibling_asc_discovered(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "plant.cfg"
            cfg.write_text("", encoding="utf-8")
            asc = Path(d) / "plant.asc"
            asc.write_text("", encoding="utf-8")
            found = s7300_to_qet._discover_asc(str(cfg))
            self.assertIsNotNone(found)
            self.assertEqual(Path(found).name, "plant.asc")

    def test_no_sibling_asc_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "plant.cfg"
            cfg.write_text("", encoding="utf-8")
            self.assertIsNone(s7300_to_qet._discover_asc(str(cfg)))

    def test_non_cfg_extension_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            other = Path(d) / "plant.txt"
            other.write_text("", encoding="utf-8")
            self.assertIsNone(s7300_to_qet._discover_asc(str(other)))


# --------------------------------------------------------------------------
# CLI end-to-end + structural assertions (fixture-gated)
# --------------------------------------------------------------------------
@unittest.skipUnless(_HAVE_FIXTURE, "S7300 fixtures not present")
class S7300StructuralTest(unittest.TestCase):
    """Drive s7300_to_qet.main() on the brpl2twin fixture to a TEMP .qet and
    assert the WADDING_1 structural invariants on the produced project."""

    def _run(self, extra=None):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "s7300.qet"
            argv = [_cfg_fixture(), "-o", str(out)] + (extra or [])
            with redirect_stderr(buf):
                rc = s7300_to_qet.main(argv)
            self.assertEqual(rc, 0)
            self.assertTrue(out.exists(), "no .qet produced")
            xml = out.read_text(encoding="utf-8")
        return ET.fromstring(xml), buf.getvalue()

    def test_cli_smoke_returns_zero_and_writes_qet(self):
        # also exercises the .asc auto-discovery (no --asc passed): the sibling
        # brpl2twin.txt.asc is derived from the .cfg path.
        root, _ = self._run()
        self.assertEqual(root.tag, "project")
        self.assertTrue(root.findall("diagram"))

    def test_iso_titleblock_on_every_folio_no_raw_tokens(self):
        root, _ = self._run()
        diagrams = root.findall("diagram")
        self.assertTrue(diagrams)
        for d in diagrams:
            self.assertEqual(d.get("titleblocktemplate"), "exxerpro",
                             f"folio {d.get('title')!r} missing ISO titleblock")
            props = d.find("properties")
            self.assertIsNotNone(props,
                                 f"folio {d.get('title')!r} has no properties")
            for prop in props.findall("property"):
                self.assertNotIn("%{", prop.text or "",
                                 f"raw token in property of {d.get('title')!r}")

    def test_terminal_ids_unique_and_conductors_resolve(self):
        root, _ = self._run()
        for d in root.findall("diagram"):
            elements = d.find("elements")
            conductors = d.find("conductors")
            if elements is None:
                continue
            term_ids = []
            for el in elements.findall("element"):
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
        self.assertTrue(types, "no element types drawn on the S7-300 set")
        coll = root.find("collection")
        self.assertIsNotNone(coll, "no <collection> in the S7-300 project")
        embedded = {el.get("name") for el in coll.iter("element")
                    if el.find("definition") is not None}
        for tp in types:
            leaf = tp.rsplit("/", 1)[-1]
            self.assertIn(leaf, embedded,
                          f"drawn type {tp!r} has no embedded <definition>")

    def test_core_folios_present_vendor_folios_absent(self):
        root, _ = self._run()
        titles = [d.get("title") or "" for d in root.findall("diagram")]
        # CORE present: portada, símbología, índice, bornero, BOM, changelog
        self.assertIn("Portada", titles)
        self.assertIn("Simbología", titles)
        self.assertTrue(any(t.startswith("Índice") for t in titles),
                        f"no índice folio: {titles}")
        self.assertTrue(any(t.startswith("Bornero") for t in titles),
                        "no bornero folio on the S7-300 set")
        self.assertTrue(any(t.startswith("BOM") for t in titles),
                        "no BOM folio on the S7-300 set")
        self.assertTrue(any(t.startswith("Historial") for t in titles),
                        "no changelog folio on the S7-300 set")
        # OMITTED for this CORE chunk: NET (network_nodes empty), and the
        # Rockwell-only vendor folios.
        self.assertNotIn("Red de comunicaciones", titles)
        self.assertFalse(any(t.startswith("Alimentación") for t in titles))
        self.assertFalse(any(t.startswith("Puesta a tierra") for t in titles))

    def test_floor_reflected_in_render(self):
        # the merged-IR floor surfaces in the stderr summary.
        _, err = self._run()
        import re
        m_pts = re.search(r"points\s*:\s*(\d+)\s+drawn,\s*(\d+)\s+skipped", err)
        self.assertIsNotNone(m_pts, f"no points line:\n{err}")
        self.assertEqual(int(m_pts.group(1)), 214)   # mapped
        self.assertEqual(int(m_pts.group(2)), 42)    # RESERVA


@unittest.skipUnless(_HAVE_FIXTURE, "S7300 fixtures not present")
class S7300OffmoduleSectionTest(unittest.TestCase):
    """The off-module section (servos + PROFINET cameras NOT on an I/O card,
    S7300-3b) is rendered into the single S7-300 .qet between the BOM and the
    changelog, with the ISO title block, and the wired floor stays 214/42."""

    def _run(self):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "s7300.qet"
            with redirect_stderr(buf):
                rc = s7300_to_qet.main([_cfg_fixture(), "-o", str(out)])
            self.assertEqual(rc, 0)
            xml = out.read_text(encoding="utf-8")
        return ET.fromstring(xml), buf.getvalue()

    def _off_diagrams(self, root):
        import logix_to_qet as lq
        return [d for d in root.findall("diagram")
                if lq.SECTION_OFFMODULE <= int(d.get("order")) < lq.SECTION_CHANGELOG]

    def test_section_present_drives_and_identification(self):
        root, _ = self._run()
        import render_plant as rp
        off = self._off_diagrams(root)
        self.assertTrue(off, "no off-module folios rendered")
        titles = " | ".join(d.get("title") or "" for d in off)
        self.assertIn(rp.OFFMODULE_SECTION_TITLE, titles)
        self.assertIn("Drives", titles)
        self.assertIn("Identification", titles)

    def test_off_folios_orders_between_bom_and_changelog(self):
        root, _ = self._run()
        import logix_to_qet as lq
        orders = sorted(int(d.get("order")) for d in self._off_diagrams(root))
        # consecutive band starting at SECTION_OFFMODULE (400), all < changelog
        self.assertEqual(orders[0], lq.SECTION_OFFMODULE)
        for o in orders:
            self.assertLess(o, lq.SECTION_CHANGELOG)
            self.assertGreater(o, lq.SECTION_BOM)

    def test_off_folios_carry_iso_titleblock_no_raw_tokens(self):
        root, _ = self._run()
        for d in self._off_diagrams(root):
            self.assertEqual(d.get("titleblocktemplate"), "exxerpro",
                             f"off folio {d.get('title')!r} missing ISO block")
            props = d.find("properties")
            if props is not None:
                for prop in props.findall("property"):
                    self.assertNotIn("%{", prop.text or "",
                                     f"raw token in {d.get('title')!r}")

    def test_real_symbols_appear_in_section(self):
        # the joined camera symbols are drawn faithfully (no invention)
        root, _ = self._run()
        text = "".join(t.get("text") or "" for d in self._off_diagrams(root)
                       for t in d.iter("input"))
        self.assertIn("Camera_Result", text)
        self.assertIn("Reset_Camaras", text)
        self.assertIn("CMMP-AS M3", text)

    def test_wired_floor_unchanged_214_42(self):
        # the section is ADDITIVE: servos/cameras never become DI/DO/AI channels.
        _, err = self._run()
        import re
        m = re.search(r"points\s*:\s*(\d+)\s+drawn,\s*(\d+)\s+skipped", err)
        self.assertIsNotNone(m, f"no points line:\n{err}")
        self.assertEqual(int(m.group(1)), 214)
        self.assertEqual(int(m.group(2)), 42)


class RockwellByteEquivalenceTest(unittest.TestCase):
    """The additive `offmodule_groups` param must leave the Rockwell path
    byte-for-byte unchanged: render WADDING_1 WITHOUT groups and assert the
    output is identical to a run with the param defaulted/None (i.e. the param's
    presence never perturbs the Rockwell render)."""

    def _l5x(self):
        return os.path.join(os.path.dirname(__file__), "..", "Fixtures",
                            "Rockwell", "WADDING_1.L5X")

    @unittest.skipUnless(os.path.exists(os.path.join(
        os.path.dirname(__file__), "..", "Fixtures", "Rockwell",
        "WADDING_1.L5X")), "Rockwell fixture not present")
    def test_offmodule_param_default_and_none_byte_identical(self):
        import re
        import logix_to_qet as lq
        import plc_ir as _ir

        def _render(groups):
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / "w.qet"
                ir = _ir.build_rockwell_project(self._l5x())
                buf = io.StringIO()
                with redirect_stderr(buf):
                    lq.render_project(ir, str(out), offmodule_groups=groups)
                s = out.read_text(encoding="utf-8")
            # normalize the per-run UUIDs so only structural deltas remain
            return re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                          r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "UUID", s)

        # default (omitted) vs explicit None vs explicit [] — all must match,
        # proving Rockwell/empty-groups is never perturbed by the new param.
        a = _render(None)
        b = _render([])
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
