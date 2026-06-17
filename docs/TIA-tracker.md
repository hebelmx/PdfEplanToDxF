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
- [ ] **RACK+IDX** — Rack/chassis layout overview (Story 2.3) + drawing index/TOC (Story 2.2) for
      Siemens; derivable from the IR + `.aml`.
- [ ] **ALIM** — Alimentación/power one-line for Siemens (Epic 5, config-driven). Needs Abel's
      panel power data (breakers/feeders/voltages) — never-invent. Build config schema + folio.
- [ ] **Símbología vocabulary (future, low pri)** — only `push_button` matches the Siemens tag
      vocabulary today (correct never-invent). Could add a Siemens symbol dictionary (fcuv/VS_/etc.)
      with CONFIDENT mappings only. Not a bug.
- [ ] **Adversarial review** at the phase boundary (3-lens + general) vs `docs/planning/*` before
      proposing the merge gate.

## Status log
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
