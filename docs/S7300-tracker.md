# S7-300 import tracker  (EPIC E7 — legacy STEP 7 Classic front-end)

> Durable source of truth for the S7-300 batch. Survives a context clear.
> Driven by the bmad-orchestrator loop. Mirror of the in-session Task list.
> Started 2026-06-17. Branch: `feat/s7300-import` (off `main` @ E6-merged `f3a3fc5`).
> Abel chose this batch (2026-06-17) over issue-#3 nits / símbología dict.

## Why this is feasible & WHY it reuses the E6 framework wholesale
The S7-300 station is the **same shape as the E6 distributed plant**: one CPU with a
**local rack + a PROFIBUS-DP line of remote drops**, plus telegram drives. It slots onto
the existing renderer with **no renderer changes** — the work is parsers → IR front-end →
a thin CLI. Architecture seam (verified from ground truth):
- `render_plant(station_irs, out, *, no_symbols, …)` (`src/render_plant.py:660`) takes a
  `list[PlcProject]` — the local rack + each DP drop become "stations" (E6 bands).
- `build_offmodule_groups` / `render_plant.build_offmodule_section` already draw telegram
  drives by function → per element (the CMMP servos are the SK-drive analog).
- `tia_to_qet.py --distributed` (`main()` @ :125) is the exact template for a new
  `src/s7300_to_qet.py`.
- `plc_ir.build_tia_distributed_project(aml) -> list[PlcProject]` (`:145`) is the exact
  template for `build_s7300_project(cfg, asc) -> list[PlcProject]`.
- `logix_to_qet.py` / `render_project` stay **UNTOUCHED** → Rockwell byte-identical.

## Ground-truth fixture (orchestrator-verified from the files, 2026-06-17)
`Fixtures/Siemens/S7300/brpl2twin.txt.{cfg,asc}` — a **CPU 315-2 PN/DP** station,
`STATION S7300 , "SIMATIC 300(1)"`. Both files ASCII/CRLF, stdlib-parseable, NO Openness.

### `.cfg` (STEP 7 HW config, `FILEVERSION "3.2"`) — the HARDWARE source (the `.aml` analog)
- **Subnets:** `SUBNET INDUSTRIAL_ETHERNET , "Ethernet(1)"` + `SUBNET PROFIBUS , "PROFIBUS(1)"`
  (1.5 MBPS). PN-IO subslot carries `SUBNETMASK "FFFFFF00"` (real mask, never invent).
- **Local rack (RACK 0):** `RACK 0, SLOT m, "<6ES7 order#>", "<type>"` lines:
  | Slot | Order#            | Type             | Kind/points |
  |------|------------------|------------------|-------------|
  | 1    | 6ES7 307-1EA01…  | PS 307 5A        | power (not I/O) |
  | 2    | 6ES7 315-2EH14…  | CPU 315-2 PN/DP  | CPU (not I/O) |
  | 4    | 6ES7 321-1BL00…  | DI32xDC24V       | **32 DI** |
  | 5    | 6ES7 321-1BL00…  | DI32xDC24V       | **32 DI** |
  | 6    | 6ES7 322-1BL00…  | DO32xDC24V/0.5A  | **32 DO** |
  | 7    | 6ES7 322-1BL00…  | DO32xDC24V/0.5A  | **32 DO** |
  | 8    | 6ES7 340-1AH02…  | CP 340-RS232C    | comms (0/0 addr) |
  | 9    | 6ES7 340-1AH02…  | CP 340-RS232C    | comms (0/0 addr) |
  | 10   | 6ES7 331-7KF02…  | AI8x12Bit        | **8 AI** |
  - `RACK 0, "6ES7 390-1???0-0AA0", "UR"` — the rack frame; **masked `?` = version-independent
    wildcard, KEEP AS-IS, never fill** (Abel, memory `siemens-import-findings`).
  - **The `type` string encodes kind + point count** (`DI32`→DI/32, `DO32`→DO/32, `AI8`→AI/8).
- **Address + inline symbols (THE KEY FINDING):** under each module/sub-slot is a
  `LOCAL_IN_ADDRESSES` / `LOCAL_OUT_ADDRESSES` block: `ADDRESS  <startByte>, …` then **inline**
  `SYMBOL  I , <ch>, "<name>", "<comment>"` lines, one per channel (`ch` = 0-based index
  within the module). **STEP 7 already joined tag↔channel** — the `.cfg` is largely
  self-contained for the wired I/O. `O` for outputs. Example (slot 4 DI32): `SYMBOL I, 0,
  "control off", "PB206A - NO pushbutton"` … `SYMBOL I, 31, "I3.7", "Spare"`.
  - **SPARE rule (mirrors empty-`<Tag>`):** a channel is RESERVA when the name is a bare
    placeholder address (`"I0.4"`, `"I38.1"`, `"I2.7"`, `"Q11.3"`-style `^[IQ]?\d+\.\d`) AND/OR
    the comment is `"Spare"`. Conservative; mapped channels carry the real name+comment.
- **PROFIBUS-DP slaves:** `DPSUBSYSTEM 1, DPADDRESS <a>, "<GSD>", "<type>"` then sub-slots
  `DPSUBSYSTEM 1, DPADDRESS <a>, SLOT <k>, "<x output bytes, y input bytes>", "<type>"` each
  with their own ADDRESS + inline SYMBOL lines:
  - **5× `ET 200eco 16DI`** @ DPADDRESS 4,5,6,7,8 — each **16 DI** w/ inline symbols (remote DI drops).
  - **1× `Festo CPX-Terminal`** @ DPADDRESS 12 — valve terminal: Status sub-slot + **MPA 8DO**
    blocks (`SYMBOL O, …`) → real **DO** channels w/ tags.
  - **3× `CMMP-AS M3`** @ DPADDRESS 16,17,18 — Festo servo drives; sub-slot `"FHPP Standard + FPC"`
    is a **telegram** range (`ADDRESS 528, …, 8 bytes`) with **NO inline channel symbols** →
    the **off-module / telegram** analog (E6 c2 SK-drive treatment; never synthesize channels).
- The first `LOCAL_IN_ADDRESSES ADDRESS 2036/2038/2040 …` per slave head = the DP diagnostic
  address (not wired I/O) — do not enumerate as channels.

### `.asc` (global symbol table, ~1467 rows) — the TAG source (the `PLCTags*.xlsx` analog)
- Fixed-width: `126,<name…><area> <addr> <datatype> <comment>`. Area letters seen (histogram):
  **I 157 / Q 125 / PIW 4** (physical I/O — KEEP) vs **M 679 / FC 160 / FB 52 / DB 124 / T 148**
  (flags + program objects — FILTER OUT, like non-hardwired Rockwell points).
- I/Q/PIW rows give **name↔byte.bit-address↔comment** → cross-check / fallback for the inline
  `.cfg` symbols (they agree: `.cfg` slot4 ch0 == `.asc` "control off" I 0.0 same comment).
  PIW (4) = the AI8 analog input words.

### Rough floor (to be RE-DERIVED & asserted by the dev cycle — do NOT trust this estimate)
Local wired capacity 136 (64 DI + 64 DO + 8 AI) + DP 80 DI (5×16) + Festo CPX DO (~16) +
servo telegrams (off-module). `.asc` physical symbols ≈ 157 I + 125 Q + 4 PIW. **The dev cycle
computes the exact capacity / mapped / RESERVA from the IR and the tracker locks it.**

## Non-negotiable guardrails (every item)
- **Rockwell WADDING_1 floor must hold 11/106/75/0, 78 RESERVA, 35 folios** and stay
  **BYTE-EQUIVALENT** (this epic adds NEW files only; `logix_to_qet.py` / `render_project`
  untouched). Run the byte-equiv diff every chunk that could touch shared code.
- **Single-station + distributed TIA paths UNCHANGED** (22 folios single; the E6 plant render).
- **Never invent:** masked `?` kept as-is; missing symbol → address-only (NEVER a fabricated
  tag); empty/placeholder name + "Spare" = RESERVA; servo telegrams stay off-module (no
  synthesized channels); real subnet mask from the `.cfg`, never a fabricated `/24`.
- **stdlib only**; `source_vendor="siemens"`; same `PlcProject` shape → renderer needs no
  vendor branch. **NEVER `git add` under `Fixtures/`** or any `*.cfg`/`*.asc`/`*.qet`/personal file.
- One focused commit per item; feature branch → **Abel's merge gate**.

## Build chunks (recommended order — data first, visual LAYOUT gated at render per E6)
- [ ] **S7300-1 — parsers (data-only, fully tested).** `src/s7300_cfg.py` (station/subnets/
      local rack modules w/ catalog+type→kind/points+address+inline symbols; PROFIBUS DP slaves
      w/ sub-slots+address+inline symbols; masked `?` kept) + `src/s7300_asc.py` (filter to
      I/Q/PIW → {address:(name,comment)}; drop M/FC/FB/DB/T/C). Pure parse, no IR, no render.
      Tests assert REAL fixture numbers (module counts, address ranges, slave counts, symbol
      counts, M-row filtering, masked-? preservation). Suite stays green; Rockwell byte-equiv.
- [ ] **S7300-2 — front-end → IR (data-only, fully tested).** `src/s7300_front_end.py` +
      `plc_ir.build_s7300_project(cfg_path, asc_path=None) -> list[PlcProject]` (mirror
      `build_tia_distributed_project`): one PlcProject per local-rack station + each DP drop;
      modules+points+RESERVA from inline `.cfg` symbols (primary), `.asc` cross-check; spare
      rule; servo telegrams collected for the off-module section; `network_nodes` from the two
      subnets (real mask); `controller_cpu` = CPU 315-2. **RE-DERIVE & LOCK the floor here.**
- [ ] **S7300-3 — CLI + render + off-module + EYEBALL GATE.** `src/s7300_to_qet.py` (mirror
      `tia_to_qet --distributed`) → `render_plant` + off-module section. **Visual LAYOUT =
      GATED to Abel** (per-station bands vs single-station-with-DP-cards; section labels;
      PROFIBUS network folio). Regenerate `_eyeball_s7300.qet`, launch QET, gate before locking.
- [ ] **Adversarial review** at the phase boundary (3 lenses + general) against this tracker
      + the never-invent guardrails. Triage findings back here.

## Status log
- 2026-06-17: Tracker created; branch `feat/s7300-import` cut off `main` @ `f3a3fc5` (E6 merged,
  suite 471 green). Ground truth derived from the fixture (above). S7300-1 delegating next.
