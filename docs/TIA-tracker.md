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
- [ ] **ALL OPEN ITEMS TRACKED IN GitHub issue #2** (https://github.com/hebelmx/PdfEplanToDxF/issues/2,
      sanitized for the public repo): (A) pending desktop-eyeball visual decisions [two-CPU highlight,
      NET layout, símbología, overall sign-off]; (B) queued fixes [TIA-FIX-2 cover leak; Rockwell 24V
      rail + supply test + PV classifier + comms-bridge; FP=0 real counter both pipelines]; (C) docs
      sync; (D) ALIM (blocked on power data). **Abel deferring final visual decisions to desktop
      inspection** → fixes that change validated output wait for his go.
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
