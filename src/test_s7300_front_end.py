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

    def test_emit_clamps_to_capacity(self):
        # M2: a module declaring N points but carrying a SYMBOL beyond channel
        # N-1 must NOT emit that channel (digital emission clamped to capacity,
        # mirroring the analog `for ch in range(capacity)`).
        real = C.Symbol(area="I", ch=0, name="control off", comment="ok")
        overflow = C.Symbol(area="I", ch=8, name="too far", comment="x")
        mod = F._make_digital_module("m", "S", "DI", 8, 0, 4, "cat")
        points, skipped = [], []
        F._emit_digital_channels(mod, [real, overflow], 8, 0, "DI", "S",
                                 points, skipped)
        # capacity 8 -> ch8 is out of range and dropped (not mapped, not RESERVA)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].tag, "control off")
        self.assertEqual(len(skipped), 0)

    def test_local_station_di_no_address_block_no_crash(self):
        # M3: a DI module with NO address block (in_addr=None) must not raise
        # AttributeError; start defaults to 0 (mirrors the analog guard).
        di = C.CfgModule(rack=0, slot=4, order_no="x", fw_version=None,
                         type_str="DI32xDC24V", kind="DI", points=32,
                         in_addr=None, out_addr=None)
        cfg = C.CfgData(station=C.Station(id="S7300", descr="d"))
        cfg.modules.append(di)
        st = F._local_station(cfg, [], "CPU 315-2 PN/DP")  # must not raise
        mod = next(m for m in st["io_mods"] if m.kind == "DI")
        self.assertEqual(mod.in_byte_base, 0)  # defaulted, no crash

    def test_local_station_do_no_address_block_no_crash(self):
        # M3 (output side): a DO module with out_addr=None must not crash.
        do = C.CfgModule(rack=0, slot=6, order_no="x", fw_version=None,
                         type_str="DO32xDC24V", kind="DO", points=32,
                         in_addr=None, out_addr=None)
        cfg = C.CfgData(station=C.Station(id="S7300", descr="d"))
        cfg.modules.append(do)
        st = F._local_station(cfg, [], None)  # must not raise
        mod = next(m for m in st["io_mods"] if m.kind == "DO")
        self.assertEqual(mod.out_byte_base, 0)

    def test_et200_capacity_generic_32di(self):
        # M4: capacity is derived generically from the sub-slot type. A sibling
        # 32DI drop must report 32 capacity (not its symbol count), so the
        # literal "ET 200eco 16DI" string is no longer a gate.
        addr = C.AddrBlock(direction="in", start_byte=30, length_bytes=4,
                           area_code=0)
        # 32 channels: 10 real-named, 22 bare-address placeholders (-> RESERVA),
        # one symbol per channel as the file format provides.
        syms = []
        for i in range(32):
            if i < 10:
                syms.append(C.Symbol(area="I", ch=i, name=f"sig{i}",
                                     comment="real"))
            else:
                byte, bit = 30 + i // 8, i % 8
                syms.append(C.Symbol(area="I", ch=i, name=f"I{byte}.{bit}",
                                     comment=""))  # bare placeholder
        addr.symbols = syms
        ss = C.DpSubslot(slot=2, io_descr="x", type_str="32DE", in_addr=addr)
        # generic capacity from "32DE" -> 32 (NOT the literal-string gate)
        self.assertEqual(F._et200_capacity(ss), 32)
        slave = C.DpSlave(dp_address=9, gsd="g", type_str="ET 200eco 32DI")
        slave.subslots.append(C.DpSubslot(slot=1, io_descr="status",
                                          type_str="64"))  # no symbols
        slave.subslots.append(ss)
        st = F._dp_station(slave, "CPU 315-2 PN/DP")
        self.assertIsNotNone(st)
        self.assertEqual(st["io_mods"][0].points, 32)  # generic, not len(syms)
        mapped = len(st["points"])
        reserva = sum(1 for s in st["skipped"] if s[0] == "RESERVA")
        self.assertEqual(mapped, 10)
        self.assertEqual(reserva, 22)
        self.assertEqual(mapped + reserva, 32)


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
        self.assertEqual(cap, 136)       # 4x32 digital + AI8 (UNCHANGED by M1)
        # M1 anchored the spare regex: channels whose NAME merely begins with an
        # address but carries a real description are now MAPPED, not RESERVA.
        # Slot4 DI 29 + Slot5 DI 16 + Slot6 DO 32 + Slot7 DO 32 (+ 0 AI8) = 109.
        self.assertEqual(mapped, 109)
        # 11 digital RESERVA (bare-address placeholders) + 8 AI8 = 19+8 = 27.
        self.assertEqual(reserva, 27)
        self.assertEqual(mapped + reserva, cap)

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
            # M1: DP7 +1 mapped (I37.6 FC Cil move conect), DP8 +8 mapped
            # (the I38.x/I39.x described channels).
            "DP7 ET 200eco 16DI": (14, 2),
            "DP8 ET 200eco 16DI": (10, 6),
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
        # M1: the 10 Festo channels whose names begin with a Q-address but carry
        # a real description (Q30.6 ext cil horiz alin, ... Q38.7 ret cil
        # ventcap) are now MAPPED -> 40 mapped / 0 RESERVA.
        self.assertEqual(mapped, 40)
        self.assertEqual(reserva, 0)
        self.assertEqual(mapped + reserva, cap)
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
        self.assertEqual(cap, 256)        # capacity UNCHANGED by M1
        # M1 moved 27 channels RESERVA -> mapped (real wired I/O whose names
        # merely begin with an address token). Old floor was 256/187/69.
        self.assertEqual(mapped, 214)     # 187 + 27
        self.assertEqual(reserva, 42)     # 69 - 27
        self.assertEqual(mapped + reserva, cap)  # every slot accounted for

    def test_m1_previously_dropped_channels_now_mapped(self):
        # M1 regression: channels whose NAME merely begins with an address token
        # but carry a real description were WRONGLY dropped to RESERVA by the
        # unanchored spare regex. They must now be MAPPED with their REAL tag +
        # comment (faithfulness -- never drop real wired I/O).
        all_points = [pt for p in self.projects for pt in p.points]
        by_tag = {pt.tag: pt for pt in all_points}
        # the "13.5 lamp test power" channel (was RESERVA, now mapped @ %Q3.7)
        self.assertIn("13.5 lamp test power", by_tag)
        lamp = by_tag["13.5 lamp test power"]
        self.assertEqual(lamp.description, "Power on to lamp")
        self.assertEqual(lamp.logix_address, "%Q3.7")
        # a few more of the 27 flipped channels, with their real tag+comment
        self.assertIn("I2.6 LeftSide S.Det Vent", by_tag)
        self.assertEqual(by_tag["I2.6 LeftSide S.Det Vent"].description,
                         "Deteccion Color VentCap")
        self.assertIn("Q3.4 Venturi AutoExp", by_tag)
        self.assertEqual(by_tag["Q3.4 Venturi AutoExp"].description, "VW216")
        # the "Xspare"-named channels are faithful real channels, now mapped
        self.assertIn("Q3.6spare", by_tag)
        self.assertEqual(by_tag["Q3.6spare"].description, "1=resistance test")
        self.assertIn("Q2.0spare", by_tag)
        # and a Festo (DP12) flipped channel
        self.assertIn("Q38.7 ret cil ventcap", by_tag)

    def test_m1_bare_placeholders_still_reserva(self):
        # The genuine bare-address placeholders (whole name == an address token)
        # must STAY RESERVA -- M1 only un-drops names with a trailing description.
        all_tags = {pt.tag for p in self.projects for pt in p.points}
        # these bare placeholder names must NOT have become mapped points
        for bare in ("I0.4", "I3.7"):
            self.assertNotIn(bare, all_tags)

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


@unittest.skipUnless(_HAVE_FIXTURE, "S7300 fixtures not present")
class TestOffmoduleGroups(unittest.TestCase):
    """build_offmodule_groups_s7300 adapts the NON-channel devices (servos +
    PROFINET cameras) into render_plant's `groups` shape — faithfully, never
    inventing. Drives = 3 CMMP-AS servos; Identification = 2 Keyence cameras
    carrying their REAL .asc PIW rows + inline .cfg SYMBOL O rows."""

    @classmethod
    def setUpClass(cls):
        import s7300_asc as A
        cls.cfg = C.parse_cfg(_cfg_fixture())
        cls.asc = A.parse_asc(_asc_fixture())
        cls.groups = F.build_offmodule_groups_s7300(cls.cfg, cls.asc)

    def _func(self, name):
        for f, els in self.groups:
            if f == name:
                return els
        return None

    def test_two_functions_in_order(self):
        funcs = [f for f, _ in self.groups]
        # Drives then Identification (match OFFMODULE_FUNCTIONS order); no
        # Coordination/Safety (no off-module S7-300 devices fall there).
        self.assertEqual(funcs, ["Drives", "Identification"])

    def test_drives_three_servo_elements_with_ranges(self):
        drives = self._func("Drives")
        self.assertEqual(len(drives), 3)
        # each servo is a faithful identity + telegram range tags (no invented
        # channel names; the label is the real CMMP-AS FHPP profile).
        names = [e["name"] for e in drives]
        self.assertEqual(names,
                         ["CMMP-AS M3 (DP16)", "CMMP-AS M3 (DP17)",
                          "CMMP-AS M3 (DP18)"])
        for e in drives:
            self.assertTrue(e["tags"], "servo element has no range tag")
            for raw, name, desc in e["tags"]:
                self.assertEqual(name, "FHPP telegram")
                self.assertEqual(desc, "")
                # a REAL word-range address span, nothing invented
                self.assertRegex(raw, r"^%[IQ]W\d+")

    def test_identification_three_elements_two_cameras_plus_unassigned(self):
        # FIX A (faithful): 2 cameras (each carrying its OWN .cfg slots) PLUS one
        # separate "unassigned telegrams" element for the 4 .asc PIW words. The
        # PIW words are NEVER assigned to a specific camera (no data link).
        ident = self._func("Identification")
        self.assertEqual(len(ident), 3)
        self.assertEqual([e["name"] for e in ident],
                         ["STleftrear", "strightrear",
                          F.OFFMODULE_UNASSIGNED_TELEGRAMS_NAME])

    def test_camera_tags_are_real_cfg_slots(self):
        # FIX A: each camera's tags are its DATA-LINKED .cfg IOSUBSYSTEM slots
        # (real word-range address + real slot name), NOT a guessed PIW link.
        ident = {e["name"]: e for e in self._func("Identification")}
        left = {raw: name for raw, name, _ in ident["STleftrear"]["tags"]}
        # STleftrear's real slots: Command Control is an OUTPUT @ byte 1148 len 12
        # -> %QW1148..%QW1159 (faithful direction + real byte span).
        self.assertEqual(left.get("%QW1148..%QW1159"), "Command Control")
        # input slots at their real bytes (Command Status Bits @ 1146 len 4)
        self.assertEqual(left.get("%IW1146..%IW1149"), "Command Status Bits")
        self.assertEqual(left.get("%IW1150..%IW1153"), "Device Result Bits")
        self.assertEqual(left.get("%IW1200..%IW1215"), "Device Status Words")
        # NEVER invent: no PIW telegram word is attached to a camera
        for raw in left:
            self.assertNotIn("PIW", raw)

    def test_strightrear_inline_cfg_symbols_present(self):
        # FIX A: strightrear's inline .cfg SYMBOL O rows at their real %Q address.
        ident = {e["name"]: e for e in self._func("Identification")}
        right = {raw: name for raw, name, _ in ident["strightrear"]["tags"]}
        self.assertEqual(right.get("%Q1300.0"), "Np")
        self.assertEqual(right.get("%Q1301.0"), "trigger")
        self.assertEqual(right.get("%Q1302.0"), "Reset_Camaras")
        # and its real slot ranges too (e.g. Result Data 128Byte @ 1312 len 128)
        names = {name for _, name, _ in ident["strightrear"]["tags"]}
        self.assertIn("Result Data 128Byte", names)
        # NEVER invent: no PIW telegram word attached to this camera either
        for raw in right:
            self.assertNotIn("PIW", raw)

    def test_unassigned_telegrams_carry_the_four_piw_words(self):
        # FIX A (never-invent): the 4 .asc PIW words live in their OWN element,
        # not on any camera; faithful name + address, ascending order.
        ident = {e["name"]: e for e in self._func("Identification")}
        unassigned = ident[F.OFFMODULE_UNASSIGNED_TELEGRAMS_NAME]
        tags = {raw: name for raw, name, _ in unassigned["tags"]}
        self.assertEqual(len(unassigned["tags"]), 4)
        self.assertEqual(tags.get("%PIW372"), "Camera_Result")
        self.assertEqual(tags.get("%PIW374"), "currrent_job_numb")
        self.assertEqual(tags.get("%PIW736"), "Job Status")
        self.assertEqual(tags.get("%PIW738"), "Job Number")
        # this element is NOT a camera (its name is the unassigned label)
        self.assertNotIn(unassigned["name"], ("STleftrear", "strightrear"))

    def test_never_invent_no_asc_cameras_keep_cfg_slots(self):
        # Without the .asc the unassigned-telegrams element disappears (no PIW
        # rows to carry) but the cameras keep their .cfg slot tags and the servos
        # are unaffected. NOTHING is invented.
        groups = F.build_offmodule_groups_s7300(self.cfg, [])
        funcs = dict(groups)
        self.assertEqual(len(funcs["Drives"]), 3)
        for e in funcs["Drives"]:
            self.assertTrue(e["tags"])
        ident = {e["name"]: e for e in funcs["Identification"]}
        # only the two cameras now (no unassigned-telegrams element without .asc)
        self.assertEqual(set(ident), {"STleftrear", "strightrear"})
        self.assertTrue(ident["STleftrear"]["tags"])  # real .cfg slots, not empty
        self.assertTrue(ident["strightrear"]["tags"])

    def test_empty_inputs_yield_empty_groups(self):
        empty = C.parse_cfg("/no/such/file.cfg") \
            if False else None
        # a cfg with no devices -> [] (gated off, never an empty section)
        class _Empty:
            dp_slaves = []
            io_devices = []
        self.assertEqual(F.build_offmodule_groups_s7300(_Empty(), []), [])


if __name__ == "__main__":
    unittest.main()
