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


if __name__ == "__main__":
    unittest.main()
