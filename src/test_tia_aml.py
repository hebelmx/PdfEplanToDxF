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
              <Attribute Name="PositionNumber"><Value>5</Value></Attribute>
              <InternalElement ID="m1c" Name="DI10_11">
                <Attribute Name="Address">
                  <RefSemantic CorrespondingAttributePath="OrderedListType" />
                  <Attribute Name="1">
                    <Attribute Name="StartAddress" AttributeDataType="xs:int"><Value>10</Value></Attribute>
                    <Attribute Name="Length" AttributeDataType="xs:int"><Value>16</Value></Attribute>
                    <Attribute Name="IoType" AttributeDataType="xs:string"><Value>Input</Value></Attribute>
                  </Attribute>
                </Attribute>
                <ExternalInterface ID="c0" Name="Channel_DI_0">
                  <Attribute Name="IoType" AttributeDataType="xs:string"><Value>Input</Value></Attribute>
                  <Attribute Name="Length" AttributeDataType="xs:int"><Value>1</Value></Attribute>
                </ExternalInterface>
                <ExternalInterface ID="c1" Name="Channel_DI_1">
                  <Attribute Name="IoType" AttributeDataType="xs:string"><Value>Input</Value></Attribute>
                  <Attribute Name="Length" AttributeDataType="xs:int"><Value>1</Value></Attribute>
                </ExternalInterface>
              </InternalElement>
            </InternalElement>
            <InternalElement ID="mf" Name="F-DI450">
              <Attribute Name="TypeName"><Value>F-DI 8x24VDC HF</Value></Attribute>
              <Attribute Name="TypeIdentifier"><Value>OrderNumber:6ES7 136-6BA00-0CA0</Value></Attribute>
              <Attribute Name="PositionNumber"><Value>2</Value></Attribute>
              <InternalElement ID="mfc" Name="F-DI450">
                <Attribute Name="Address">
                  <RefSemantic CorrespondingAttributePath="OrderedListType" />
                  <Attribute Name="1">
                    <Attribute Name="StartAddress" AttributeDataType="xs:int"><Value>450</Value></Attribute>
                    <Attribute Name="Length" AttributeDataType="xs:int"><Value>48</Value></Attribute>
                    <Attribute Name="IoType" AttributeDataType="xs:string"><Value>Input</Value></Attribute>
                  </Attribute>
                  <Attribute Name="2">
                    <Attribute Name="StartAddress" AttributeDataType="xs:int"><Value>450</Value></Attribute>
                    <Attribute Name="Length" AttributeDataType="xs:int"><Value>32</Value></Attribute>
                    <Attribute Name="IoType" AttributeDataType="xs:string"><Value>Output</Value></Attribute>
                  </Attribute>
                  <Attribute Name="3">
                    <Attribute Name="StartAddress" AttributeDataType="xs:int"><Value>NaN</Value></Attribute>
                    <Attribute Name="Length" AttributeDataType="xs:int"><Value>8</Value></Attribute>
                    <Attribute Name="IoType" AttributeDataType="xs:string"><Value>Input</Value></Attribute>
                  </Attribute>
                </Attribute>
                <ExternalInterface ID="fc0" Name="Channel_DI_0">
                  <Attribute Name="IoType" AttributeDataType="xs:string"><Value>Input</Value></Attribute>
                  <Attribute Name="Length" AttributeDataType="xs:int"><Value>1</Value></Attribute>
                </ExternalInterface>
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

    def test_mask_to_prefix(self):
        # N1 helper: dotted netmask -> CIDR prefix; odd/absent -> None (never /24)
        self.assertEqual(tia_aml._mask_to_prefix("255.255.255.0"), 24)
        self.assertEqual(tia_aml._mask_to_prefix("255.255.0.0"), 16)
        self.assertEqual(tia_aml._mask_to_prefix("255.255.255.255"), 32)
        self.assertEqual(tia_aml._mask_to_prefix("0.0.0.0"), 0)
        self.assertIsNone(tia_aml._mask_to_prefix(None))
        self.assertIsNone(tia_aml._mask_to_prefix(""))
        self.assertIsNone(tia_aml._mask_to_prefix("255.0.255.0"))  # non-contiguous
        self.assertIsNone(tia_aml._mask_to_prefix("255.255.255"))   # not 4 octets


class SyntheticAmlTest(unittest.TestCase):
    def _parse(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.aml"
            p.write_text(_SYNTHETIC_AML, encoding="utf-8")
            return tia_aml.parse_aml(str(p))

    def test_modules_extracted(self):
        hw = self._parse()
        # head + DI10_11 + F-DI450 + MASKED1 (the Port_1 non-module is skipped)
        self.assertEqual(
            sorted(m for (_st, m) in hw),
            ["DI10_11", "F-DI450", "HeadA", "MASKED1"],
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

    def test_position_number_captured_as_slot(self):
        hw = self._parse()
        self.assertEqual(hw[("StationA", "DI10_11")]["slot"], 5)

    def test_missing_position_number_yields_none_slot(self):
        # MASKED1 has no PositionNumber -> slot None (never invented)
        hw = self._parse()
        self.assertIsNone(hw[("StationA", "MASKED1")]["slot"])

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

    def test_hardware_for_unknown_station_yields_empty_no_contamination(self):
        # N3: a station-name MISMATCH must NOT merge sibling stations' modules
        # (that would bind another physical station's catalog/PROFINET to a
        # module). The map is empty => caller leaves catalog ""/addr None.
        hw = self._parse()
        st = tia_aml.hardware_for_station(hw, "does-not-exist")
        self.assertEqual(st, {})


class AddressRangesTest(unittest.TestCase):
    """_address_ranges + the parse_aml `addresses` field: anchored ONLY on
    `<Attribute Name="Address">`'s numbered children, multi-range in order,
    [] when absent, and non-numeric entries skipped (never invented)."""

    def _parse(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.aml"
            p.write_text(_SYNTHETIC_AML, encoding="utf-8")
            return tia_aml.parse_aml(str(p))

    def test_single_input_range(self):
        # DI10_11 declares ONE Input range: byte 10, 16 bits (16 channels)
        hw = self._parse()
        self.assertEqual(
            hw[("StationA", "DI10_11")]["addresses"],
            [("Input", 10, 16)],
        )

    def test_fmodule_two_ranges_in_order(self):
        # F-DI450 carries an Input range AND a PROFIsafe Output range, in order.
        # (a 3rd entry has a non-numeric StartAddress and must be skipped.)
        hw = self._parse()
        self.assertEqual(
            hw[("StationA", "F-DI450")]["addresses"],
            [("Input", 450, 48), ("Output", 450, 32)],
        )

    def test_non_numeric_start_address_entry_skipped(self):
        # the F-DI450 entry "3" (StartAddress NaN) is dropped, not raised on,
        # leaving exactly the two well-formed ranges.
        hw = self._parse()
        self.assertEqual(len(hw[("StationA", "F-DI450")]["addresses"]), 2)
        self.assertNotIn(8, [length for (_t, _s, length) in
                              hw[("StationA", "F-DI450")]["addresses"]])

    def test_module_without_address_block_yields_empty(self):
        # MASKED1 declares no Address block -> [] (never invented)
        hw = self._parse()
        self.assertEqual(hw[("StationA", "MASKED1")]["addresses"], [])

    def test_head_module_without_address_block_yields_empty(self):
        hw = self._parse()
        self.assertEqual(hw[("StationA", "HeadA")]["addresses"], [])

    def test_channel_externalinterface_not_picked_up(self):
        # DI10_11 has per-channel Channel_* ExternalInterface elements that ALSO
        # carry IoType=Input/Length=1 — those must NOT appear as address ranges.
        # The module has 2 channels but only ONE real address range.
        hw = self._parse()
        addrs = hw[("StationA", "DI10_11")]["addresses"]
        self.assertEqual(len(addrs), 1)
        self.assertEqual(addrs[0], ("Input", 10, 16))
        # the channel Length=1 entries are absent
        self.assertNotIn(1, [length for (_t, _s, length) in addrs])

    def test_missing_iotype_kept_with_blank(self):
        # a well-formed range whose IoType is absent keeps the entry, IoType ""
        tiny = (
            '<?xml version="1.0" encoding="utf-8"?><CAEXFile><InstanceHierarchy Name="ih">'
            '<InternalElement Name="P"><InternalElement Name="dev">'
            '<Attribute Name="TypeIdentifier"><Value>System:Device.ET200SP</Value></Attribute>'
            '<InternalElement Name="Rack_0">'
            '<Attribute Name="TypeIdentifier"><Value>System:Rack.ET200SP</Value></Attribute>'
            '<InternalElement Name="MX"><Attribute Name="TypeName"><Value>X</Value></Attribute>'
            '<InternalElement Name="MX"><Attribute Name="Address">'
            '<Attribute Name="1">'
            '<Attribute Name="StartAddress"><Value>20</Value></Attribute>'
            '<Attribute Name="Length"><Value>8</Value></Attribute>'
            '</Attribute></Attribute></InternalElement></InternalElement>'
            '</InternalElement></InternalElement></InternalElement></InstanceHierarchy></CAEXFile>'
        )
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.aml"
            p.write_text(tiny, encoding="utf-8")
            hw = tia_aml.parse_aml(str(p))
        self.assertEqual(hw[("dev", "MX")]["addresses"], [("", 20, 8)])

    def test_helper_directly_on_module_element(self):
        # _address_ranges reads numbered children of an Address block, in order
        xml = (
            '<InternalElement Name="m"><InternalElement Name="m">'
            '<Attribute Name="Address">'
            '<Attribute Name="1"><Attribute Name="StartAddress"><Value>0</Value></Attribute>'
            '<Attribute Name="Length"><Value>16</Value></Attribute>'
            '<Attribute Name="IoType"><Value>Output</Value></Attribute></Attribute>'
            '</Attribute></InternalElement></InternalElement>'
        )
        el = tia_aml.ET.fromstring(xml)
        self.assertEqual(tia_aml._address_ranges(el), [("Output", 0, 16)])


class ProfinetNodesSyntheticTest(unittest.TestCase):
    """profinet_nodes resolves (ip, name, type) by climbing to the TypeName
    ancestor, sorts numerically by IP, and never invents missing fields."""

    def _nodes(self, text=_SYNTHETIC_AML):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.aml"
            p.write_text(text, encoding="utf-8")
            return tia_aml.profinet_nodes(str(p))

    def test_single_node_resolved_to_owning_device(self):
        # the address sits under "PROFINET interface_1 > E1"; the resolver climbs
        # to the first ancestor with a TypeName (the head module). The 5-tuple
        # now carries the REAL SubnetMask sibling and the DeviceItemType=CPU flag.
        nodes = self._nodes()
        # HeadA carries DeviceItemType=CPU and no SubnetMask sibling => (mask None,
        # controller True). The mask is None (never reconstructed from the IP).
        self.assertEqual(
            nodes,
            [("192.168.10.55", "HeadA", "CPU 1512SP F-1 PN", None, True)],
        )

    def test_numeric_ip_sort(self):
        # two addresses out of dotted-decimal order must sort numerically, not
        # lexically (.10 before .9 lexically, but .9 < .10 numerically).
        two = _SYNTHETIC_AML.replace(
            '<Attribute Name="NetworkAddress"><Value>192.168.10.55</Value></Attribute>',
            '<Attribute Name="NetworkAddress"><Value>192.168.10.10</Value></Attribute>'
            '<Attribute Name="NetworkAddress"><Value>192.168.10.9</Value></Attribute>',
        )
        nodes = self._nodes(two)
        ips = [n[0] for n in nodes]
        self.assertEqual(ips, ["192.168.10.9", "192.168.10.10"])

    def test_no_typename_keeps_named_ancestor_blank_type(self):
        # a node whose ancestors carry NO TypeName keeps the first named
        # InternalElement and leaves the type "" — never invented.
        text = _SYNTHETIC_AML.replace(
            '<Attribute Name="TypeName"><Value>CPU 1512SP F-1 PN</Value></Attribute>',
            "",
        )
        nodes = self._nodes(text)
        self.assertEqual(len(nodes), 1)
        ip, name, typ, mask, ctrl = nodes[0]
        self.assertEqual(typ, "")
        self.assertTrue(name)  # falls back to a named ancestor, not fabricated
        self.assertFalse(ctrl)  # no TypeName ancestor => DeviceItemType unseen

    def test_real_subnet_mask_read_from_sibling(self):
        # N1: a SubnetMask sibling Attribute under the node element is read as the
        # 4th field — NEVER reconstructed from the host IP.
        text = _SYNTHETIC_AML.replace(
            '<Attribute Name="NetworkAddress"><Value>192.168.10.55</Value></Attribute>',
            '<Attribute Name="NetworkAddress"><Value>192.168.10.55</Value></Attribute>'
            '<Attribute Name="SubnetMask"><Value>255.255.255.0</Value></Attribute>',
        )
        nodes = self._nodes(text)
        self.assertEqual(nodes[0][3], "255.255.255.0")

    def test_controller_flag_from_device_item_type(self):
        # N2: DeviceItemType=CPU on the owning module sets is_controller (real
        # provenance, not a host-IP heuristic).
        nodes = self._nodes()
        self.assertTrue(nodes[0][4])  # HeadA carries DeviceItemType=CPU
        # a non-CPU owner does not flag controller
        text = _SYNTHETIC_AML.replace(
            "<Attribute Name=\"DeviceItemType\"><Value>CPU</Value></Attribute>",
            "<Attribute Name=\"DeviceItemType\"><Value>HeadModule</Value></Attribute>",
        )
        self.assertFalse(self._nodes(text)[0][4])


class ProfinetNodesFixtureTest(unittest.TestCase):
    def setUp(self):
        self.aml = _imv1_aml()
        if not self.aml.is_file():
            self.skipTest("IMV1 .aml fixture not present")

    def test_thirtyfive_nodes_sorted_with_known_endpoints(self):
        nodes = tia_aml.profinet_nodes(str(self.aml))
        self.assertEqual(len(nodes), 35)
        # numerically sorted, .10 first .95 last; 5-tuple w/ real mask + the
        # DeviceItemType=CPU controller flag
        self.assertEqual(nodes[0], ("192.168.10.10", "Q100_QUERETARO1",
                                    "CPU 1512SP F-1 PN", "255.255.255.0", True))
        self.assertEqual(nodes[-1], ("192.168.10.95", "PLC_1",
                                     "CPU 1214C AC/DC/Rly", "255.255.255.0", True))
        ips = [n[0] for n in nodes]
        self.assertEqual(ips, sorted(ips, key=tia_aml._ip_sort_key))

    def test_all_nodes_carry_real_uniform_mask(self):
        # N1: every node carries the REAL 255.255.255.0 from the .aml SubnetMask
        nodes = tia_aml.profinet_nodes(str(self.aml))
        self.assertEqual({n[3] for n in nodes}, {"255.255.255.0"})

    def test_sample_nodes_match(self):
        nodes = {n[0]: (n[1], n[2]) for n in tia_aml.profinet_nodes(str(self.aml))}
        self.assertEqual(nodes["192.168.10.12"], ("EV_UV_Q100", "EX260 SPN 3/4"))
        self.assertEqual(nodes["192.168.10.20"], ("Q200_Q1", "IM 155-6 PN ST"))

    def test_ir_carries_network_nodes_with_aml(self):
        proj = plc_ir.build_tia_project(str(self.aml.parent /
                "IMV1_QRO001_08AGO21_V15_IO_Channels.xml"), None, str(self.aml))
        self.assertEqual(len(proj.network_nodes), 35)

    def test_ir_network_nodes_empty_without_aml(self):
        proj = plc_ir.build_tia_project(str(self.aml.parent /
                "IMV1_QRO001_08AGO21_V15_IO_Channels.xml"))
        self.assertEqual(proj.network_nodes, [])


class PhysicalNameTest(unittest.TestCase):
    def test_strips_split_suffix(self):
        self.assertEqual(tia._physical_name("F-DQ1500 [DI]"), "F-DQ1500")
        self.assertEqual(tia._physical_name("F-DQ1500 [DO]"), "F-DQ1500")

    def test_passes_unsuffixed_through(self):
        self.assertEqual(tia._physical_name("F-DI150"), "F-DI150")


# A tiny IO_Channels.xml whose Station Name does NOT match the synthetic .aml's
# "StationA", but whose module Name ("DI10_11") DOES exist in the .aml. The N3
# guarantee: the .aml join must NOT bind that sibling station's hardware.
_MISMATCH_IO = """<?xml version="1.0" encoding="UTF-8"?>
<Stations>
  <Station Name="OtherStation">
    <Rack Name="Rack_0">
      <Module Name="DI10_11">
        <IOChannel Number="0"><Address>%I10.0</Address><Tag>some_tag</Tag></IOChannel>
        <IOChannel Number="1"><Address>%I10.1</Address><Tag></Tag></IOChannel>
      </Module>
    </Rack>
  </Station>
</Stations>
"""


class N3CrossStationContaminationTest(unittest.TestCase):
    """N3: when the IO_Channels station name does NOT match any .aml station, the
    hardware join must leave catalog ''/network_address None (never bind a
    DIFFERENT physical station's order#/PROFINET to the module)."""

    def test_mismatched_station_yields_no_contamination(self):
        with tempfile.TemporaryDirectory() as d:
            aml = Path(d) / "plant.aml"
            aml.write_text(_SYNTHETIC_AML, encoding="utf-8")
            io = Path(d) / "x_IO_Channels.xml"
            io.write_text(_MISMATCH_IO, encoding="utf-8")
            _st, _mods, io_mods, _pts, _sk = tia.build_modules_and_points(
                str(io), {}, str(aml))
        by_name = {m.name: m for m in io_mods}
        self.assertIn("DI10_11", by_name)
        # the .aml HAS a DI10_11 (under StationA) with order# 6ES7 131-6BH00-0BA0
        # and addr 192.168.10.55 — but the station mismatch must NOT bind it.
        self.assertEqual(by_name["DI10_11"].catalog, "")
        self.assertIsNone(getattr(by_name["DI10_11"], "network_address", None))
        self.assertIsNone(by_name["DI10_11"].slot)

    def test_matching_station_still_joins(self):
        # control: the SAME module under the MATCHING station name DOES join.
        matched_io = _MISMATCH_IO.replace('Name="OtherStation"', 'Name="StationA"')
        with tempfile.TemporaryDirectory() as d:
            aml = Path(d) / "plant.aml"
            aml.write_text(_SYNTHETIC_AML, encoding="utf-8")
            io = Path(d) / "x_IO_Channels.xml"
            io.write_text(matched_io, encoding="utf-8")
            _st, _mods, io_mods, _pts, _sk = tia.build_modules_and_points(
                str(io), {}, str(aml))
        by_name = {m.name: m for m in io_mods}
        self.assertEqual(by_name["DI10_11"].catalog, "6ES7 131-6BH00-0BA0")
        self.assertEqual(by_name["DI10_11"].network_address, "192.168.10.55")


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

    def test_known_module_address_ranges(self):
        # ground truth from the real .aml:
        #   F-DI150 -> Input byte 150 len 48 bits + PROFIsafe Output 150 len 32
        #   DI10_11 -> single Input byte 10 len 16 bits (16 DI channels)
        #   DQ10_11 -> single Output byte 10 len 16 bits
        #   F-DQ1500 -> Input 1500 len 40 + Output 1500 len 40
        #   the CPU head + END server module declare NO Address block -> []
        hw = tia_aml.parse_aml(str(self.aml))
        q100 = tia_aml.hardware_for_station(hw, "Q100-Cooling1/UV")
        self.assertEqual(q100["F-DI150"]["addresses"],
                         [("Input", 150, 48), ("Output", 150, 32)])
        self.assertEqual(q100["DI10_11"]["addresses"], [("Input", 10, 16)])
        self.assertEqual(q100["DQ10_11"]["addresses"], [("Output", 10, 16)])
        self.assertEqual(q100["F-DQ1500"]["addresses"],
                         [("Input", 1500, 40), ("Output", 1500, 40)])
        self.assertEqual(q100["Q100_QUERETARO1"]["addresses"], [])
        self.assertEqual(q100["END_Q100"]["addresses"], [])

    def test_address_ranges_never_invented(self):
        # every range across the plant has integer start/length and an IoType
        # that is "" or a real Input/Output (never a fabricated value)
        hw = tia_aml.parse_aml(str(self.aml))
        for info in hw.values():
            for io_type, start, length in info["addresses"]:
                self.assertIsInstance(start, int)
                self.assertIsInstance(length, int)
                self.assertIn(io_type, ("", "Input", "Output"))

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

    def test_slots_populated_from_position_number(self):
        # the .aml PositionNumber fills Module.slot (fixes "Slot None"); the 6
        # physical Q100 modules carry slots 2..7, split halves share slot 4.
        proj = plc_ir.build_tia_project(str(self.io), None, str(self.aml))
        by_name = {m.name: m.slot for m in proj.io_mods}
        self.assertEqual(by_name["F-DI150"], 2)
        self.assertEqual(by_name["F-DI156"], 3)
        self.assertEqual(by_name["DI10_11"], 5)
        self.assertEqual(by_name["DI12_13"], 6)
        self.assertEqual(by_name["DQ10_11"], 7)
        # both split halves of F-DQ1500 share the physical slot 4
        self.assertEqual(by_name["F-DQ1500 [DO]"], 4)
        self.assertEqual(by_name["F-DQ1500 [DI]"], 4)
        self.assertTrue(all(m.slot is not None for m in proj.io_mods))

    def test_without_aml_catalog_stays_blank(self):
        # never-invent regression: no .aml -> catalog "" / network_address None /
        # slot None
        proj = plc_ir.build_tia_project(str(self.io))
        self.assertTrue(all(m.catalog == "" for m in proj.io_mods))
        self.assertTrue(all(m.network_address is None for m in proj.io_mods))
        self.assertTrue(all(m.slot is None for m in proj.io_mods))

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
