#!/usr/bin/env python3
"""Unit + integration tests for the TIA CAx/AML hardware-map parser (TIA-3).

Stdlib-only (unittest). Run from src/:
    python -m unittest test_tia_aml
or via discovery:
    python -m unittest discover -p "test_*.py"

Two groups:
  * Synthetic-AML tests — exercise the parser + join with a tiny hand-built .aml
    (masked '?' digits, never-invent, split-module suffix join) WITHOUT any
    fixture, so they run everywhere.
  * Fixture-gated tests — assert the real extracted order#/TypeName/PROFINET for
    known IMV1 modules and the IR-join, gated on the (gitignored) .aml fixture.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tia_aml
import tia_front_end as tia
import plc_ir


def _imv1_aml() -> Path:
    root = Path(__file__).resolve().parent.parent / "Fixtures" / "Siemens" / "TiaPortal"
    return root / "IMV1_QRO001_08AGO21_V15.aml"


def _imv1_io_channels() -> Path:
    root = Path(__file__).resolve().parent.parent / "Fixtures" / "Siemens" / "TiaPortal"
    return root / "IMV1_QRO001_08AGO21_V15_IO_Channels.xml"


# A minimal-but-faithful AML: one ET200SP station with a head module (order# +
# PROFINET), one normal I/O module, one module with a MASKED order number, and a
# rack-child that is NOT a module (no TypeName / OrderNumber) which must be
# skipped. NetworkAddress lives deep under the head module's interface.
_SYNTHETIC_AML = """<?xml version="1.0" encoding="utf-8"?>
<CAEXFile SchemaVersion="2.15">
  <InstanceHierarchy Name="ih">
    <InternalElement ID="p" Name="Proj">
      <InternalElement ID="g" Name="Ungrouped devices">
        <InternalElement ID="dev" Name="StationA">
          <Attribute Name="TypeIdentifier"><Value>System:Device.ET200SP</Value></Attribute>
          <InternalElement ID="rack" Name="Rack_0">
            <Attribute Name="TypeIdentifier"><Value>System:Rack.ET200SP</Value></Attribute>
            <InternalElement ID="head" Name="HeadA">
              <Attribute Name="TypeName"><Value>CPU 1512SP F-1 PN</Value></Attribute>
              <Attribute Name="DeviceItemType"><Value>CPU</Value></Attribute>
              <Attribute Name="TypeIdentifier"><Value>OrderNumber:6ES7 512-1SK01-0AB0</Value></Attribute>
              <InternalElement ID="pn" Name="PROFINET interface_1">
                <InternalElement ID="node" Name="E1">
                  <Attribute Name="NetworkAddress"><Value>192.168.10.55</Value></Attribute>
                </InternalElement>
              </InternalElement>
            </InternalElement>
            <InternalElement ID="m1" Name="DI10_11">
              <Attribute Name="TypeName"><Value>DI 16x24VDC ST</Value></Attribute>
              <Attribute Name="TypeIdentifier"><Value>OrderNumber:6ES7 131-6BH00-0BA0</Value></Attribute>
              <InternalElement ID="m1c" Name="DI10_11">
                <ExternalInterface ID="c0" Name="Channel_DI_0"/>
                <ExternalInterface ID="c1" Name="Channel_DI_1"/>
              </InternalElement>
            </InternalElement>
            <InternalElement ID="m2" Name="MASKED1">
              <Attribute Name="TypeName"><Value>Masked module</Value></Attribute>
              <Attribute Name="TypeIdentifier"><Value>OrderNumber:6ES7 1??-6BH00-0BA0</Value></Attribute>
            </InternalElement>
            <InternalElement ID="notmod" Name="Port_1">
              <Attribute Name="PositionNumber"><Value>1</Value></Attribute>
            </InternalElement>
          </InternalElement>
        </InternalElement>
      </InternalElement>
    </InternalElement>
  </InstanceHierarchy>
</CAEXFile>
"""


class ParseHelpersTest(unittest.TestCase):
    def test_order_number_strips_prefix(self):
        self.assertEqual(
            tia_aml._order_number("OrderNumber:6ES7 136-6BA00-0CA0"),
            "6ES7 136-6BA00-0CA0",
        )

    def test_order_number_keeps_masked_question_marks(self):
        self.assertEqual(
            tia_aml._order_number("OrderNumber:6ES7 1??-6BH00-0BA0"),
            "6ES7 1??-6BH00-0BA0",
        )

    def test_non_order_typeidentifier_yields_blank(self):
        # System:Device.ET200SP is NOT an order number -> "" (never invented)
        self.assertEqual(tia_aml._order_number("System:Device.ET200SP"), "")
        self.assertEqual(tia_aml._order_number(None), "")
        self.assertEqual(tia_aml._order_number(""), "")


class SyntheticAmlTest(unittest.TestCase):
    def _parse(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.aml"
            p.write_text(_SYNTHETIC_AML, encoding="utf-8")
            return tia_aml.parse_aml(str(p))

    def test_modules_extracted(self):
        hw = self._parse()
        # head + DI10_11 + MASKED1 (the Port_1 non-module is skipped)
        self.assertEqual(
            sorted(m for (_st, m) in hw), ["DI10_11", "HeadA", "MASKED1"]
        )

    def test_head_module_order_typename_and_profinet(self):
        hw = self._parse()
        head = hw[("StationA", "HeadA")]
        self.assertEqual(head["order_number"], "6ES7 512-1SK01-0AB0")
        self.assertEqual(head["type_name"], "CPU 1512SP F-1 PN")
        self.assertEqual(head["network_address"], "192.168.10.55")
        self.assertEqual(head["device_item_type"], "CPU")

    def test_io_module_channel_count(self):
        hw = self._parse()
        self.assertEqual(hw[("StationA", "DI10_11")]["channels"], 2)

    def test_io_module_inherits_station_profinet(self):
        # ET200SP I/O modules sit behind the head module's single PROFINET node
        hw = self._parse()
        self.assertEqual(
            hw[("StationA", "DI10_11")]["network_address"], "192.168.10.55"
        )

    def test_masked_order_number_kept_verbatim(self):
        hw = self._parse()
        self.assertEqual(
            hw[("StationA", "MASKED1")]["order_number"], "6ES7 1??-6BH00-0BA0"
        )

    def test_non_module_rack_child_skipped(self):
        hw = self._parse()
        self.assertNotIn(("StationA", "Port_1"), hw)

    def test_hardware_for_station_exact(self):
        hw = self._parse()
        st = tia_aml.hardware_for_station(hw, "StationA")
        self.assertIn("DI10_11", st)
        self.assertEqual(st["DI10_11"]["order_number"], "6ES7 131-6BH00-0BA0")

    def test_hardware_for_unknown_station_merges_all(self):
        # an unknown station name falls back to the merged module map so unique
        # module names still join (graceful, never fails)
        hw = self._parse()
        st = tia_aml.hardware_for_station(hw, "does-not-exist")
        self.assertIn("DI10_11", st)


class PhysicalNameTest(unittest.TestCase):
    def test_strips_split_suffix(self):
        self.assertEqual(tia._physical_name("F-DQ1500 [DI]"), "F-DQ1500")
        self.assertEqual(tia._physical_name("F-DQ1500 [DO]"), "F-DQ1500")

    def test_passes_unsuffixed_through(self):
        self.assertEqual(tia._physical_name("F-DI150"), "F-DI150")


# --------------------------------------------------------------------------
# Fixture-gated: assert real extracted values + IR join
# --------------------------------------------------------------------------
class Imv1AmlFixtureTest(unittest.TestCase):
    def setUp(self):
        self.aml = _imv1_aml()
        if not self.aml.is_file():
            self.skipTest("IMV1 .aml fixture not present")

    def test_known_module_order_numbers_and_profinet(self):
        hw = tia_aml.parse_aml(str(self.aml))
        q100 = tia_aml.hardware_for_station(hw, "Q100-Cooling1/UV")
        # CPU 1512SP F-1 PN head module (the floor station's CPU)
        cpu = q100["Q100_QUERETARO1"]
        self.assertEqual(cpu["order_number"], "6ES7 512-1SK01-0AB0")
        self.assertEqual(cpu["type_name"], "CPU 1512SP F-1 PN")
        self.assertEqual(cpu["network_address"], "192.168.10.10")
        # an ET200SP I/O module
        fdi = q100["F-DI150"]
        self.assertEqual(fdi["order_number"], "6ES7 136-6BA00-0CA0")
        self.assertEqual(fdi["type_name"], "F-DI 8x24VDC HF")
        self.assertEqual(fdi["network_address"], "192.168.10.10")

    def test_no_invented_order_numbers(self):
        # every extracted order number is either "" or starts with the Siemens
        # "6ES7"/"6ES" / "6AG" family marker (and is never a TypeIdentifier path)
        hw = tia_aml.parse_aml(str(self.aml))
        for info in hw.values():
            order = info["order_number"]
            if order:
                self.assertFalse(order.startswith("System:"), order)
                self.assertTrue(order.startswith("6"), order)


class Imv1IrJoinTest(unittest.TestCase):
    def setUp(self):
        self.aml = _imv1_aml()
        self.io = _imv1_io_channels()
        if not (self.aml.is_file() and self.io.is_file()):
            self.skipTest("IMV1 .aml or IO_Channels.xml fixture not present")

    def test_catalog_and_network_address_populated(self):
        proj = plc_ir.build_tia_project(str(self.io), None, str(self.aml))
        by_name = {m.name: m for m in proj.io_mods}
        # F-DI150 -> its real order number + PROFINET
        self.assertEqual(by_name["F-DI150"].catalog, "6ES7 136-6BA00-0CA0")
        self.assertEqual(by_name["F-DI150"].network_address, "192.168.10.10")
        # DI10_11 -> standard ST DI order number
        self.assertEqual(by_name["DI10_11"].catalog, "6ES7 131-6BH00-0BA0")
        self.assertEqual(by_name["DI10_11"].network_address, "192.168.10.10")

    def test_split_module_both_halves_join(self):
        # the F-DQ1500 split into [DI] + [DO] both resolve to the physical module
        proj = plc_ir.build_tia_project(str(self.io), None, str(self.aml))
        halves = [m for m in proj.io_mods if m.name.startswith("F-DQ1500")]
        self.assertEqual(len(halves), 2)
        for m in halves:
            self.assertEqual(m.catalog, "6ES7 136-6DB00-0CA0")
            self.assertEqual(m.network_address, "192.168.10.10")

    def test_every_floor_module_gets_a_catalog(self):
        # all 7 IR modules of the floor station match the .aml -> none left blank
        proj = plc_ir.build_tia_project(str(self.io), None, str(self.aml))
        self.assertTrue(all(m.catalog for m in proj.io_mods))
        self.assertTrue(all(m.network_address for m in proj.io_mods))

    def test_without_aml_catalog_stays_blank(self):
        # never-invent regression: no .aml -> catalog "" / network_address None
        proj = plc_ir.build_tia_project(str(self.io))
        self.assertTrue(all(m.catalog == "" for m in proj.io_mods))
        self.assertTrue(all(m.network_address is None for m in proj.io_mods))

    def test_unmatched_module_degrades_to_blank(self):
        # an IR whose module names do NOT appear in the .aml must NOT be filled
        # with an invented order number — feed an .aml with a non-overlapping
        # station so the join finds nothing for these names.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "other.aml"
            # a valid .aml describing a DIFFERENT module set ("ZZ99")
            p.write_text(
                _SYNTHETIC_AML.replace("DI10_11", "ZZ99").replace("HeadA", "ZZHEAD")
                .replace("MASKED1", "ZZMASK"),
                encoding="utf-8",
            )
            proj = plc_ir.build_tia_project(str(self.io), None, str(p))
            by_name = {m.name: m for m in proj.io_mods}
            # F-DI150 is not in the other.aml -> stays blank, never invented
            self.assertEqual(by_name["F-DI150"].catalog, "")
            self.assertIsNone(by_name["F-DI150"].network_address)


if __name__ == "__main__":
    unittest.main()
