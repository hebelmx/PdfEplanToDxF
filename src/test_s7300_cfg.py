#!/usr/bin/env python3
"""Unit + integration tests for the S7-300 ``.cfg`` hardware-config parser.

Stdlib-only (unittest). Run from src/:
    python -m unittest test_s7300_cfg
or via discovery:
    python -m unittest discover -p "test_*.py"

Two groups:
  * Pure-helper tests (kind/points extraction, masked-? preservation,
    spare/placeholder hint, fw-version parsing, comma/quote splitting,
    SYMBOL/ADDRESS line parsing) -- run everywhere, no fixture.
  * Fixture-gated tests asserting the REAL measured ground truth (station,
    subnets incl. real mask, local I/O modules, DP slaves incl. the
    diagnostic-address modelling and the servo telegram-range-with-no-symbols)
    -- skipped if the (gitignored) fixture is absent.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import s7300_cfg as C


def _fixture() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "Fixtures",
                        "Siemens", "S7300", "brpl2twin.txt.cfg")


_HAVE_FIXTURE = os.path.exists(_fixture())


class TestCfgHelpers(unittest.TestCase):
    """Pure helpers -- no fixture needed."""

    def test_classify_io_types(self):
        self.assertEqual(C.classify_type("DI32xDC24V"), ("DI", 32))
        self.assertEqual(C.classify_type("DO32xDC24V/0.5A"), ("DO", 32))
        self.assertEqual(C.classify_type("AI8x12Bit"), ("AI", 8))
        self.assertEqual(C.classify_type("AO4x12Bit"), ("AO", 4))

    def test_classify_non_io_types(self):
        self.assertEqual(C.classify_type("PS 307 5A"), ("power", None))
        self.assertEqual(C.classify_type("CPU 315-2 PN/DP"), ("cpu", None))
        self.assertEqual(C.classify_type("CP 340-RS232C"), ("comms", None))
        self.assertEqual(C.classify_type("UR"), ("other", None))
        self.assertEqual(C.classify_type(""), ("other", None))
        self.assertEqual(C.classify_type(None), ("other", None))

    def test_looks_like_spare_hint(self):
        # comment == "Spare"
        self.assertTrue(C.looks_like_spare("control off", "Spare"))
        # bare placeholder name with or without leading I/Q
        self.assertTrue(C.looks_like_spare("I0.4", "Spare"))
        self.assertTrue(C.looks_like_spare("I38.1", ""))
        self.assertTrue(C.looks_like_spare("Q11.3", ""))
        self.assertTrue(C.looks_like_spare("13.5 lamp test power", ""))
        # a real, named signal is not a spare
        self.assertFalse(C.looks_like_spare("membrane vacuum OK", ""))
        self.assertFalse(C.looks_like_spare("control off", ""))

    def test_split_commas_respects_quotes(self):
        h = ('RACK 0, SLOT 2, "6ES7 315-2EH14-0AB0" "V3.2", '
             '"CPU 315-2 PN/DP"')
        parts = C._split_commas(h)
        self.assertEqual(parts[0], "RACK 0")
        self.assertEqual(parts[1], "SLOT 2")
        self.assertIn("V3.2", parts[2])
        self.assertEqual(parts[3], '"CPU 315-2 PN/DP"')

    def test_parse_rack_module_with_fw_version(self):
        header = ('RACK 0, SLOT 2, "6ES7 315-2EH14-0AB0" "V3.2", '
                  '"CPU 315-2 PN/DP"')
        fields = C._split_commas(header)
        m = C._parse_rack_module(header, fields)
        self.assertEqual(m.slot, 2)
        self.assertEqual(m.order_no, "6ES7 315-2EH14-0AB0")
        self.assertEqual(m.fw_version, "V3.2")
        self.assertEqual(m.type_str, "CPU 315-2 PN/DP")
        self.assertEqual(m.kind, "cpu")

    def test_parse_rack_module_without_fw_version(self):
        header = 'RACK 0, SLOT 4, "6ES7 321-1BL00-0AA0", "DI32xDC24V"'
        fields = C._split_commas(header)
        m = C._parse_rack_module(header, fields)
        self.assertEqual(m.slot, 4)
        self.assertIsNone(m.fw_version)
        self.assertEqual(m.kind, "DI")
        self.assertEqual(m.points, 32)

    def test_rack_frame_and_subslot_are_not_modules(self):
        # Rack frame (no SLOT) -> None
        frame = 'RACK 0, "6ES7 390-1???0-0AA0", "UR"'
        self.assertIsNone(C._parse_rack_module(frame, C._split_commas(frame)))
        # CPU sub-slot (has SUBSLOT) -> None
        sub = ('RACK 0, SLOT 2, SUBSLOT 2, '
               '"_S7H_HSP_IO_CONTROLLER_315_2EH14_FW32_CT", "PN-IO"')
        self.assertIsNone(C._parse_rack_module(sub, C._split_commas(sub)))

    def test_masked_order_number_preserved_verbatim(self):
        # The masked '?' digits in the rack frame must NOT be filled.
        frame = 'RACK 0, "6ES7 390-1???0-0AA0", "UR"'
        toks = []
        for f in C._split_commas(frame):
            toks.extend(C._quoted(f))
        self.assertEqual(toks[0], "6ES7 390-1???0-0AA0")

    def test_parse_symbol_line(self):
        s = C._parse_symbol_line('SYMBOL  I , 4, "I0.4", "Spare"')
        self.assertEqual(s.area, "I")
        self.assertEqual(s.ch, 4)
        self.assertEqual(s.name, "I0.4")
        self.assertEqual(s.comment, "Spare")
        self.assertTrue(s.looks_like_spare)

    def test_parse_symbol_line_empty_comment(self):
        s = C._parse_symbol_line('SYMBOL  O , 0, "emergecy reset lamp", ""')
        self.assertEqual(s.area, "O")
        self.assertEqual(s.ch, 0)
        self.assertEqual(s.name, "emergecy reset lamp")
        self.assertEqual(s.comment, "")
        self.assertFalse(s.looks_like_spare)

    def test_parse_address_line(self):
        start, length, area = C._parse_address_line(
            "ADDRESS  352, 0, 16, 0, 1, 0")
        self.assertEqual(start, 352)
        self.assertEqual(length, 16)
        self.assertEqual(area, 1)


@unittest.skipUnless(_HAVE_FIXTURE, "S7300 .cfg fixture not present")
class TestCfgFixture(unittest.TestCase):
    """Integration against the real fixture -- asserts MEASURED ground truth."""

    @classmethod
    def setUpClass(cls):
        cls.d = C.parse_cfg(_fixture())

    def test_fileversion_and_station(self):
        self.assertEqual(self.d.fileversion, "3.2")
        self.assertEqual(self.d.station.id, "S7300")
        self.assertEqual(self.d.station.descr, "SIMATIC 300(1)")

    def test_subnets_with_real_mask(self):
        self.assertEqual(len(self.d.subnets), 2)
        by_kind = {s.kind: s for s in self.d.subnets}
        self.assertIn("INDUSTRIAL_ETHERNET", by_kind)
        self.assertIn("PROFIBUS", by_kind)
        eth = by_kind["INDUSTRIAL_ETHERNET"]
        self.assertEqual(eth.name, "Ethernet(1)")
        # Real mask, kept verbatim -- never synthesized.
        self.assertEqual(eth.subnet_mask, "FFFFFF00")
        self.assertEqual(eth.ip_address, "C0A81EBE")
        pb = by_kind["PROFIBUS"]
        self.assertEqual(pb.name, "PROFIBUS(1)")
        self.assertIsNone(pb.subnet_mask)

    def test_local_modules(self):
        # (slot, kind, points, in_start, out_start, symbol_count)
        got = [
            (m.slot, m.kind, m.points,
             m.in_addr.start_byte if m.in_addr else None,
             m.out_addr.start_byte if m.out_addr else None,
             len(m.symbols))
            for m in self.d.modules
        ]
        expected = [
            (1, "power", None, None, None, 0),
            (2, "cpu", None, None, None, 0),
            (4, "DI", 32, 0, None, 32),
            (5, "DI", 32, 4, None, 32),
            (6, "DO", 32, None, 0, 32),
            (7, "DO", 32, None, 4, 32),
            (8, "comms", None, 304, 304, 0),
            (9, "comms", None, 320, 320, 0),
            (10, "AI", 8, 352, None, 0),
        ]
        self.assertEqual(got, expected)

    def test_cpu_module_fw_version(self):
        cpu = next(m for m in self.d.modules if m.slot == 2)
        self.assertEqual(cpu.fw_version, "V3.2")
        self.assertEqual(cpu.order_no, "6ES7 315-2EH14-0AB0")

    def test_slot4_di_first_and_spare_symbols(self):
        di = next(m for m in self.d.modules if m.slot == 4)
        self.assertEqual(len(di.in_addr.symbols), 32)
        ch0 = di.in_addr.symbols[0]
        self.assertEqual(ch0.name, "control off")
        self.assertEqual(ch0.comment, "PB206A  - NO pushbutton")
        # The placeholder/spare channels are preserved, not dropped.
        ch4 = di.in_addr.symbols[4]
        self.assertEqual(ch4.name, "I0.4")
        self.assertEqual(ch4.comment, "Spare")
        self.assertTrue(ch4.looks_like_spare)

    def test_dp_slave_count_and_addresses(self):
        slaves = self.d.dp_slaves
        self.assertEqual(len(slaves), 9)
        by_addr = {s.dp_address: s for s in slaves}
        self.assertEqual(sorted(by_addr), [4, 5, 6, 7, 8, 12, 16, 17, 18])

        # Diagnostic addresses live on the head, distinct from wired ranges.
        self.assertEqual(by_addr[4].diagnostic_addr, 2036)
        self.assertEqual(by_addr[12].diagnostic_addr, 2040)
        self.assertEqual(by_addr[16].diagnostic_addr, 2038)

    def test_et200eco_slave(self):
        s = next(s for s in self.d.dp_slaves if s.dp_address == 4)
        self.assertEqual(s.type_str, "ET 200eco 16DI")
        self.assertEqual(s.gsd, "META\\SIEM80DA.GSE")
        # slot1 status (0 syms), slot2 16DE wired @ start 30 with 16 input syms.
        sub = {ss.slot: ss for ss in s.subslots}
        self.assertEqual(set(sub), {1, 2})
        self.assertEqual(sub[1].symbol_count, 0)
        self.assertEqual(sub[2].type_str, "16DE")
        self.assertEqual(sub[2].in_addr.start_byte, 30)
        self.assertEqual(sub[2].symbol_count, 16)
        self.assertEqual(sub[2].in_addr.symbols[0].name, "LH upr frame cyl EXT")

    def test_festo_cpx_terminal(self):
        s = next(s for s in self.d.dp_slaves if s.dp_address == 12)
        self.assertEqual(s.type_str, "Festo CPX-Terminal")
        sub = {ss.slot: ss for ss in s.subslots}
        # slot1 = Status (input @ 100, no wired channel symbols)
        self.assertEqual(sub[1].type_str, "64")
        self.assertEqual(sub[1].in_addr.start_byte, 100)
        self.assertEqual(sub[1].symbol_count, 0)
        # slots 2..6 = MPA 8DO valve banks, 8 output symbols each.
        for slot in (2, 3, 4, 5, 6):
            self.assertEqual(sub[slot].type_str, "8DA")
            self.assertIsNotNone(sub[slot].out_addr)
            self.assertEqual(sub[slot].symbol_count, 8)
        self.assertEqual(sub[2].out_addr.start_byte, 30)
        self.assertEqual(sub[2].out_addr.symbols[0].name, "LH_upr frame cyl EXT")

    def test_cmmp_servo_telegram_range_has_no_symbols(self):
        # CMMP-AS M3 servos: "FHPP Standard + FPC" telegram range at a high
        # start byte (~528), with NO inline channel symbols.
        servos = [s for s in self.d.dp_slaves
                  if s.type_str == "CMMP-AS M3"]
        self.assertEqual(sorted(s.dp_address for s in servos), [16, 17, 18])
        s16 = next(s for s in servos if s.dp_address == 16)
        sub = {ss.slot: ss for ss in s16.subslots}
        self.assertEqual(set(sub), {1, 2})
        self.assertEqual(sub[1].type_str, "183")
        self.assertEqual(sub[1].in_addr.start_byte, 528)
        self.assertEqual(sub[1].out_addr.start_byte, 528)
        self.assertEqual(sub[1].symbol_count, 0)
        self.assertEqual(sub[2].in_addr.start_byte, 536)
        self.assertEqual(sub[2].symbol_count, 0)

    def test_profinet_io_devices_captured(self):
        # The PROFINET-IO Keyence cameras are present in the file and captured
        # faithfully (outside the brief's DP/local focus but not dropped).
        self.assertGreater(len(self.d.io_devices), 0)
        names = {d.name for d in self.d.io_devices}
        self.assertTrue(any("rear" in n.lower() for n in names))


if __name__ == "__main__":
    unittest.main()
