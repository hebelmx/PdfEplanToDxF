# Handoff — Phase 2 (TIA S7-1200 parser SHIPPED → next: eyeball gate, TIA-3 .aml hardware, then S7-1500)

> Self-contained handoff so a **fresh agent in a new session** can continue with no prior
> context. Updated 2026-06-16 (supersedes the earlier 2026-06-16 "next: TIA parser" version).
> Phase 1 (Rockwell) is shipped; this is **Phase 2** (multi-vendor + LLM-aided diagrams),
> driven by `docs/planning/{brief,prd,epics}.md`. The TIA-1200 batch is tracked in
> `docs/TIA-tracker.md` (read it — it is the live source of truth for this work).

## TL;DR — read this first

- Product: turn a PLC program export into a near-finished **QElectroTech** drawing set.
  Rockwell generator = `src/logix_to_qet.py`; vendor-neutral IR = `src/plc_ir.py`
  (`PlcProject`); Rockwell parser = `src/logix_to_eplan_csv.py` (`l2e`); **Siemens TIA
  front-end = `src/tia_front_end.py` + `src/tia_to_qet.py`** (NEW this session). Tests =
  `src/test_logix_to_qet.py` + `src/test_tia_front_end.py` + `src/test_tia_to_qet.py`
  (**300 tests**, stdlib unittest).
- **Phase 1 (Rockwell L5X → 33-folio set) is COMPLETE & merged to `main`.**
- **DONE THIS SESSION — the TIA S7-1200 path now generates a drawing set.** Two commits on
  branch **`feat/e4-tia-1200`** (pushed), NOT yet merged to `main`:
  1. **TIA-1** `3be4655` — `plc_ir.build_tia_project()` + `src/tia_front_end.py`: parses the
     TIA `IO_Channels.xml` (the real absolute `%I/%Q/%IW` address source) into the same
     vendor-neutral `Module`/`IoPoint` IR, joining `PLCTags*.xlsx` for descriptions. Returns
     `PlcProject(source_vendor="siemens")`. **IR-only, no rendering.**
  2. **TIA-2** `1584828` — `src/tia_to_qet.py` (a SEPARATE Siemens command) + a shared
     `render_project()` extracted from `logix_to_qet.main()`. Renders the Siemens set.
     **Rockwell output proven BYTE-EQUIVALENT** (UUID+filename-normalized diff empty, re-derived
     from ground truth vs the TIA-1 baseline).
- **Floors (all re-derived from ground truth, not trusted from a subagent summary):**
  - **Rockwell WADDING_1 UNCHANGED:** 10 drawing folios / 106 points / 75 matched / 0 false
    positives, 62 RESERVA, 33 folios.
  - **Siemens IMV1 1200 station:** IR = 7 modules / 88 channels / 48 points / 40 spares / 0
    unparsable. Render = **18 folios** (6 I/O cards + portada + símbología + 6 bornero + 3 BOM
    + changelog), ISO 7200 title block on all, 0 token leaks, 0 unresolved conductors; 48 drawn
    / 40 skipped; topology/grounding/supply correctly ABSENT.
- **`main` @ `b403f85` == `origin/main`** (Phase-1 + planning + E2.1 topology + network
  addresses + Epic-1 IR). The TIA work is on **`feat/e4-tia-1200` @ `1584828`** (pushed),
  awaiting the eyeball gate + the rest of the batch before a human merge gate.

## Decisions locked this session (Abel, 2026-06-16) — see `docs/TIA-tracker.md`
- **The missing S7-1500 CAx is being re-exported by Abel ("ca02") → build the S7-1200 path
  FIRST.** (Resolved the prior handoff's open question.)
- **Floor target = the REAL machine** `Fixtures/Siemens/TiaPortal/IMV1_QRO001_*` 1200 station,
  not the synthetic `Fixtures/Siemens/S71200/Project1_*`.
- **CLI = a SEPARATE command** `src/tia_to_qet.py` (Rockwell stays `logix_to_qet.py`/`.L5X`).
- **Siemens folio scope (never-invent):** render the vendor-neutral set (portada, símbología,
  per-card I/O folios, bornero, BOM, changelog, ISO title block); **OMIT topology + grounding +
  supply/Alimentación** — Rockwell-specific (ControlNet/EtherNet, AB-1756) or underivable from
  IO_Channels (power rails). Gated via `render_project(..., emit_vendor_folios=False)` AND
  `source_vendor=="rockwell"`.

## ⚠️ OPEN / PENDING (resolve early next cycle)
1. **Abel's EYEBALL GATE is still pending** for the Siemens render. A scratch render is at
   `Fixtures/Siemens/TiaPortal/_eyeball_tia.qet` (gitignored). Re-render + relaunch QET if needed
   (workflow below). Get his visual sign-off on the I/O folios / F-module names / `[DO]`/`[DI]`
   split labels / Siemens addresses before treating TIA-2 as visually final.
2. **F-DQ1500 [DI] all-spare card decision (pending Abel).** The safety output module F-DQ1500
   carries `%Q1500.x` outputs AND 4 unused `%I1500.x` spare inputs; the IR splits it by
   direction and the all-spare `[DI]` half is currently **SKIPPED** (Rockwell "folio only for
   cards with mapped tags" rule) → 36 RESERVA drawn vs 40 at IR; the 4 unused safety-input
   spares are not drawn anywhere. Options offered: keep skipping (current) / draw a RESERVA-only
   folio so all 88 channels are represented. Abel to decide (was deciding-after-eyeball).
3. **Descriptions are empty** for the 1200 set — `PLCTagsS71200.xlsx`'s Comment column is empty,
   so only 2 symbols match (push_button) and 46 are generic terminals. This is correct
   never-invent behavior, NOT a bug. (The S7-1500 tag table `PLCTagsS71500.xlsx` HAS rich
   English comments — descriptions will populate once the 1500 path lands.)
4. **S7-1500 CAx ("ca02") inbound from Abel** — when it lands in `Fixtures/Siemens/TiaPortal/`,
   the 1500 hardware path can be built (its I/O is already in `IO_Channels.xml` +
   `PLCTagsS71500.xlsx`; only the rack/module hardware export was missing).

## THE NEXT TASKS (in order) — tracker: `docs/TIA-tracker.md`
1. **Eyeball gate** (above) — Abel's visual sign-off on the Siemens render; resolve the
   F-DQ1500 [DI] decision.
2. **TIA-3 — `.aml` hardware map (Story 4.1).** Parse `IMV1_QRO001_08AGO21_V15.aml` (CAx/AML,
   stdlib `xml.etree`) for each module's catalog/order# (`<Attribute Name="TypeIdentifier">`
   `<Value>OrderNumber:6ES7…`), `TypeName`, kind/points, and PROFINET `NetworkAddress`
   (192.168.10.x). Fill `Module.catalog`/`network_address` so the Siemens BOM module rows carry
   real order numbers. `module_db` schema; pins `"TBD"`; masked `?` digits kept; nothing invented.
   ⚠️ the `.aml` has ONLY the S7-1200 CPU 1214C (`6ES7 214-1BG40-0XB0`) + 8×ET200SP — the 1500
   hardware is NOT in it (see open item 4). Join `.aml`↔`IO_Channels.xml` on module name/address.
3. **Adversarial review** (phase boundary) — fan out `bmad-code-review` (Blind Hunter + Edge
   Case Hunter + Acceptance Auditor) **and** `bmad-review-adversarial-general` against
   `docs/planning/*` + `docs/TIA-tracker.md`. Triage findings into the tracker. Then propose the
   **human merge gate** for `feat/e4-tia-1200` → `main`.
4. **S7-1500 path** (when the ca02 CAx lands) → then **S7-300** (spike GO, memory
   `siemens-import-findings`, fixture `Fixtures/Siemens/S7300/`).

## Fixtures (GITIGNORED plant data — NEVER `git add` under `Fixtures/`)
- `Fixtures/Rockwell/WADDING_1.L5X` — the Rockwell hard-gate fixture. **`Fixtures/Rockwell/
  WADDING_1.qet` is Abel's hand-edited WORKING file — NEVER `-o` over it** (memory
  `never-overwrite-working-qet`). Verify to a scratch path, delete after.
- `Fixtures/Siemens/TiaPortal/` — project IMV1_QRO001 (real machine, the 1200 target):
  `IMV1_QRO001_08AGO21_V15.aml` (CAx hardware, 1200 only) + `…_IO_Channels.xml` (THE point
  source) + `PLCTagsS71200.xlsx` + `PLCTagsS71500.xlsx` + a 63MB PDF. Schema in memory
  `tia-import-findings` (⚠️ that memory was CORRECTED this session: `sharedStrings.xml` is NOT
  empty — the xlsx parser must resolve `t="s"` refs).
- `Fixtures/Siemens/S71200/Project1_*` — a smaller synthetic 1200 sample (not the floor target).
- `Fixtures/Siemens/S7300/` — `brpl2twin.txt.{asc,cfg}` for the later S7-300 path.

## Gate commands (run from `src/`, re-derive every number; don't trust a summary)
```
# Rockwell WADDING_1 hard gate (floor must hold 10/106/75/0):
cd src && python logix_to_qet.py ../Fixtures/Rockwell/WADDING_1.L5X -o ../Fixtures/Rockwell/_gen_check.qet
#   then delete ../Fixtures/Rockwell/_gen_check.qet + _gen_check_bom.csv

# Rockwell BYTE-EQUIVALENCE (when refactoring shared code) — diff must be EMPTY:
git show <pre-change-rev>:src/logix_to_qet.py > src/_old.py    # run from src/
python _old.py ../Fixtures/Rockwell/WADDING_1.L5X -o ../Fixtures/Rockwell/_b.qet
python logix_to_qet.py ../Fixtures/Rockwell/WADDING_1.L5X -o ../Fixtures/Rockwell/_a.qet
sed -E 's/uuid="\{[0-9a-fA-F-]+\}"/uuid="{X}"/g; s/filename="[^"]*"/filename="{F}"/g' _b... ; diff …   # empty

# Siemens render + structural check (render to scratch, then delete):
cd src && python tia_to_qet.py ../Fixtures/Siemens/TiaPortal/IMV1_QRO001_08AGO21_V15_IO_Channels.xml -o ../Fixtures/Siemens/TiaPortal/_chk.qet
#   assert: 18 folios; ISO title block on all; 0 %{token} leaks outside <titleblocktemplate>;
#   0 unresolved conductor endpoints; topology/grounding/supply ABSENT; floor 48 drawn/40 skipped/88 ch.

# Full suite (300 tests, ~15-90s; VM can be slow):
cd src && python -m unittest discover -p "test_*.py"
```

## QET eyeball workflow (how Abel reviews visual folios)
1. Render to a SCRATCH path (above). 2. Launch QET (background):
   `"/c/Program Files/QElectroTech/bin/qelectrotech.exe" "E:\Dynamic\PdfEplanToDxF\Fixtures\Siemens\TiaPortal\_eyeball_tia.qet" &`
3. Abel prints to PDF and tells you the page; **`Read` that PDF page** (Read supports PDFs via
   `pages`). Siemens folio order: portada 0, símbología 1, then the 6 I/O cards, 6 bornero, 3 BOM,
   changelog. QET has no headless PDF export; QET caches title-block templates at startup (restart
   QET to see `.titleblock` edits).

## ⚠️ HARD RULES (carry forward — these bit us)
1. **NEVER `-o` over `Fixtures/Rockwell/WADDING_1.qet`** (Abel's working file).
2. **Don't trust a subagent's summary / `shipReady`.** Re-derive every number from ground truth
   (run the generator → stderr; run the tests; parse the `.qet`; diff for byte-equivalence). Done
   for both TIA items this session — caught a bad test example (`%Q11.7` is a spare) and a doc
   inconsistency (Alimentación) that way.
3. **Never invent.** Real absolute Siemens addresses used directly; empty `<Tag>` = spare/RESERVA;
   missing description → ""; missing catalog/PROFINET → blank; Siemens catalogs keep masked `?`;
   pins `TBD`→`__`. Multilingual DBs language-agnostic. Python 3.10+, **stdlib only**.
4. **Public-repo hygiene:** never `git add` under `Fixtures/` or any
   `*.L5X`/`*.qet`/`*.xlsx`/`*.xml`/`*.aml`/`*.pdf`/`*_bom.csv`/`*.asc`/`*.cfg`/personal file. The
   `assets/*.png/.bmp/.ai` logo exports are intentionally untracked (`??`) — leave them.
5. **One focused commit per item; feature branch → human merge gate.** Footer:
   `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## Architecture seam (so the next agent gets it fast)
`plc_ir.PlcProject` is the vendor-neutral IR. `build_rockwell_project(l5x)` and
`build_tia_project(io_channels_xml, tags_xlsx=None)` BOTH return the same shape. The renderer is
`logix_to_qet.render_project(project_ir, out_path, *, include_hmi, no_symbols, wire_scheme,
emit_vendor_folios)` — `logix_to_qet.main()` (Rockwell CLI) and `tia_to_qet.main()` (Siemens CLI)
both call it. Siemens leaves `controller_tags`/`program_tags` empty and `catalog`/`slot`/
`network_address` blank/None — the renderer's I/O + neutral folios read only fields the Siemens IR
populates; the 3 vendor folios are gated off. TIA-3 will fill `catalog`/`network_address` from the
`.aml`. The renderer needs no further vendor branch for the core set.

## Kickoff prompt — paste into the new session
```
Continue the PLC → mini-EPLAN product, Phase 2, TIA S7-1200 batch. Branch feat/e4-tia-1200 @
1584828 (pushed) holds TIA-1 (plc_ir.build_tia_project + src/tia_front_end.py) and TIA-2
(src/tia_to_qet.py + shared render_project). 300 tests green. Rockwell WADDING_1 floor
10/106/75/0 byte-equivalent; Siemens IMV1 1200 render = 18 folios, floor 48/40/88, vendor
folios omitted. main @ b403f85 == origin/main (TIA work NOT merged yet).

READ FIRST: docs/TIA-tracker.md (live tracker + decisions + the F-DQ1500 [DI] open item),
docs/HANDOFF-next-cycle.md (this file), docs/planning/*, and memory tia-import-findings +
siemens-import-findings + never-overwrite-working-qet.

NEXT: (1) get Abel's EYEBALL sign-off on the Siemens render + resolve the F-DQ1500 [DI]
all-spare-card decision (render Fixtures/Siemens/TiaPortal/_eyeball_tia.qet, launch QET).
(2) TIA-3 — parse IMV1_QRO001_08AGO21_V15.aml for module catalog/order#/PROFINET, fill
Module.catalog/network_address (stdlib; .aml has the 1200 CPU + 8×ET200SP only). (3) phase-
boundary adversarial review vs docs/planning/* then propose the human merge gate. (4) S7-1500
path when Abel's ca02 CAx export lands, then S7-300.

HARD RULES: never -o Fixtures/Rockwell/WADDING_1.qet; never invent; stdlib only; never git add
Fixtures/; re-derive every number from ground truth (stderr floors + 300 tests + parse the .qet
+ byte-equiv diff); one commit per item, feature branch → human merge gate.
```
---
*Overwrite this file for the cycle after the TIA-3 + eyeball + adversarial-review work.*
