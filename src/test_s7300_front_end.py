#!/usr/bin/env python3
"""Unit + integration tests for the S7-300 IR front-end.

Stdlib-only (unittest). Run from src/:
    python -m unittest test_s7300_front_end
or via discovery:
    python -m unittest discover -p "test_*.py"

Two groups:
  * Pure-helper tests (digital I/Q address computation; analog word math; the
    kind->byte/word base assignment; the PIW word index) -- run everywhere,
    no fixture.
  * Fixture-gated integration against the REAL fixture, asserting MEASURED
    ground truth: the ordered station list (count + names + controller_cpu);
    per-station module/point/RESERVA counts; the AI8 mapped-via-.asc behaviour
    (0 mapped / 8 RESERVA -- the .asc PIW rows fall OUTSIDE the AI8 word range);
    the plant floor (capacity / mapped / RESERVA); and that the CMMP servos +
    Keyence cameras are NOT in any io_mods/points but ARE exposed off-module.
    Skipped if the (gitignored) fixtures are absent.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plc_ir
import s7300_cfg as C
import s7300_front_end as F


def _cfg_fixture() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "Fixtures",
                        "Siemens", "S7300", "brpl2twin.txt.cfg")


def _asc_fixture() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "Fixtures",
                        "Siemens", "S7300", "brpl2twin.txt.asc")


_HAVE_FIXTURE = os.path.exists(_cfg_fixture()) and os.path.exists(_asc_fixture())


# ---------------------------------------------------------------------------
# Pure helpers -- no fixture needed
# ---------------------------------------------------------------------------
class TestFrontEndHelpers(unittest.TestCase):

    def test_digital_address_input(self):
        # start byte 0: channel 0 -> %I0.0, channel 7 -> %I0.7, channel 8 -> %I1.0
        self.assertEqual(F.digital_address("I", 0, 0), "%I0.0")
        self.assertEqual(F.digital_address("I", 0, 7), "%I0.7")
        self.assertEqual(F.digital_address("I", 0, 8), "%I1.0")
        # start byte 4: channel 0 -> %I4.0, channel 5 -> %I4.5
        self.assertEqual(F.digital_address("I", 4, 0), "%I4.0")
        self.assertEqual(F.digital_address("I", 4, 5), "%I4.5")
        # start byte 30 (ET200 drop): channel 9 -> %I31.1
        self.assertEqual(F.digital_address("I", 30, 9), "%I31.1")

    def test_digital_address_output(self):
        # 'O' and 'Q' both mean output -> %Q...
        self.assertEqual(F.digital_address("O", 0, 0), "%Q0.0")
        self.assertEqual(F.digital_address("O", 4, 3), "%Q4.3")
        self.assertEqual(F.digital_address("O", 30, 8), "%Q31.0")

    def test_analog_word_math(self):
        # AI8 start byte 352: each channel is 1 word (2 bytes)
        self.assertEqual(F.analog_word(352, 0), 352)
        self.assertEqual(F.analog_word(352, 1), 354)
        self.assertEqual(F.analog_word(352, 7), 366)

    def test_kind_direction(self):
        self.assertEqual(F._kind_direction("DI"), "I")
        self.assertEqual(F._kind_direction("AI"), "I")
        self.assertEqual(F._kind_direction("DO"), "O")
        self.assertEqual(F._kind_direction("AO"), "O")

    def test_digital_module_base_assignment(self):
        di = F._make_digital_module("Slot4 DI", "S7300", "DI", 32, 0, 4, "cat")
        self.assertEqual(di.kind, "DI")
        self.assertEqual(di.points, 32)
        self.assertEqual(di.in_byte_base, 0)
        self.assertEqual(di.out_byte_base, 0)  # untouched default
        self.assertEqual(di.slot, 4)
        self.assertEqual(di.catalog, "cat")
        do = F._make_digital_module("Slot7 DO", "S7300", "DO", 32, 4, 7, "cat2")
        self.assertEqual(do.kind, "DO")
        self.assertEqual(do.out_byte_base, 4)
        self.assertEqual(do.in_byte_base, 0)  # untouched default

    def test_emit_digital_channels_spare_vs_mapped(self):
        # build two synthetic symbols: one real, one placeholder spare.
        real = C.Symbol(area="I", ch=0, name="control off",
                        comment="PB206A  - NO pushbutton")
        spare = C.Symbol(area="I", ch=4, name="I0.4", comment="Spare")
        self.assertFalse(real.looks_like_spare)
        self.assertTrue(spare.looks_like_spare)
        mod = F._make_digital_module("m", "S", "DI", 32, 0, 4, "cat")
        points, skipped = [], []
        F._emit_digital_channels(mod, [real, spare], 32, 0, "DI", "S",
                                 points, skipped)
        self.assertEqual(len(points), 1)
        self.assertEqual(len(skipped), 1)
        p = points[0]
        self.assertEqual(p.tag, "control off")
        self.assertEqual(p.index, 0)
        self.assertEqual(p.direction, "I")
        self.assertFalse(p.analog)
        self.assertEqual(p.description, "PB206A  - NO pushbutton")
        self.assertEqual(p.logix_address, "%I0.0")
        # spare -> RESERVA, computed real address, never a tag
        self.assertEqual(skipped[0], ("RESERVA", "%I0.4", "spare"))

    def test_emit_blank_comment_never_invented(self):
        sym = C.Symbol(area="O", ch=0, name="CR8403A", comment="")
        mod = F._make_digital_module("m", "S", "DO", 32, 4, 7, "cat")
        points, skipped = [], []
        F._emit_digital_channels(mod, [sym], 32, 4, "DO", "S", points, skipped)
        self.assertEqual(points[0].description, "")  # blank, not fabricated
        self.assertEqual(points[0].logix_address, "%Q4.0")

    def test_piw_index_only_numeric_words(self):
        rows = [
            type("R", (), {"area": "PIW", "addr": "372", "name": "Cam",
                           "comment": ""})(),
            type("R", (), {"area": "PIW", "addr": "notnum", "name": "x",
                           "comment": ""})(),
            type("R", (), {"area": "I", "addr": "0.0", "name": "y",
                           "comment": ""})(),
        ]
        idx = F._index_piw_by_word(rows)
        self.assertIn(372, idx)
        self.assertEqual(len(idx), 1)  # non-numeric + non-PIW dropped

    def test_offmodule_devices_empty_cfg(self):
        empty = C.CfgData(station=None)
        self.assertEqual(F.offmodule_devices(empty), [])


# ---------------------------------------------------------------------------
# Fixture integration -- MEASURED ground truth
# ---------------------------------------------------------------------------
@unittest.skipUnless(_HAVE_FIXTURE, "S7300 fixtures not present")
class TestFrontEndFixture(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.projects = plc_ir.build_s7300_project(_cfg_fixture(), _asc_fixture())
        cls.cfg = C.parse_cfg(_cfg_fixture())

    def test_station_list_order_and_cpu(self):
        names = [p.name for p in self.projects]
        self.assertEqual(names, [
            "S7300",
            "DP4 ET 200eco 16DI",
            "DP5 ET 200eco 16DI",
            "DP6 ET 200eco 16DI",
            "DP7 ET 200eco 16DI",
            "DP8 ET 200eco 16DI",
            "DP12 Festo CPX-Terminal",
        ])
        # every project is siemens, real CPU type read from the cfg
        for p in self.projects:
            self.assertEqual(p.source_vendor, "siemens")
            self.assertEqual(p.controller_cpu, "CPU 315-2 PN/DP")
            self.assertEqual(p.network_nodes, [])  # empty for this chunk

    def test_local_station_modules(self):
        local = self.projects[0]
        # (name, kind, points, slot, catalog, in_base, out_base, an_in_word_base)
        got = [
            (m.name, m.kind, m.points, m.slot, m.catalog,
             m.in_byte_base, m.out_byte_base, m.an_in_word_base)
            for m in local.io_mods
        ]
        expected = [
            ("Slot4 DI", "DI", 32, 4, "6ES7 321-1BL00-0AA0", 0, 0, 0),
            ("Slot5 DI", "DI", 32, 5, "6ES7 321-1BL00-0AA0", 4, 0, 0),
            ("Slot6 DO", "DO", 32, 6, "6ES7 322-1BL00-0AA0", 0, 0, 0),
            ("Slot7 DO", "DO", 32, 7, "6ES7 322-1BL00-0AA0", 0, 4, 0),
            ("Slot10 AI", "AI", 8, 10, "6ES7 331-7KF02-0AB0", 0, 0, 352),
        ]
        self.assertEqual(got, expected)

    def test_local_station_floor(self):
        local = self.projects[0]
        cap = sum(m.points for m in local.io_mods)
        mapped = len(local.points)
        reserva = sum(1 for s in local.skipped if s[0] == "RESERVA")
        self.assertEqual(cap, 136)       # 4x32 digital + AI8
        self.assertEqual(mapped, 101)    # 27+16+28+30 (+ 0 AI8)
        self.assertEqual(reserva, 35)    # 5+16+4+2 (+ 8 AI8)

    def test_ai8_mapped_via_asc_is_all_reserva(self):
        # The local AI8 has NO inline symbols; it joins via .asc PIW word
        # addresses. The 4 PIW rows are at words 372/374/736/738 -- ALL OUTSIDE
        # the AI8 word range (352..366), so the AI8 maps 0 / RESERVA 8.
        local = self.projects[0]
        ai = next(m for m in local.io_mods if m.kind == "AI")
        ai_points = [p for p in local.points if p.module is ai]
        self.assertEqual(len(ai_points), 0)
        ai_reserva = [s for s in local.skipped if s[1].startswith("%IW")]
        # 8 channel words -> 8 RESERVA, computed from the real start byte 352
        self.assertEqual(len(ai_reserva), 8)
        self.assertEqual(ai_reserva[0], ("RESERVA", "%IW352", "spare"))
        self.assertEqual(ai_reserva[-1], ("RESERVA", "%IW366", "spare"))

    def test_ai8_maps_when_piw_in_range(self):
        # Positive control: a synthetic PIW row INSIDE the AI8 range DOES map,
        # proving the join works and the fixture's 0-map is data, not a bug.
        import s7300_asc as A
        row = A.AscSymbol(name="Pressure", area="PIW", addr="354",
                          bit_addr=None, datatype="WORD", comment="bar")
        st = F._local_station(self.cfg, [row], "CPU 315-2 PN/DP")
        ai = next(m for m in st["io_mods"] if m.kind == "AI")
        ai_points = [p for p in st["points"] if p.module is ai]
        self.assertEqual(len(ai_points), 1)
        p = ai_points[0]
        self.assertEqual(p.tag, "Pressure")
        self.assertTrue(p.analog)
        self.assertEqual(p.index, 1)            # word 354 == channel 1
        self.assertEqual(p.logix_address, "%PIW354")  # real area+number
        self.assertEqual(p.description, "bar")

    def test_et200_drops(self):
        # (name, mapped, reserva) per ET200eco drop (each cap 16)
        drops = {p.name: p for p in self.projects if "ET 200eco" in p.name}
        expected = {
            "DP4 ET 200eco 16DI": (14, 2),
            "DP5 ET 200eco 16DI": (13, 3),
            "DP6 ET 200eco 16DI": (14, 2),
            "DP7 ET 200eco 16DI": (13, 3),
            "DP8 ET 200eco 16DI": (2, 14),
        }
        for name, (mapped, reserva) in expected.items():
            p = drops[name]
            self.assertEqual(len(p.io_mods), 1)
            self.assertEqual(p.io_mods[0].kind, "DI")
            self.assertEqual(p.io_mods[0].points, 16)
            self.assertEqual(len(p.points), mapped, name)
            res = sum(1 for s in p.skipped if s[0] == "RESERVA")
            self.assertEqual(res, reserva, name)

    def test_et200_dp4_first_channel_address(self):
        dp4 = next(p for p in self.projects if p.name == "DP4 ET 200eco 16DI")
        first = dp4.points[0]
        self.assertEqual(first.tag, "LH upr frame cyl EXT")
        self.assertEqual(first.direction, "I")
        self.assertEqual(first.logix_address, "%I30.0")  # start byte 30, ch 0

    def test_festo_cpx_station(self):
        festo = next(p for p in self.projects
                     if p.name == "DP12 Festo CPX-Terminal")
        # 5 valve banks (8DO each), all outputs
        self.assertEqual(len(festo.io_mods), 5)
        for m in festo.io_mods:
            self.assertEqual(m.kind, "DO")
            self.assertEqual(m.points, 8)
        cap = sum(m.points for m in festo.io_mods)
        mapped = len(festo.points)
        reserva = sum(1 for s in festo.skipped if s[0] == "RESERVA")
        self.assertEqual(cap, 40)
        self.assertEqual(mapped, 30)
        self.assertEqual(reserva, 10)
        # first output channel: bank slot2 start byte 30 -> %Q30.0
        first = festo.points[0]
        self.assertEqual(first.tag, "LH_upr frame cyl EXT")
        self.assertEqual(first.direction, "O")
        self.assertEqual(first.logix_address, "%Q30.0")

    def test_plant_floor_totals(self):
        cap = sum(m.points for p in self.projects for m in p.io_mods)
        mapped = sum(len(p.points) for p in self.projects)
        reserva = sum(1 for p in self.projects for s in p.skipped
                      if s[0] == "RESERVA")
        self.assertEqual(cap, 256)
        self.assertEqual(mapped, 187)
        self.assertEqual(reserva, 69)
        self.assertEqual(mapped + reserva, cap)  # every slot accounted for

    def test_servos_and_cameras_not_channels(self):
        # The CMMP servos + Keyence cameras must NEVER appear as a channel
        # module or a mapped point in ANY station.
        for p in self.projects:
            for m in p.io_mods:
                self.assertNotIn("CMMP", m.name)
                self.assertNotIn("camera", m.name.lower())
                self.assertNotIn("rear", m.name.lower())
            for pt in p.points:
                # no point should carry a high servo/camera telegram address
                self.assertFalse(pt.logix_address.startswith("%IW5"))

    def test_offmodule_devices_exposed(self):
        devs = F.offmodule_devices(self.cfg)
        servos = [d for d in devs if d["kind"] == "servo"]
        cams = [d for d in devs if d["kind"] == "camera"]
        # 3 CMMP-AS M3 servos at DP 16/17/18
        self.assertEqual(sorted(d["address"] for d in servos), [16, 17, 18])
        for d in servos:
            self.assertEqual(d["type"], "CMMP-AS M3")
            self.assertEqual(d["bus"], "PROFIBUS-DP")
            self.assertTrue(d["ranges"])  # real telegram ranges, not empty
        # 2 Keyence cameras at IO address 1/2
        self.assertEqual(sorted(d["address"] for d in cams), [1, 2])
        cam_types = {d["type"] for d in cams}
        self.assertIn("STleftrear", cam_types)
        self.assertIn("strightrear", cam_types)

    def test_servo_real_address_ranges(self):
        devs = F.offmodule_devices(self.cfg)
        s16 = next(d for d in devs
                   if d["kind"] == "servo" and d["address"] == 16)
        # DP16 telegrams: in/out @ 528 and @ 536 (the real parsed ranges)
        starts = {(io, start) for (io, start, _ln) in s16["ranges"]}
        self.assertIn(("in", 528), starts)
        self.assertIn(("out", 528), starts)
        self.assertIn(("in", 536), starts)


# ---------------------------------------------------------------------------
# Seam (plc_ir.build_s7300_project) -- graceful degradation
# ---------------------------------------------------------------------------
class TestSeam(unittest.TestCase):

    def test_missing_cfg_returns_empty(self):
        self.assertEqual(plc_ir.build_s7300_project("/no/such/file.cfg"), [])
        self.assertEqual(plc_ir.build_s7300_project(""), [])

    @unittest.skipUnless(_HAVE_FIXTURE, "S7300 fixtures not present")
    def test_cfg_only_no_asc_still_builds(self):
        # Without the .asc the AI8 simply has no PIW rows to join -> all RESERVA,
        # but the digital stations are unaffected and the seam still returns the
        # full ordered station list.
        projs = plc_ir.build_s7300_project(_cfg_fixture(), None)
        self.assertEqual(len(projs), 7)
        local = projs[0]
        ai = next(m for m in local.io_mods if m.kind == "AI")
        ai_points = [p for p in local.points if p.module is ai]
        self.assertEqual(len(ai_points), 0)  # no asc -> AI8 all RESERVA


if __name__ == "__main__":
    unittest.main()
