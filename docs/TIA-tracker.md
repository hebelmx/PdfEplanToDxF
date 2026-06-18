# TIA S7-1200 parser tracker  (Epic 4 — Siemens TIA front-end)

> Durable source of truth for the TIA-1200 batch. Survives a context clear.
> Driven by the bmad-orchestrator loop. Mirror of the in-session Task list.
> Started 2026-06-16. Branch: `feat/e4-tia-1200`.

## Decisions (Abel, 2026-06-16)
- **1500 CAx is being re-exported ("ca02") → build the S7-1200 path FIRST.** (Resolves the
  handoff open question at HANDOFF line 44.)
- **Floor target = the REAL machine:** `Fixtures/Siemens/TiaPortal/IMV1_QRO001_*` 1200 station,
  NOT the synthetic `S71200/Project1`.
- **CLI = a SEPARATE command:** new `src/tia_to_qet.py` for the Siemens path, sharing the
  renderer + the `plc_ir.PlcProject` seam. (`logix_to_qet.py` stays Rockwell/`.L5X`.)
- **F-DQ1500 [DI] all-spare half → DRAW a RESERVA-only folio** (Abel, 2026-06-16, post-eyeball
  gate). All 88 channels must be represented. **SUBSUMED by the CHAN decision below.**
- **CHAN: draw ALL channels as card-box I/O points — BOTH vendors** (Abel, 2026-06-16, post-PDF
  eyeball). Today the card box draws a left-side I/O stub only for MAPPED channels; spares appear
  only as strip terminals (`X1:n RESERVA`) on the right. Abel wants every channel of a DI16/DQ16
  drawn as a box I/O point, unused ones as RESERVA stubs, so the module shows its full capacity.
  Applies to **Rockwell too** → **RE-BASELINES the WADDING_1 floor** (drawn/RESERVA/folio counts
  rise; **matched MUST stay 75, false positives MUST stay 0** — spares are never matched). Re-eyeball
  of the Rockwell set required before locking the new floor. Subsumes the F-DQ1500 [DI] item.
- **Roadmap = recommended sequence** (Abel, 2026-06-16): CHAN fix → Network/topology folio
  (PROFINET from `.aml`) → Rack layout + drawing index → Alimentación/power (config-driven; Abel
  supplies panel power data when we reach it). Eyeball gate after each.

## Ground-truth fixture floor (IMV1 1200 station — verified by orchestrator from IO_Channels.xml)
Station "Q100-Cooling1/UV", Rack_0, **6 modules / 88 channels / 48 tagged / 40 spare**:
| Module    | ch | tagged | spare | addresses        | kind |
|-----------|----|--------|-------|------------------|------|
| F-DI150   | 16 | 16     | 0     | %I150.x / %I151.x | DI (F) |
| F-DI156   | 16 | 4      | 12    | %I156.x / %I157.x | DI (F) |
| DI10_11   | 16 | 12     | 4     | %I10.x / %I11.x   | DI |
| DI12_13   | 16 | 5      | 11    | %I12.x / %I13.x   | DI |
| F-DQ1500  | 8  | 3      | 5     | %Q1500.x + %I1500.x | DQ/DI (F) |
| DQ10_11   | 16 | 8      | 8     | %Q10.x / %Q11.x   | DQ |

## Non-negotiable guardrails (every item)
- **Rockwell WADDING_1 floor (RE-BASELINED @ CHAN, 2026-06-16, Abel-eyeballed): 11 drawing folios
  / 106 drawn / 75 matched / 0 false positives, 78 RESERVA, 35 folios total.** (Was 10/106/75/0,
  62 RESERVA, 33 folios before CHAN drew all channels incl. all-spare cards.) matched=75 + FP=0 are
  the invariants that must NEVER move; drawn=106 (mapped only) also holds.
- Real absolute Siemens addresses used directly — **never synthesize/invent** an address.
- Empty `<Tag>` = spare (RESERVA), mirroring the Rockwell spare semantics.
- stdlib only (zipfile+xml.etree for xlsx); never `git add` under `Fixtures/`.
- `source_vendor="siemens"`; same `PlcProject` shape → renderer needs no vendor branch.

## ⚠️ EPIC E6 — FULL-PLANT DISTRIBUTED I/O (the big reframe, Abel 2026-06-17)
**Fundamental scope correction (Abel, reviewing with fresh eyes):** modern S7-1500 plants have
**local I/O at the CPU + many DISTRIBUTED I/O drops** (often mixed families / other brands), unlike
the ControlLogix case where both 1756 chassis were in one L5X. **We were drawing only ONE station.**
- **Ground truth (from the `.aml`, orchestrator-verified):** the plant has **9 I/O stations / 75 I/O
  modules / 636 channels** — Q100 (1512SP CPU-local @ .10, 6 mod) + Q200–Q800 (IM155-6 ET200SP drops
  @ .20–.80) + the **1214C** (@ .95, 2 mod). `IO_Channels.xml` exported **only Q100** (88 ch) → we
  render ~14% of the real I/O.
- **CORRECTION to the earlier handoff:** the 1214C's I/O **IS** in the `.aml` (@ .95); nothing is
  blocked on an export. The data for the WHOLE plant is present: `.aml` = every station's modules +
  `StartAddress`/`Length`/`IoType` (verified: e.g. `DI40_41` start 40 len 16 Input → `%I40.0..%I41.7`)
  + the tag tables = names/descriptions/`%`addresses (`PLCTagsS71500` for the 1500 stations,
  `PLCTagsS71200` for the .95 1214C).
- **FEASIBILITY PROVEN (orchestrator):** computing channel addresses from the `.aml` and joining by
  address to the tag table resolves real tags+descriptions on non-Q100 stations (Q400 DI 15/16,
  Q400 DQ 12/16, Q500 10/16; unmapped = spares/RESERVA). The full set is reconstructable.
- **Plan (each station its OWN section, per Abel):**
  1. Extend `tia_aml.parse_aml` to extract per-module `StartAddress`/`Length`/`IoType` (addresses).
  2. New front-end path: build the IR from the FULL `.aml` (all stations), enumerate each module's
     channels by address, join **per-station to the right tag table** (reuse E6 foundation coverage
     logic: 1500 stations → S71500, .95 → S71200), unmapped address = RESERVA. Carry station + owning
     PLC on each point (the `controller_cpu`/`scope` seam is in place).
  3. Renderer: **one section per station** (local CPU rack + each IM155-6 drop + the 1214C), I/O
     folios + bornero per station, PLC-/station-labeled. **Section/numbering scheme = GATED visual
     design decision (Abel).**
  4. F-module addressing (safety, e.g. F-DI Length 48 for 8 ch) + analog word addressing need care.
  5. Mixed-brand PROFINET nodes (EX260 valve terminals, SK drives, BIS RFID) — on the NET folio
     already; draw I/O only where real addresses exist, else leave on the network overview (never invent).
- **LOCKED DESIGN (Abel, 2026-06-17):**
  * **Section block per station** (divider/rack + I/O folios + bornero), contiguous, in plant order.
  * **Heaviest PLC first** (capability ordinal; 1512SP >> 1214C) → the 1512SP's 8 stations (Q100
    CPU-local first, then Q200–Q800 drops), THEN the 1214C's station.
  * **PLC→I/O ownership comes from the DATA, never guessed; ASK the user if ambiguous.**
  * **General + extendable**, not fixture-hardcoded; "not so worried about polymorphic, but extendable."
- **OWNERSHIP RESOLVED data-drivenly (orchestrator-verified):** a station's I/O addresses resolve in
  exactly ONE PLC's program tag table (Q100–Q800 → all in `PLCTagsS71500` / the 1512SP @ .10, 0 in
  1200; the .95 station → all in `PLCTagsS71200` / the 1214C, 0 in 1500). **Zero overlap → unambiguous.**
  This reuses the E6 coverage selector (now per-station for BOTH the tag-join AND ownership); keep the
  ask-on-ambiguity fallback for any future station that splits across both tables.
  The `.aml` has NO explicit ownership attribute (only network addresses + 537 PROFINET InternalLinks),
  so tag-table coverage is the ownership source of truth.
- **BUILD CHUNKS:** (data) extend `parse_aml` for per-module addresses → build the multi-station IR
  (all 9 stations, ownership + channel→address→tag join + RESERVA, ordered heaviest-PLC-first); THEN
  (render) section-block-per-station folios — the section/numbering scheme + a desktop eyeball gate.
- **E6 foundation DONE @ `bc0e5b0`** (branch `feat/e6-s71500-descriptions`): per-station tag-table
  selection + descriptions + symbol match/suppression + `controller_cpu` seam. **NOT merged** — hold
  the merge until the distributed-I/O build lands so `main` never ships the misleading single-station set.
- **E6(a) DONE @ `6135125`** — `parse_aml` carries `addresses` = list[(io_type,start_byte,length_bits)]
  per module (suite 419). Ordered list entries; F-modules carry 2 ranges; never invented.
- **GROUND-TRUTH RE-DERIVATION before E6(b) (orchestrator, 2026-06-17 — corrects the prior
  "feasibility proven" summary, which hid the F-module trap):**
  * Real stations in the `.aml` (9): `Q100-Cooling1/UV` (1512SP CPU-local) + `Q200 Q300 Q400 Q500
    Q600 Q700 Q700_1` (IM155-6 drops) + `S7-1200 station_1` (1214C onboard @ .95). NB the names are
    `Q700_1`, not "Q800".
  * Tag tables: `PLCTagsS71500.xlsx` 1167 tags (759 parse to I/Q addrs, rich English comments);
    `PLCTagsS71200.xlsx` 143 tags (21 parse, comments empty, auto-names like "I0.4").
  * **OWNERSHIP by coverage is unambiguous:** Q100–Q700_1 resolve 100% in S71500 / 0 in S71200;
    `S7-1200 station_1` resolves in S71200 (21) >> S71500 (10). Zero genuine overlap.
  * **CAPACITY / channel-count rules (VALIDATED: reproduce Abel's approved Q100 floor 88/48/40 EXACTLY):**
    - Standard digital DI/DQ Nx: capacity = ExtIf count = `Length` bits; single module.
    - **F-DI 8x: capacity = 2×ExtIf = 16, ALL in the Input area** (byte = 8 device values, byte+1 =
      8 `VS_*`/"Vsupply" value-status bits — already suppressed to generic by the foundation). The
      `.aml` `Length`=48 is PROFIsafe-inflated; do NOT enumerate by Length.
    - **F-DQ 4x: SPLIT — DO part = ExtIf %Q[start].0..3, DI-readback part = ExtIf %I[start].0..3**
      (8 total; readback usually all-spare). Matches the existing [DO]+[DI] split.
    - Analog Nx: capacity = ExtIf words = `Length`/16; addresses %{area}W[start+2i].
    - **1214C (S7-1200 station_1): conservative** — enumerate only standard onboard low-address I/O
      (`%I0`/`%Q0` digital, `%IW64` analog); the `%ID1000…` HSC/pulse **double-word** ranges parse to
      None → emit ONLY real mapped tags there, never synthesize digital spares (never-invent).
  * **The doc figure "636 ch / 75 modules" was a RAW-ExtIf sum that UNDERCOUNTS F-modules** (it gives
    Q100=68, but the approved Q100 is 88). The user-facing total (with the F-DI ×2 / F-DQ split) is
    higher; recompute it from the IR and update the figure when E6(b) lands.
  * **Spares are a COUNT, not addresses** (CHAN decision): mapped channels carry real tag+address from
    the tag table; RESERVA = capacity − mapped drawn as anonymous stubs (pin `__`, no address) — this
    is the "sidesteps F-module per-channel addressing" the design intended.
- **E6(b) DONE @ `f4c1de5`** — `build_tia_distributed_project(aml)` → ordered `list[PlcProject]`,
  9 stations / 776 ch / 549 mapped / 227 RESERVA; Q100 reproduces approved 88/48/40 (suite 433).
  Existing single-station path untouched; Rockwell floor untouched.
- **E6(b) ADVERSARIAL REVIEW (3 lenses, 2026-06-17) — triage.** Strongest +evidence: Q100 synthesis is
  BYTE-IDENTICAL to the real `IO_Channels.xml` point source (faithful). No BLOCKERs. Findings:
  * [FIX] MAJOR — ordering + `controller_cpu` for the 7 drops derive from the wrong key (drops get
    `cpu_rank(None)`, rescued only because Q100 is present; remove Q100 → 1200 sorts ahead of 1500
    drops). Derive owning-PLC CPU/class from the OWNER LABEL's CPU-local station; populate the drops'
    `controller_cpu` (needed for E6c labeling). Data-driven, never-invented.
  * [FIX] MAJOR(latent) — digital capacity uses `range(channels)`, ignores range `Length`; clamp so a
    garbage `channels` can't synthesize addresses past the declared range.
  * [FIX] MAJOR(latent) — analog heuristic `Length==16*channels` false-positives a 1-ch digital w/ a
    16-bit range; guard `channels>=2` (keeps real `SM 1232 AQ2`, channels=2).
  * [FIX] MINOR — `_synthesize_cpu_onboard` hardcodes `(start,length)` triples (fails silently for other
    1200 CPUs); drive from declared ranges `start<1000`. + MINOR modules-dict collision guard; docstring
    "tag-sweep" correction; missing-file docstring/guard.
  * [SCOPE, not a bug] 231 S71500 tags (`%IW>=1000` VFD telegrams, RFID, drive status on mixed-brand
    PROFINET nodes) map to NO I/O module → correctly NOT drawn as wired I/O (design pt 5: stay on the
    NET folio; never invent a module). Document scope + add a transparent per-station off-module count;
    SURFACE to Abel at the E6c gate. (So plant addressed I/O = 776 module ch + ~231 off-module telegram.)
  * CLEAN (corroborated): per-station arithmetic, SM1232 analog fix, no wrong-module/cross-station join,
    descriptions all real, `no_symbol` suppression correct, ownership unambiguous, masked-`?`/blanks.
- **E6(b-fix) DONE @ `b8d4afc`** — ordering/controller_cpu from owner label, digital capacity clamp,
  analog channels>=2 guard, CPU-onboard range-driven, docstring/collision guards. Suite 439; Q100
  byte-identical; plant 776/549/227; all 9 stations carry their owning CPU; both render gates held.
- **E6(c) GATED VISUAL DECISIONS (Abel, 2026-06-17) — LOCKED:**
  1. **Layout = PER-STATION NUMERIC BANDS.** Each station its own band: I/O 101+/201+/…/9xx (1214C),
     bornero 12xx/22xx/…, preceded by a station divider. Plant folios: portada 0, símbología 1,
     Red PROFINET 2, índice 3, rack 4 (per station), then per-station I/O+bornero bands, BOM 300+,
     changelog 900. índice spans all stations; BOM aggregates all.
  2. **Q100 = UNIFORM** — build ALL 9 stations (incl. Q100) through the new distributed `.aml` path
     (proven byte-identical to approved Q100). Single-station IO_Channels CLI stays available.
  3. **Section label = station + owning-PLC CPU + a FUNCTIONAL "tag name"**, derived from the data when
     available (the `.aml` device Name carries it: Q100 → "Cooling1/UV"; Q200–Q700_1 are bare), ELSE
     **semantically derived** from the station's tag descriptions — CONSERVATIVE (a real common token,
     blank if nothing clear; never invent). Eyeball-gate the derived names.
  4. **OFF-MODULE PROFINET I/O = a NEW SECTION (chunk c2), grouped by FUNCTION.** The ~231 non-1:1
     addressed tags (VFD telegrams `%IW>=1000`, RFID, barcode, drive status on SK drives / EX260 valve
     terminals / BIS RFID) are NOT on a Siemens I/O module → draw them in subsections of a new section
     grouped by function (drives, RFID, barcode, valves…), each element with its real address range +
     tags, as a finishable starting point for the user. (Abel also OK with adding an address-range
     column on the Red PROFINET folio, but PREFERS the new grouped-by-function section.) Needs a data
     investigation first: link telegram addresses → PROFINET device/function (the `.aml` drives/RFID
     are devices but their telegram ranges are NOT captured as I/O modules — investigate before design).
- **E6(c1) DONE & EYEBALL-APPROVED (Abel, 2026-06-17) @ `6db8e91`.** `src/render_plant.py` +
  `tia_to_qet --distributed`. 191 folios (76 I/O + 86 bornero + matter), 9 per-station bands
  (front 0–50, bands 100–900, back BOM 1000+/changelog 1900 — moved off 300/900 to clear the
  Q300/1214C bands), 0 order collisions / 0 token leaks / 0 unresolved conductors, ISO on all.
  `logix_to_qet.py` untouched → Rockwell byte-identical; single-station Siemens unchanged (22/48/40).
  Suite 454. **Abel: "looks good — proceed to c2."** Functional-name decision: **keep auto-derived +
  blanks** (Q100 Cooling1/UV real; Q200 Infrareds, Q300 Coating derived; rest blank — never invent;
  non-device VS_/Vsupply points excluded from derivation).

## Backlog (recommended order)
- [x] **TIA-1** — `build_tia_project()` IR front-end. DONE @ `3be4655`. `src/tia_front_end.py` +
      `plc_ir.build_tia_project()`. IR ground truth: vendor=siemens, station "Q100-Cooling1/UV",
      7 IR modules (F-DQ1500 split [DO]+[DI]), 88 ch / 48 points / 40 spares / 0 unparsable.
      Descriptions all "" (PLCTagsS71200.xlsx Comment column empty — never-invent, correct).
      Suite 287 green; WADDING_1 holds 10/106/75/0, 62 RESERVA. (Orchestrator fixed one bad
      round-trip test example — %Q11.7 is a spare, swapped for %Q11.3.)
- [x] **TIA-2** — `src/tia_to_qet.py` (separate Siemens command) + render. DONE @ (commit below).
      Extracted shared `render_project(ir, out, *, emit_vendor_folios=...)`; Rockwell **byte-equivalent**
      (UUID+filename-normalized diff EMPTY, re-derived by orchestrator) + floor 10/106/75/0 holds.
      Siemens set = 18 folios (6 I/O cards + portada + símbología + 6 bornero + 3 BOM + changelog),
      ISO title block on all, 0 token leaks, 0 unresolved conductors. **Omits topology + grounding +
      supply/Alimentación** (all Rockwell-specific OR underivable from IO_Channels — never invent;
      reconciles the earlier status-log note that wrongly kept Alimentación). Floor 48 drawn/40
      skipped/88 ch. ⚠️ NOTE for review: the all-spare `F-DQ1500 [DI]` card is skipped (Rockwell
      "folio per card with mapped tags" rule) → 36 RESERVA drawn vs 40 at IR; 4 unused safety-input
      spares not drawn. Suite 300 green (+13). **Pending Abel eyeball gate.**
- [x] **TIA-3** — `.aml` hardware map (Story 4.1). DONE @ (commit below). `src/tia_aml.py`
      (+`test_tia_aml.py`, 20 tests) parses the CAx/AML for order# (`6ES7…`) + PROFINET
      `NetworkAddress`; `tia_front_end` joins onto IR `Module.catalog`/`network_address` by
      physical name (split `[DI]/[DO]` halves share the physical module); `tia_to_qet --aml`
      flag + sibling auto-discovery. **Verified from ground truth (orchestrator):** suite 320
      green (1 pre-existing skip); WADDING_1 floor holds 10/106/75/0, 62 RESERVA; Siemens render
      18 folios, floor 48/40/88, order numbers present in BOM. Never-invent preserved (no match →
      catalog ``/addr None; masked `?` kept; pins TBD).
      ⚠️ **CORRECTION (verified):** the `.aml` is the FULL plant (91 entries), NOT "1214C +
      8×ET200SP". The Q100-Cooling1/UV station CPU is **`CPU 1512SP F-1 PN` (`6ES7 512-1SK01-0AB0`),
      an S7-1500-class F-CPU** — both `CPU 1214C` and the 1512SP F-1 exist in the file. So our
      "1200 floor machine" is really a 1500-class ET200SP system. Handoff line "1500 hardware not
      in the .aml" is wrong for this station.
- [x] **CHAN** — draw ALL channels as card-box I/O points, BOTH vendors (decision above).
      Spare loop (`logix_to_qet.py` build_folio) now draws each unused channel as a FULL box I/O
      point: card-side I/O terminal at the column x (generic name IN-n/OUT-n per card direction,
      pin `__`, no addr/desc, NO device symbol — borne_2 generic terminal) + a card->strip conductor
      (no wire number, no address — never invented) + the RESERVA strip terminal. The drawing-folio
      gate in `render_project` no longer skips cards with no mapped points, so ALL-SPARE cards/halves
      emit a folio (and a bornero); this subsumes the F-DQ1500 [DI] item. **WADDING_1 floor
      RE-BASELINED from ground truth** (matched=75 + FP=0 HELD):
      OLD→NEW: drawing folios 10→11; points drawn 106→106; points skipped 80→80; matched 75→75;
      false positives 0→0 (31 generic→31 generic); RESERVA 62→78; total folios 33→35.
      Siemens render OLD→NEW: folios 6→7 (F-DQ1500 [DI] now renders); points drawn 48→48;
      skipped 40→40; RESERVA 36→40 (all 88 channels represented). ISO title block on all folios,
      0 token leaks, 0 unresolved conductors on both sets. Suite green (320 tests, 1 pre-existing
      skip). **DONE & committed @ `b2fe954`** — Abel-eyeballed both sets via PDF preview (approved
      2026-06-16); floor RE-BASELINED in guardrails above.
- [~] **NET** — whole-plant PROFINET network folio for Siemens (scope locked by Abel: ALL 35 nodes
      on 192.168.10.x, Q100 CPU highlighted). `tia_aml.profinet_nodes()` resolves (ip, name, type)
      per node from the `.aml` (35 nodes: Q100 CPU 1512SP F-1, IM 155-6 heads Q200-Q800, SK TU3-PNT
      drives, EX260 valve terminals, BIS M-4008 RFID, CPU 1214C @ .95). Topology folio = text+shape
      primitives (empty elements/conductors), gated ON for Siemens when nodes present, omitted w/o
      `--aml`. **DONE @ (commit below).** Folio builder `build_network_folio` in `logix_to_qet`,
      gated at `SECTION_TOPOLOGY` (order 2) by `PlcProject.network_nodes` (Rockwell IR empty → off,
      independent of `emit_vendor_folios`). Layout: subnet bus + 5-col×7-row node grid, controller
      (.10 host + CPU TypeName, data-driven) boxed + `(CONTROLADOR)`. **Verified from ground truth:**
      suite 320→336 (+16); WADDING_1 floor UNCHANGED 11/106/75/0/78/35 + 0 PROFINET refs on Rockwell;
      Siemens 20→21 folios, 35 nodes, ISO title block on all, 0 token leaks; omits w/o `--aml`.
      Abel preview-PDF sent for eyeball (proceeding per his "move fast"; tweakable before merge gate).
      **DECISION LOCKED 2026-06-17 (desktop re-confirm): tag ALL real CPUs — both Q100 1512SP @ .10
      and PLC_1 1214C @ .95 keep (CONTROLADOR); whole-plant view, no code change. NET item = DONE.**
- [~] **RACK+IDX** — Rack/chassis layout overview (Story 2.3) + drawing index/TOC (Story 2.2) for
      Siemens; derivable from the IR + `.aml`. Rack uses real slot from `.aml` `PositionNumber`
      (522 present); bonus: fill `Module.slot` → fixes "Slot None" in I/O titles. Both Siemens-only,
      additive (Rockwell floor untouched). **DONE @ (commit below).** New `build_rack_folio`
      (order 4, 'Disposición del rack', 7 modules slot order 2,3,4,4,5,6,7) + `build_index_folio`
      (order 3, 'Índice de planos', all 23 folios). **Verified from ground truth:** suite 336→357;
      Rockwell **BYTE-EQUIVALENT** (normalized diff EMPTY) + floor 11/106/75/0/78/35; Siemens 21→23
      folios; I/O titles now show real slots R0.S2–S7 (no "Slot None"); 0 token leaks. Preview sent.
- [x] **ALIM** — Alimentación/power one-line for Siemens (Epic 5, config-driven). DONE on branch
      `feat/e5-alim` (@ `026080c` + label tweak `310150d`), desktop-confirmed by Abel. New
      `src/power_config.py` (stdlib json loader) + `build_power_folio` (`logix_to_qet.py`,
      `SECTION_ALIM=5`, visual-only vertical one-line, gated `source_vendor=="siemens" AND
      power_config` → Rockwell byte-equiv) + `tia_to_qet --power-config PATH` flag +
      `docs/examples/power_config.example.json` (synthetic schema example). **TEST values
      (Abel-supplied test assumptions, NOT real plant data — see memory `alim-test-power-config`):
      120 VAC single system; input CB 2 A; PS 10 A; output CB 10 A; no transformer/UPS.** One-line:
      `120 VAC → [CB 2A] → [PS 10A] → [CB 10A] → control/PLC loads`; absent transformer/ups omitted
      (never-invent). Verified: suite 390→397 green; Rockwell BYTE-EQUIVALENT + floor
      11/106/75/0/78/35; Siemens 22→23 folios, Alimentación order 5 (0 elements/0 conductors),
      listed in the drawing index. **Pending the human merge gate `feat/e5-alim` → main.**
- [ ] **Símbología vocabulary (future, low pri)** — only `push_button` matches the Siemens tag
      vocabulary today (correct never-invent). Could add a Siemens symbol dictionary (fcuv/VS_/etc.)
      with CONFIDENT mappings only. Not a bug.
- [x] **Adversarial review** (phase boundary, 5 refute-lenses Workflow) DONE 2026-06-16. No floor
      blockers (matched=75/FP=0/byte-equiv all confirmed). Findings triaged below.
- [~] **TIA-FIX-1** — address review findings (never-invent + test holes). IN FLIGHT 2026-06-16:
      - N1 (MAJOR): `_subnet_label` fabricates `/24` from host IPs; real `SubnetMask` (255.255.255.0)
        is in the `.aml` next to each `NetworkAddress`. → read real mask, label only when uniform,
        else bare "PROFINET" (no invented octet). Fixes the doubled "PROFINET — Red PROFINET" nit too.
      - N2 (MINOR): controller flagged by hard-coded `.10` host; use the parsed `DeviceItemType=CPU`
        (dropped in `profinet_nodes`) instead. Fix docstring.
      - N3 (MINOR): cross-station contamination — `hardware_for_station` merges all stations
        last-write-wins on name mismatch → skip join (catalog ''/addr None), never bind wrong station.
      - T1 (MAJOR): `_discover_aml` untested → add the 2 tests (mirror `_discover_tags`).
      - T2 (MAJOR): "no --aml" render tests silently auto-discover the fixture `.aml`; NET render-side
        omission never asserted → isolated-dir omission test (assert `PROFINET_TITLE` absent + no `red
        PN`) + positive title assertions for NET/RACK/IDX (not just count 23).
      - IDX-guard (MINOR): dedup/guard duplicate diagram orders in `_index_entries`.
      Guardrail: WADDING_1 floor 11/106/75/0/78/35 + Rockwell byte-equiv must hold (fixes are
      Siemens-path); Siemens still 23 folios (subnet label stays /24 for IMV1, from real mask).
      **DONE @ (commit below).** Verified from ground truth: suite 357→372; Rockwell BYTE-EQUIVALENT
      + floor held; Siemens 23 folios, subnet "192.168.10.0/24" from REAL SubnetMask, controllers
      flagged via DeviceItemType=CPU = **2 real CPUs** (Q100 1512SP @ .10 AND PLC_1 1214C @ .95 —
      the old `.10` heuristic HID the 2nd CPU; correction surfaces both). N3 returns {} on station
      mismatch. All 6 findings + tests.
- [x] **TIA-FIX-2 (Siemens vendor leak)** — DONE @ `d52163e` (branch `feat/e5-output-fixes`).
      `build_portada_folio` gained keyword `source_format` (default `"L5X"` → Rockwell byte-equiv);
      `render_project` passes `"TIA"` for Siemens. Siemens cover now `CONTROLADOR (TIA)`, zero `(L5X)`
      leak; suite 385→388; Rockwell BYTE-EQUIVALENT + floor 11/106/75/0/78/35. (Only user-visible
      change this cycle → quick Siemens eyeball before the E5 merge.)
- [ ] **TIA-DOCS** (orchestrator) — append E4 entry to `docs/planning/.decision-log.md` (floor
      RE-BASELINE 10/106/75/0/62/33 → 11/106/75/0/78/35 w/ matched=75+FP=0 invariants; 1200-first;
      Siemens-only folio scope; `.aml`-direct catalog vs curated module_db for Story 4.1; resolve the
      4 carried open questions) + reconcile `epics.md` Stories 2.2/2.3 (Siemens-first) & 4.1.
- [x] **Rockwell-pipeline audit** (Abel-requested, 3-lens Workflow) DONE 2026-06-16. Confirmed real
      analogues of the TIA defect classes: (MAJOR) `SUPPLY_DEFAULT_RAILS` seeds a **`24V` rail no
      WADDING card references** (collect_supply_rails, logix_to_qet.py:1298/1305) + the test locks it
      in; (MAJOR) topology **HMI classifier 2-letter `PV` substring** (false-pos + misses real
      `2711P-*`, :1894); (MINOR) comms-bridge list misses DNB/DHRIO/5094-AENTR (:1843); (MAJOR)
      **FP=0 asserted by proxy** (generic count) not a real counter — both pipelines; (nits) magic 256
      analog base, EPLAN `A/KF` letters. Rockwell fixes CHANGE the validated Rockwell output → need
      Abel desktop eyeball before doing them.
      **RESOLVED 2026-06-17 (E5 cycle):** #2 PV-classifier + #3 comms-bridge families fixed
      BYTE-EQUIVALENTLY @ `b401555` (neither active on WADDING — its HMI is literal `PanelView`, its
      bridges are CNB). #1 24V rail = WON'T-FIX (Abel: keep the standard rail template). FP=0 real
      counter already shipped @ `2d2de39`. Remaining nits (magic 256, A/KF letters) still open, low-pri.
- [x] **GitHub issue #2 CLOSED 2026-06-17** — all substantive items DONE or decided + merged to `main`:
      (A) visual decisions [two-CPU highlight=ALL CPUs, NET layout=EYE-1/2, símbología accepted, sign-off
      passed]; (B) fixes [TIA-FIX-2 cover leak ✓, PV classifier ✓, comms-bridge ✓, FP=0 counter ✓; 24V
      rail + supply test = WON'T-FIX (keep template)]; (C) docs sync ✓; (D) ALIM ✓ (shipped @ `56c6de3`
      with Abel's test power data). The 3 surviving low-pri nits → **GitHub issue #3** (non-blocking).
- [ ] **TIA-DEFERRED (nits)** — >32-ch column overflow assert+test (latent); NET inter-row spine
      (cosmetic); two-column positional test drive from a synthetic 32-ch module (currently skips).

## Status log
- 2026-06-17: **E5 OUTPUT-FIXES CYCLE (branch `feat/e5-output-fixes`, off `main` @ 586555e).**
  Three commits, all verified from ground truth:
  - **TIA-FIX-2** @ `d52163e` — vendor-aware cover controller tag. `build_portada_folio` gained a
    keyword `source_format` (default `"L5X"`); `render_project` passes `"TIA"` for Siemens. Rockwell
    **BYTE-EQUIVALENT** (default unchanged); Siemens cover now `CONTROLADOR (TIA)`, zero `(L5X)` leak.
    Suite 385→388. **(Only user-visible change in this cycle → wants a quick Siemens eyeball.)**
  - **RW-CLASSIFY** @ `b401555` (findings #2+#3) — **BYTE-EQUIVALENT** on WADDING (verified):
    #2 `classify_node` HMI no longer uses the bare 2-letter `"PV"` substring (false-positived on
    e.g. `1492-SPV-*` AND missed real `2711P-*`); now matches literal `PANELVIEW` or AB family
    prefix `2711`/`2715`. WADDING's real HMI catalog is the literal `PanelView` → still hmi.
    #3 added comms-bridge families `AENT`→EtherNet/IP, `DNB`→DeviceNet, `DHRIO`→DH+/RIO (WADDING
    has none → additive). Suite 388→390.
  - **Finding #1 (24V rail) = WON'T-FIX (Abel decision 2026-06-17): keep the standard rail template
    L1/N/L+/24V/0V/PE** as an intentional panel skeleton, even though WADDING cards reference only
    L1/N/L+/0V (the DC card names its rails L+/0V; L+ IS the 24V positive). No code change; the
    existing supply-rail test correctly locks in the intended template.
  **NEXT: quick Siemens cover eyeball → merge `feat/e5-output-fixes` → main; then ALIM (blocked on
  Abel's panel power data); then S7-1500/S7-300.**
- 2026-06-17: **DESKTOP RE-CONFIRM (Abel) → PASS. Both decisions gated & locked; MERGED to `main`.**
  Abel opened both fresh eyeball sets in real QET-desktop (`_eyeball_wadding.qet` 35 folios +
  `_eyeball_tia.qet` 22 folios, regenerated at HEAD post EYE-1..4) → "they look good." Decisions:
  - **NET controller highlight = TAG ALL REAL CPUs** (current behavior LOCKED): both the in-scope
    Q100 `1512SP F-1` @ .10 and the separate PLC_1 `1214C` @ .95 keep `(CONTROLADOR)` — correct for a
    whole-plant PROFINET view, data-driven from `DeviceItemType=CPU`. **No code change.**
  - **Merge order = MERGE NOW, output-changing fixes AFTER** on a fresh branch (each re-eyeballed).
  Ground truth re-derived before the merge: suite **385 green** (1 skip); Rockwell floor
  **11/106/75/0/78/35** + exact per-type breakdown; Siemens **22 folios / 35 PROFINET nodes / ISO on
  all**. `feat/e4-tia-1200` (17 commits) merged `--no-ff` → `main`. símbología (1 type) + split-card
  per-half bornero accepted as-is. **Next: output-changing fixes (TIA-FIX-2 + Rockwell 24V/PV/comms)
  on a new branch; then ALIM when Abel sends panel power data.**
- 2026-06-17: **DESKTOP EYEBALL (Abel) → 4 visual fixes DONE & pushed** (branch
  `feat/e4-tia-1200`). All verified from ground truth (suite 385 green; Rockwell
  BYTE-EQUIVALENT + floor 11/106/75/0/78/35; Siemens render checked; merged/NET
  folios read from PDF). **Pending Abel's desktop RE-confirm before the merge gate.**
  - **EYE-1+EYE-2** @ `bc06b57` (Red PROFINET, Siemens-only): node-box 3rd text
    line lifted off the bottom border (y+6/24/42) + controller header in FONT_TEXT
    (was bold) + `_fit_text` ellipsis clip so long names don't spill; and every row
    now hangs off the bus (per-column drop + inter-row spine) — rows 1-6 were
    floating. +3 positional tests.
  - **EYE-4** @ `13a0698` (I/O folios, both vendors): widened the row-text lane by
    +70 (STRIP_X_OFF 235→305, SYM_X_OFF 290→360) so long AB tags clear the bornera
    (X1:n) + symbol — Abel chose "widen" over truncate. +70 is the safe max (left
    sym 501<530 right-box; right sym 981<1010 frame). Rockwell counts unchanged
    (positions shift → not byte-equiv, expected). +1 symbol-extent frame proof.
  - **EYE-3** @ `7811cff` (Siemens, delegated dev-cycle + orchestrator-verified):
    split safety card F-DQ1500 [DO]+[DI] now renders side-by-side on ONE folio
    (`build_split_card_folio` + `_is_split_sibling_pair`, build_folio untouched →
    Rockwell byte-equiv). Siemens 23→22 folios; bornero stays per-half (merge
    deferred). Title kind-marker derived from real kinds (orchestrator fixed a
    hard-coded "[DO+DI]" never-invent smell). +7 tests. **LIMITATION:** pairs only
    CONSECUTIVE split siblings (the IMV1 case); non-adjacent halves degrade
    gracefully to two folios (revisit if a fixture needs it).
  - Spare points: Abel confirmed OK (no change).
- 2026-06-16: **FP-FIX DONE & committed @ `2d2de39`, pushed.** No-output-change item
  from issue #2 (B): the floor tests asserted only the matched TOTAL → a semantic
  mis-classification (right count, wrong type) shipped green. Added
  `_parse_match_breakdown` + `test_floor_match_breakdown_by_type` to BOTH floor tests,
  asserting the EXACT per-type dict (Rockwell 11-type =75 + 31 generic; Siemens
  push_button 2 + 46 generic). Test-only; verified from ground truth: suite 372→374
  green; WADDING_1 floor unchanged 11/106/75/0/78/35 (no production code touched).
- 2026-06-16: tracker created; decisions gated & locked; TIA-1 delegated.
- 2026-06-16: Eyeball render refreshed (`_eyeball_tia.qet`, 18 folios) + QET launched for Abel.
  F-DQ1500 [DI] gate RESOLVED → "draw RESERVA-only folio" (new item TIA-2b, queued behind TIA-3).
  TIA-3 delegated to background dev agent (independent of the F-DQ1500 render change).
- 2026-06-16: TIA-1 DONE & committed @ `3be4655`, verified from ground truth. Next: TIA-2.
  ⚠️ TIA-2 folio-scope decision (orchestrator, never-invent): render the VENDOR-NEUTRAL folios
  (cover, símbología, I/O point folios, bornero, BOM, changelog, Alimentación) and GRACEFULLY
  OMIT the Rockwell-specific topology (ControlNet/EtherNet classification) + AB-1756 grounding
  folios for the Siemens path — to be revisited as Siemens-specific folios later. Confirm main()
  coupling before delegating (Explore).
