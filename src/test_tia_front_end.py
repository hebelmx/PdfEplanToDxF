#!/usr/bin/env python3
"""Unit tests for the Siemens TIA front-end (tia_front_end + build_tia_project).

Stdlib-only (unittest). Run from src/:
    python -m unittest test_tia_front_end
or via discovery:
    python -m unittest discover -p "test_*.py"

The fixture-floor / integration / round-trip tests are gated on the (gitignored)
IMV1 IO_Channels.xml fixture existing — but it MUST be present in the dev env.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tia_front_end as tia
import plc_ir
from logix_to_eplan_csv import eplan_address


def _imv1_io_channels() -> Path:
    """Resolve the IMV1 IO_Channels.xml fixture (gitignored). Returns the
    preferred path even when absent so the caller's skip guard fires cleanly."""
    root = Path(__file__).resolve().parent.parent / "Fixtures" / "Siemens" / "TiaPortal"
    return root / "IMV1_QRO001_08AGO21_V15_IO_Channels.xml"


def _imv1_xlsx() -> Path:
    root = Path(__file__).resolve().parent.parent / "Fixtures" / "Siemens" / "TiaPortal"
    return root / "PLCTagsS71200.xlsx"


# --------------------------------------------------------------------------
# Pure-helper tests: address parsing
# --------------------------------------------------------------------------
class ParseAddressTest(unittest.TestCase):
    def test_digital_input(self):
        self.assertEqual(
            tia.parse_address("%I150.0"),
            {"direction": "I", "analog": False, "byte": 150, "bit": 0},
        )

    def test_digital_input_high_bit(self):
        self.assertEqual(
            tia.parse_address("%I151.7"),
            {"direction": "I", "analog": False, "byte": 151, "bit": 7},
        )

    def test_digital_output(self):
        self.assertEqual(
            tia.parse_address("%Q1500.0"),
            {"direction": "O", "analog": False, "byte": 1500, "bit": 0},
        )

    def test_analog_input_word(self):
        self.assertEqual(
            tia.parse_address("%IW64"),
            {"direction": "I", "analog": True, "word": 64},
        )

    def test_analog_output_word(self):
        self.assertEqual(
            tia.parse_address("%QW128"),
            {"direction": "O", "analog": True, "word": 128},
        )

    def test_tolerates_missing_percent_and_case(self):
        self.assertEqual(
            tia.parse_address("q10.3"),
            {"direction": "O", "analog": False, "byte": 10, "bit": 3},
        )

    def test_double_word_is_not_an_io_channel(self):
        # %ID1000 (double-word) is not a bit/word I/O channel -> None
        self.assertIsNone(tia.parse_address("%ID1000"))

    def test_digital_without_bit_is_none(self):
        self.assertIsNone(tia.parse_address("%I150"))

    def test_word_with_bit_is_malformed(self):
        self.assertIsNone(tia.parse_address("%IW64.0"))

    def test_empty_is_none(self):
        self.assertIsNone(tia.parse_address(""))
        self.assertIsNone(tia.parse_address(None))

    def test_merker_address_is_none(self):
        self.assertIsNone(tia.parse_address("%M10.0"))


# --------------------------------------------------------------------------
# Pure-helper tests: kind inference + spare detection
# --------------------------------------------------------------------------
class KindAndSpareTest(unittest.TestCase):
    def test_kind_di(self):
        self.assertEqual(tia.infer_kind(tia.parse_address("%I10.0")), "DI")

    def test_kind_do(self):
        self.assertEqual(tia.infer_kind(tia.parse_address("%Q10.0")), "DO")

    def test_kind_ai(self):
        self.assertEqual(tia.infer_kind(tia.parse_address("%IW64")), "AI")

    def test_kind_ao(self):
        self.assertEqual(tia.infer_kind(tia.parse_address("%QW64")), "AO")

    def test_spare_empty(self):
        self.assertTrue(tia.is_spare(""))

    def test_spare_whitespace(self):
        self.assertTrue(tia.is_spare("   \n   "))

    def test_spare_none(self):
        self.assertTrue(tia.is_spare(None))

    def test_not_spare_when_tagged(self):
        self.assertFalse(tia.is_spare("fcuv_door1a"))


class NonDeviceSignalTest(unittest.TestCase):
    """_is_nondevice_signal suppresses a device symbol for supply-monitor and
    permit channels ONLY (Abel 2026-06-17) — never a real field device."""

    def test_supply_monitor_by_tag_prefix(self):
        self.assertTrue(tia._is_nondevice_signal("VS_buv_ema", "Vsupply Emergency Stop"))

    def test_supply_monitor_by_description(self):
        self.assertTrue(tia._is_nondevice_signal("anything", "Vsupply UV Door 1 Open"))

    def test_permit_signal(self):
        self.assertTrue(tia._is_nondevice_signal("buv_p2open", "Permission to Open UV Door"))

    def test_real_device_kept(self):
        # a genuine door limit switch / e-stop / light is NOT suppressed
        self.assertFalse(tia._is_nondevice_signal("fcuv_door1a", "UV Door 1 Open"))
        self.assertFalse(tia._is_nondevice_signal("buv_ema", "Emergency Stop"))

    def test_permission_word_midphrase_is_kept(self):
        # the critical non-suppression: a real pilot light whose description
        # merely CONTAINS 'permission' (not at the start) must keep its symbol
        self.assertFalse(
            tia._is_nondevice_signal("uv_slpermission", "Light Signal Permission Door"))


# --------------------------------------------------------------------------
# Pure-helper tests: xlsx shared-string resolution (synthetic, no fixture)
# --------------------------------------------------------------------------
class XlsxSharedStringTest(unittest.TestCase):
    def _make_xlsx(self, path: Path):
        import zipfile

        ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        # Header uses shared strings; one data row mixes a shared-string Name
        # and an inline-string Comment to exercise both code paths.
        shared = (
            f'<?xml version="1.0"?><sst xmlns="{ns}" uniqueCount="6">'
            "<si><t>Name</t></si>"
            "<si><t>Logical Address</t></si>"
            "<si><t>Comment</t></si>"
            "<si><t>my_tag</t></si>"
            "<si><t>%I10.0</t></si>"
            "<si><r><t>Door </t></r><r><t>switch</t></r></si>"  # run-split string
            "</sst>"
        )
        sheet = (
            f'<?xml version="1.0"?><worksheet xmlns="{ns}"><sheetData>'
            '<row r="1">'
            '<c r="A1" t="s"><v>0</v></c>'
            '<c r="B1" t="s"><v>1</v></c>'
            '<c r="C1" t="s"><v>2</v></c>'
            "</row>"
            '<row r="2">'
            '<c r="A2" t="s"><v>3</v></c>'
            '<c r="B2" t="s"><v>4</v></c>'
            '<c r="C2" t="inlineStr"><is><t>Inline comment</t></is></c>'
            "</row>"
            '<row r="3">'
            '<c r="A3" t="s"><v>3</v></c>'  # reuse my_tag name, shared-string comment
            '<c r="B3" t="s"><v>4</v></c>'
            '<c r="C3" t="s"><v>5</v></c>'
            "</row>"
            "</sheetData></worksheet>"
        )
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("xl/sharedStrings.xml", shared)
            z.writestr("xl/worksheets/sheet.xml", sheet)
            z.writestr("xl/workbook.xml", "<workbook/>")

    def test_resolves_shared_and_inline_strings(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "tags.xlsx"
            self._make_xlsx(p)
            table = tia.load_tag_table(str(p))
            self.assertIn("my_tag", table)
            # last row wins; comment is a run-split shared string concatenated
            self.assertEqual(table["my_tag"]["address"], "%I10.0")
            self.assertEqual(table["my_tag"]["comment"], "Door switch")

    def test_missing_file_returns_empty(self):
        self.assertEqual(tia.load_tag_table("does_not_exist.xlsx"), {})


# --------------------------------------------------------------------------
# Fixture-gated tests (IMV1 IO_Channels.xml)
# --------------------------------------------------------------------------
class TiaFixtureTest(unittest.TestCase):
    def setUp(self):
        self.fixture = _imv1_io_channels()
        if not self.fixture.is_file():
            self.skipTest("IMV1 IO_Channels.xml fixture not present")

    def test_fixture_floor_numbers(self):
        """The invariant floor: 88 channels / 48 tagged / 40 spare / 6 physical
        modules. Counted independent of the mixed-module split decision: physical
        <Module> elements for the 6, <IOChannel> for the channels."""
        import xml.etree.ElementTree as ET

        root = ET.parse(self.fixture).getroot()
        phys_modules = list(root.iter("Module"))
        channels = list(root.iter("IOChannel"))
        tagged = 0
        spare = 0
        for ch in channels:
            tag_el = ch.find("Tag")
            txt = (tag_el.text or "") if tag_el is not None else ""
            if tia.is_spare(txt):
                spare += 1
            else:
                tagged += 1
        self.assertEqual(len(phys_modules), 6, "physical <Module> count")
        self.assertEqual(len(channels), 88, "total <IOChannel> count")
        self.assertEqual(tagged, 48, "tagged channels")
        self.assertEqual(spare, 40, "spare channels")

    def test_build_tia_project_returns_siemens_ir(self):
        proj = plc_ir.build_tia_project(str(self.fixture))
        self.assertIsInstance(proj, plc_ir.PlcProject)
        self.assertEqual(proj.source_vendor, "siemens")
        # name = station name, never invented
        self.assertEqual(proj.name, "Q100-Cooling1/UV")

    def test_points_count_matches_tagged_channels(self):
        proj = plc_ir.build_tia_project(str(self.fixture))
        # 48 tagged channels -> 48 bound IoPoints
        self.assertEqual(len(proj.points), 48)
        # 40 spares land in skipped with reason 'spare'
        spares = [s for s in proj.skipped if s[2] == "spare"]
        self.assertEqual(len(spares), 40)

    def test_mixed_module_split_into_two_ir_modules(self):
        """F-DQ1500 carries %Q1500.x outputs AND %I1500.x inputs; it splits into
        two IR Module entries (a DI part + a DO part) sharing the physical name."""
        proj = plc_ir.build_tia_project(str(self.fixture))
        fdq = [m for m in proj.io_mods if m.name.startswith("F-DQ1500")]
        self.assertEqual(len(fdq), 2, "F-DQ1500 split into 2 IR modules")
        kinds = sorted(m.kind for m in fdq)
        self.assertEqual(kinds, ["DI", "DO"])
        # all other physical modules stay single
        names = [m.name for m in proj.io_mods]
        self.assertIn("F-DQ1500 [DI]", names)
        self.assertIn("F-DQ1500 [DO]", names)
        # IR module count = 5 single + 2 split = 7
        self.assertEqual(len(proj.io_mods), 7)

    def test_round_trip_addresses(self):
        """Parsed real address == address the renderer reproduces, for several
        representative tagged points including %I150.0, %I151.7, %Q1500.0, %Q11.3.
        (%Q11.7 is intentionally NOT used here — it is an empty-Tag SPARE in the
        fixture, so it is correctly excluded from the bound points.)"""
        proj = plc_ir.build_tia_project(str(self.fixture))
        by_addr = {p.logix_address: p for p in proj.points}
        for raw in ("%I150.0", "%I151.7", "%Q1500.0", "%Q11.3"):
            self.assertIn(raw, by_addr, f"{raw} expected among tagged points")
            p = by_addr[raw]
            rendered = eplan_address(p.module, p.direction, p.index, p.analog)
            # eplan_address yields e.g. 'I150.0' / 'Q11.7' (no % prefix)
            self.assertEqual("%" + rendered, raw)

    def test_every_point_round_trips(self):
        """Robust check: EVERY bound point's rendered address equals its raw
        Siemens address (modulo the leading %)."""
        proj = plc_ir.build_tia_project(str(self.fixture))
        for p in proj.points:
            rendered = eplan_address(p.module, p.direction, p.index, p.analog)
            self.assertEqual("%" + rendered, p.logix_address)

    def test_descriptions_fall_back_to_empty(self):
        """The IMV1 1200 xlsx tag table doesn't overlap these IO tags and has no
        comments; descriptions must fall back to '' (NEVER invented)."""
        xlsx = _imv1_xlsx()
        tags_path = str(xlsx) if xlsx.is_file() else None
        proj = plc_ir.build_tia_project(str(self.fixture), tags_path)
        self.assertTrue(all(p.description == "" for p in proj.points))


def _imv1_aml() -> Path:
    root = Path(__file__).resolve().parent.parent / "Fixtures" / "Siemens" / "TiaPortal"
    return root / "IMV1_QRO001_08AGO21_V15.aml"


# --------------------------------------------------------------------------
# E6 distributed-I/O: PURE unit tests on inline parse_aml-shaped dicts
# --------------------------------------------------------------------------
class DistributedStationsPureTest(unittest.TestCase):
    """build_distributed_stations driven by hand-built parse_aml-shaped hw and
    inline tag tables — no real fixtures. We monkeypatch tia_aml.parse_aml /
    profinet_nodes so the pure synthesis + join logic is exercised in isolation."""

    def _run(self, hw, tag_tables, nodes=None):
        import tia_aml
        orig_parse = tia_aml.parse_aml
        orig_nodes = tia_aml.profinet_nodes
        orig_hw_for = tia_aml.hardware_for_station
        tia_aml.parse_aml = lambda _p: hw
        tia_aml.profinet_nodes = lambda _p: (nodes or [])
        # real hardware_for_station works on the dict shape; keep it
        try:
            return tia.build_distributed_stations("dummy.aml", tag_tables)
        finally:
            tia_aml.parse_aml = orig_parse
            tia_aml.profinet_nodes = orig_nodes
            tia_aml.hardware_for_station = orig_hw_for

    @staticmethod
    def _counts(station):
        mapped = len(station["points"])
        reserva = sum(1 for s in station["skipped"] if s[0] == "RESERVA")
        return mapped + reserva, mapped, reserva

    def test_standard_di16_12_tags(self):
        """A standard DI 16x with 12 tags -> 12 mapped + 4 RESERVA."""
        hw = {
            ("ST", "DI0_1"): {
                "order_number": "6ES7-X", "type_name": "DI 16x24VDC ST",
                "network_address": "10.0.0.1", "channels": 16,
                "device_item_type": "", "slot": 2,
                "addresses": [("Input", 0, 16)],
            },
        }
        tags = {f"t{i}": {"address": f"%I{i // 8}.{i % 8}", "comment": f"c{i}"}
                for i in range(12)}
        sts = self._run(hw, {"A": tags})
        self.assertEqual(len(sts), 1)
        ch, m, r = self._counts(sts[0])
        self.assertEqual((ch, m, r), (16, 12, 4))

    def test_fdi_8x_value_and_status_all_mapped(self):
        """F-DI 8x -> 16 channels; 8 device tags + 8 VS_ status tags all mapped,
        and the VS_ points get no_symbol=True."""
        hw = {
            ("ST", "F-DI150"): {
                "order_number": "6ES7-F", "type_name": "F-DI 8x24VDC HF",
                "network_address": "10.0.0.1", "channels": 8,
                "device_item_type": "", "slot": 2,
                "addresses": [("Input", 150, 48), ("Output", 150, 32)],
            },
        }
        tags = {}
        for i in range(8):
            tags[f"dev{i}"] = {"address": f"%I150.{i}", "comment": f"Device {i}"}
            tags[f"VS_dev{i}"] = {"address": f"%I151.{i}", "comment": f"Vsupply {i}"}
        sts = self._run(hw, {"A": tags})
        ch, m, r = self._counts(sts[0])
        self.assertEqual((ch, m, r), (16, 16, 0))
        vs = [p for p in sts[0]["points"] if p.tag.startswith("VS_")]
        self.assertEqual(len(vs), 8)
        self.assertTrue(all(p.no_symbol for p in vs))

    def test_fdi_8x_partial(self):
        """F-DI 8x with only 4 of 16 channels tagged -> 4 mapped + 12 RESERVA."""
        hw = {
            ("ST", "F-DI156"): {
                "order_number": "", "type_name": "F-DI 8x24VDC HF",
                "network_address": None, "channels": 8,
                "device_item_type": "", "slot": 3,
                "addresses": [("Input", 156, 48), ("Output", 156, 32)],
            },
        }
        tags = {
            "a": {"address": "%I156.0", "comment": ""},
            "b": {"address": "%I156.1", "comment": ""},
            "va": {"address": "%I157.0", "comment": ""},
            "vb": {"address": "%I157.1", "comment": ""},
        }
        sts = self._run(hw, {"A": tags})
        ch, m, r = self._counts(sts[0])
        self.assertEqual((ch, m, r), (16, 4, 12))

    def test_fdq_4x_split_do_di(self):
        """F-DQ 4x -> split DO/DI: DO 3 mapped + 1 RESERVA, DI 0 mapped + 4
        RESERVA => physical module 8/3/5."""
        hw = {
            ("ST", "F-DQ1500"): {
                "order_number": "", "type_name": "F-DQ 4x24VDC/2A PM HF",
                "network_address": None, "channels": 4,
                "device_item_type": "", "slot": 4,
                "addresses": [("Input", 1500, 40), ("Output", 1500, 40)],
            },
        }
        tags = {
            "o0": {"address": "%Q1500.0", "comment": "Out 0"},
            "o1": {"address": "%Q1500.1", "comment": "Out 1"},
            "o2": {"address": "%Q1500.2", "comment": "Out 2"},
        }
        sts = self._run(hw, {"A": tags})
        st = sts[0]
        ch, m, r = self._counts(st)
        self.assertEqual((ch, m, r), (8, 3, 5))
        names = sorted(mod.name for mod in st["io_mods"])
        self.assertEqual(names, ["F-DQ1500 [DI]", "F-DQ1500 [DO]"])
        do = [mod for mod in st["io_mods"] if mod.name == "F-DQ1500 [DO]"][0]
        di = [mod for mod in st["io_mods"] if mod.name == "F-DQ1500 [DI]"][0]
        do_pts = sum(1 for p in st["points"] if p.module is do)
        di_pts = sum(1 for p in st["points"] if p.module is di)
        self.assertEqual((do_pts, do.points - do_pts), (3, 1))
        self.assertEqual((di_pts, di.points - di_pts), (0, 4))

    def test_analog_ai_4x_words(self):
        """An analog AI 4x -> 4 word channels (%IW…)."""
        hw = {
            ("ST", "AI4"): {
                "order_number": "", "type_name": "AI 4xU/I/RTD/TC ST",
                "network_address": None, "channels": 4,
                "device_item_type": "", "slot": 2,
                "addresses": [("Input", 64, 64)],  # 64 bits / 16 = 4 words
            },
        }
        tags = {
            "a0": {"address": "%IW64", "comment": "A0"},
            "a1": {"address": "%IW66", "comment": "A1"},
        }
        sts = self._run(hw, {"A": tags})
        st = sts[0]
        ch, m, r = self._counts(st)
        self.assertEqual((ch, m, r), (4, 2, 2))
        self.assertTrue(all(p.analog for p in st["points"]))
        addrs = sorted(p.logix_address for p in st["points"])
        self.assertEqual(addrs, ["%IW64", "%IW66"])

    def test_analog_module_named_without_ai_aq_prefix(self):
        """An analog module whose type_name does NOT start with AI/AQ — the real
        'SM 1232 AQ2' analog-output card — is still classified analog by its
        word-structured Length (16*channels), NOT misread as digital. Regression:
        the prefix-only test synthesized %Q96.0/.1 and dropped the real %QW96 tag.
        """
        hw = {
            ("ST", "AQ 2x14BIT_1"): {
                "order_number": "", "type_name": "SM 1232 AQ2",
                "network_address": None, "channels": 2,
                "device_item_type": "", "slot": 3,
                "addresses": [("Output", 96, 32)],  # 32 bits / 16 = 2 words
            },
        }
        tags = {"ao0": {"address": "%QW96", "comment": "Setpoint"}}
        sts = self._run(hw, {"A": tags})
        st = sts[0]
        ch, m, r = self._counts(st)
        self.assertEqual((ch, m, r), (2, 1, 1))
        self.assertTrue(all(p.analog for p in st["points"]))
        self.assertEqual(st["points"][0].logix_address, "%QW96")
        self.assertEqual(st["io_mods"][0].kind, "AO")

    def test_ownership_picks_higher_coverage(self):
        """Owner selection picks the table covering MORE of the station's
        synthesized channel addresses."""
        hw = {
            ("ST", "DI0_1"): {
                "order_number": "", "type_name": "DI 16x24VDC ST",
                "network_address": None, "channels": 16,
                "device_item_type": "", "slot": 2,
                "addresses": [("Input", 0, 16)],
            },
        }
        good = {f"t{i}": {"address": f"%I{i // 8}.{i % 8}", "comment": ""}
                for i in range(10)}
        poor = {"x": {"address": "%I99.0", "comment": ""}}
        sts = self._run(hw, {"GOOD": good, "POOR": poor})
        self.assertEqual(sts[0]["owning_plc_label"], "GOOD")
        self.assertFalse(sts[0]["ambiguous_owner"])

    def test_cpu_hsc_range_no_synthesized_spares(self):
        """A CPU 'PLC_1' with an Input 1000/32 HSC range yields NO synthesized
        digital spares from that range — only the standard onboard ranges +
        real tags. Here only an onboard %I0.0 tag exists; the 1000-range is
        ignored entirely (no RESERVA explosion from %ID double-words)."""
        hw = {
            ("ST", "PLC_1"): {
                "order_number": "", "type_name": "CPU 1214C AC/DC/Rly",
                "network_address": "10.0.0.95", "channels": 26,
                "device_item_type": "CPU", "slot": 1,
                "addresses": [
                    ("Input", 0, 16), ("Output", 0, 16), ("Input", 64, 32),
                    ("Input", 1000, 32), ("Output", 1000, 16),
                ],
            },
        }
        tags = {"on0": {"address": "%I0.0", "comment": "onboard"}}
        sts = self._run(hw, {"A": tags})
        st = sts[0]
        # onboard synthesized: 16 DI + 16 DO + 2 AI = 34 channels; 1 mapped.
        ch, m, r = self._counts(st)
        self.assertEqual(ch, 34)
        self.assertEqual(m, 1)
        # NONE of the skipped spares come from an HSC %ID/%QD address
        for kind, raw, reason in st["skipped"]:
            parsed = tia.parse_address(raw)
            self.assertIsNotNone(parsed, f"spare {raw} must be a real onboard addr")


# --------------------------------------------------------------------------
# E6 distributed-I/O: FIXTURE-GATED integration test on the real plant
# --------------------------------------------------------------------------
@unittest.skipUnless(_imv1_aml().is_file(), "IMV1 .aml fixture absent")
class DistributedPlantIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.projs = plc_ir.build_tia_distributed_project(str(_imv1_aml()))

    @staticmethod
    def _counts(proj):
        mapped = len(proj.points)
        reserva = sum(1 for s in proj.skipped if s[0] == "RESERVA")
        return mapped + reserva, mapped, reserva

    def test_nine_stations_in_expected_order(self):
        names = [p.name for p in self.projs]
        self.assertEqual(names, [
            "Q100-Cooling1/UV", "Q200", "Q300", "Q400", "Q500", "Q600",
            "Q700", "Q700_1", "S7-1200 station_1",
        ])

    def test_q100_floor_88_48_40(self):
        q100 = self.projs[0]
        self.assertEqual(q100.name, "Q100-Cooling1/UV")
        ch, m, r = self._counts(q100)
        self.assertEqual((ch, m, r), (88, 48, 40))

    def test_q100_per_module_breakdown(self):
        q100 = self.projs[0]

        def part(name):
            mod = q100.modules[name]
            mapped = sum(1 for p in q100.points if p.module is mod)
            return mapped + (mod.points - mapped), mapped, mod.points - mapped

        # F-DQ1500 is split into [DO]+[DI]; combine them = 8/3/5
        do = part("F-DQ1500 [DO]")
        di = part("F-DQ1500 [DI]")
        fdq = (do[0] + di[0], do[1] + di[1], do[2] + di[2])
        self.assertEqual(part("F-DI150"), (16, 16, 0))
        self.assertEqual(part("F-DI156"), (16, 4, 12))
        self.assertEqual(part("DI10_11"), (16, 12, 4))
        self.assertEqual(part("DI12_13"), (16, 5, 11))
        self.assertEqual(fdq, (8, 3, 5))
        self.assertEqual(part("DQ10_11"), (16, 8, 8))

    def test_ownership_q100_s71500_s71200_station(self):
        # Q100 is owned by the 1500 table; the 1200 station by the 1200 table.
        # controller_cpu reflects the station's own CPU type.
        q100 = self.projs[0]
        self.assertEqual(q100.controller_cpu, "CPU 1512SP F-1 PN")
        s1200 = [p for p in self.projs if p.name == "S7-1200 station_1"][0]
        self.assertEqual(s1200.controller_cpu, "CPU 1214C AC/DC/Rly")

    def test_q400_di40_41_15_mapped(self):
        q400 = [p for p in self.projs if p.name == "Q400"][0]
        mod = q400.modules["DI40_41"]
        mapped = sum(1 for p in q400.points if p.module is mod)
        self.assertEqual((mapped, mod.points - mapped), (15, 1))

    def test_plant_totals_and_distributed_mapped(self):
        tot_ch = tot_m = tot_r = 0
        for p in self.projs:
            ch, m, r = self._counts(p)
            tot_ch += ch
            tot_m += m
            tot_r += r
        self.assertEqual(len(self.projs), 9)
        # every distributed station maps at least one channel (not all RESERVA)
        for p in self.projs:
            self.assertGreater(len(p.points), 0, f"{p.name} mapped nothing")
        # sanity: totals are the sum of per-station counts
        self.assertEqual(tot_ch, tot_m + tot_r)
        self.assertGreater(tot_m, 0)


if __name__ == "__main__":
    unittest.main()
