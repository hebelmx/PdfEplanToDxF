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

## ⚠️ SCOPE CORRECTION (S7300-1, orchestrator-verified 2026-06-17) — the station has a PROFINET side too
The initial analysis read only the PROFIBUS-DP half (first ~1340 lines). The `.cfg` ALSO has a
**`CONTROLLER IOSUBSYSTEM 100, "Ethernet(1)"` (PROFINET-IO)** with **2 Keyence vision cameras**:
`STleftrear` (IV-series, IOADDRESS 1) + `strightrear` (CV-X400, IOADDRESS 2), each with
Command Control / Status Bits / Result Bits / Status Words slots (some carry `SYMBOL O` lines like
`trigger`, `Reset_Camaras`). Captured faithfully in `CfgData.io_devices` (kept separate from
`modules`/`dp_slaves`). **Implication for S7300-2/3:** the cameras are another off-module/PROFINET
element (E6 c2 analog) + PROFINET nodes on the network folio — draw real addressed I/O where it
exists, else leave on the network/off-module overview; NEVER invent. Also: **the AI8 has NO inline
`.cfg` symbols** (only an ADDRESS) → analog channels MUST join via the `.asc` PIW rows, not inline.

## Build chunks (recommended order — data first, visual LAYOUT gated at render per E6)
- [x] **S7300-1 — parsers (data-only, fully tested). DONE @ `1a4ceee`.** `src/s7300_cfg.py`
      (`parse_cfg -> CfgData`: Station, Subnets w/ real mask, local CfgModules w/ catalog/fw/
      type→(kind,points)/addr + inline SYMBOL channels, PROFIBUS DpSlaves+subslots, PROFINET
      Keyence `io_devices`; masked `?` kept) + `src/s7300_asc.py` (`parse_asc`/`physical_io`/
      `area_histogram`). **Verified from ground truth:** suite 471→**508** green (1 skip); only
      4 new files (shared renderer untouched → Rockwell byte-identical). Measured truth:
      station S7300/"SIMATIC 300(1)"; 2 subnets (Ethernet mask FFFFFF00 + PROFIBUS); local I/O
      slots 4,5=DI32 (32 sym each), 6,7=DO32 (32 sym each), 10=AI8 (0 inline sym → .asc);
      slots 8,9=CP340 comms (0 sym); DP: 5×ET200eco-16DI (16 sym each), Festo CPX (5×8DO banks,
      8 sym each + status), 3×CMMP-AS servo (telegram @528+, 0 channel sym); `.asc` area
      histogram I=176/Q=139/PIW=4/M=732/FC=82/FB=16/DB=74/T=166 (+VAT/MW/UDT/OB/SFC/QD/SFB/MD),
      **physical_io=319** of 1467; control-off cross-check (.cfg slot4 ch0 == .asc I 0.0) holds.
      37 new tests. (Note: real `.asc` counts replaced the brief's approximate estimate.)
- [x] **S7300-2 — front-end → IR (data-only, fully tested). DONE @ `50e0d3f`.**
      `src/s7300_front_end.py` + additive `plc_ir.build_s7300_project(cfg, asc) -> list[PlcProject]`
      (mirrors `build_tia_distributed_project`): per-station decomposition (local CPU 315-2 rack +
      each wired DP drop its own station), digital channels from inline `.cfg` SYMBOL lines
      (spare→RESERVA), AI8 via `.asc` PIW, catalog=real order# (masked ? kept), `offmodule_devices()`
      exposes servos+cameras WITHOUT synthesizing channels, `network_nodes=[]` (topology deferred),
      `controller_cpu="CPU 315-2 PN/DP"`. **Verified from ground truth:** suite 508→**531** green;
      only new front-end/test + additive +62/-0 plc_ir (shared renderer untouched → Rockwell
      byte-identical). **FLOOR LOCKED (re-derived): 7 stations, capacity 256 / mapped 187 /
      RESERVA 69** (local rack 136=101+35; 5× ET200eco 80=56+24; Festo CPX 40=30+10).
      **SURPRISE (verified faithful):** the AI8 maps **0** channels (all RESERVA) — the 4 `.asc`
      PIW words 372/374/736/738 are Keyence camera tags (`Camera_Result`/`Job Status`/`Job Number`),
      OUTSIDE the AI8 range 352–366; positive-control test proves the PIW join works when in range.
      Off-module exposed: 3× CMMP-AS servo telegrams (DP 16/17/18 @528+) + 2× Keyence cameras
      (PROFINET IOADDR 1/2). NEVER invented.
- [ ] **S7300-3 — CLI + render + off-module + topology + EYEBALL GATE.** `src/s7300_to_qet.py`.
      **GATED DESIGN LOCKED (Abel, 2026-06-17):**
      * **Layout = SINGLE STATION, DP drops as remote CARDS** (option 3, the compact view): ONE
        `S7300` station band — local modules (DI32×2, DO32×2, AI8) + the 5 ET200eco + Festo CPX
        all drawn as I/O card folios in one sequence, **one bornero strip, one BOM**. So S7300-3
        renders via the SINGLE-station `render_project` (logix_to_qet) — MERGE the 7-PlcProject
        list into one IR (concat io_mods/points/skipped) OR add a single-IR builder; NOT
        `render_plant` bands. (Abel chose compact over nested/flat-bands.)
      * **Scope = ALL THREE** (Abel selected all): (3a) **core** wired I/O folios + bornero + BOM
        + portada/símbología/índice/rack; (3b) **off-module section** (E6 c2 analog) for the 3
        CMMP-AS servo telegrams + 2 Keyence cameras (real ranges, per-element boxes, never
        synthesized as channels); (3c) **PROFIBUS + PROFINET topology folio** (PROFIBUS-DP line:
        CPU + 5 ET200eco + CPX + 3 servos; PROFINET Ethernet: CPU PN-IO + 2 cameras — from the
        real `.cfg` subnets/addresses). Split into 3a/3b/3c sub-cycles, each EYEBALL-gated
        (regen `_eyeball_s7300.qet`, launch QET) — mirror E6 c1/c2.
      * **BLOCKED until the data fix cycle lands** (the M1 spare-regex over-drop fix re-locks the
        floor — don't render on wrong data).
- [ ] **Adversarial review** at the phase boundary (3 lenses + general) against this tracker
      + the never-invent guardrails. Triage findings back here.

## Status log
- 2026-06-17: Tracker created; branch `feat/s7300-import` cut off `main` @ `f3a3fc5` (E6 merged,
  suite 471 green). Ground truth derived from the fixture (above). S7300-1 delegating next.
- 2026-06-17: **S7300-1 DONE & committed @ `1a4ceee`** (delegated to an isolated subagent,
  orchestrator-verified from ground truth: suite 471→508 green, only 4 new files). Subagent
  surfaced the PROFINET/Keyence scope correction (above) — recorded. Next: S7300-2 (IR front-end).
