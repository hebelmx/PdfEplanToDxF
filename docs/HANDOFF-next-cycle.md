# Handoff — Phase 2 (Siemens TIA path feature-complete on a branch → pending desktop eyeball + merge gate)

> Self-contained handoff so a **fresh agent in a new session** can resume with no prior context.
> Updated 2026-06-16 (supersedes the earlier "next: eyeball gate, TIA-3, S7-1500" version).
> Phase 1 (Rockwell) shipped & merged to `main`; this is **Phase 2** (multi-vendor + LLM-aided
> diagrams), driven by `docs/planning/{brief,prd,epics}.md`. **Live source of truth for the open
> work = `docs/TIA-tracker.md` + GitHub issue #2** (read both).

## TL;DR — read this first
- Product: turn a PLC program export into a near-finished **QElectroTech** drawing set.
  - Rockwell: `src/logix_to_qet.py` (renderer + Rockwell folio builders) + `src/logix_to_eplan_csv.py`
    (L5X parser). CLI: `logix_to_qet.py PROJECT.L5X -o out.qet`.
  - Vendor-neutral IR: `src/plc_ir.py` — `PlcProject` (`build_rockwell_project` / `build_tia_project`
    return the same shape). The renderer `logix_to_qet.render_project(ir, out, *, …)` is shared.
  - **Siemens TIA: `src/tia_to_qet.py` (separate CLI) + `src/tia_front_end.py` + `src/tia_aml.py`.**
    CLI: `tia_to_qet.py …_IO_Channels.xml --aml …_V15.aml -o out.qet` (the `--aml` is auto-discovered
    from a sibling `*.aml` if omitted).
  - Tests: `src/test_logix_to_qet.py` + `src/test_tia_front_end.py` + `src/test_tia_to_qet.py` +
    `src/test_tia_aml.py` = **372 tests** (stdlib unittest, 1 pre-existing skip).
- **State: the Siemens TIA S7-1200/1500 drawing-set path is FEATURE-COMPLETE on branch
  `feat/e4-tia-1200` @ `cdbc1de` (pushed? verify), NOT merged to `main`.** `main` still @ `b403f85`.
- **Everything that remains is tracked in two places — keep them in sync:**
  - `docs/TIA-tracker.md` — the durable batch tracker (decisions + per-item status + findings).
  - **GitHub issue #2** (https://github.com/hebelmx/PdfEplanToDxF/issues/2) — sanitized, public-repo;
    holds the open visual decisions, queued fixes, docs sync, and ALIM. **NEVER put plant data in it.**

## Commits this session (branch `feat/e4-tia-1200`, on top of `57c024d`)
1. `ed474f7` **TIA-3** — `src/tia_aml.py` parses the CAx/AML for module order# (`6ES7…`) + PROFINET;
   `tia_front_end` joins onto IR `Module.catalog`/`network_address` by physical name. `--aml` flag.
2. `b2fe954` **CHAN** — every card channel drawn as a box I/O point (mapped + RESERVA stubs), **both
   vendors**; all-spare cards now emit folios. **RE-BASELINED the WADDING_1 floor** (see below).
3. `3eb3e35` **NET** — whole-plant PROFINET network folio (Siemens). `build_network_folio`.
4. `62294ca` **RACK+IDX** — rack-layout (`build_rack_folio`) + drawing-index (`build_index_folio`)
   folios (Siemens); fills `Module.slot` from `.aml` `PositionNumber` (fixed "Slot None" in titles).
5. `2ae095f` **TIA-FIX-1** — fixes from the phase-boundary review (see "Reviews" below).
6. `cdbc1de` **docs** — tracker + audit findings → issue #2.

## Floors (RE-DERIVED from ground truth — do not trust a summary)
- **Rockwell WADDING_1 (re-baselined @ CHAN, Abel-eyeballed):** **11 drawing folios / 106 drawn /
  75 matched / 0 false positives / 78 RESERVA / 35 folios total.** Was 10/106/75/0/62/33 pre-CHAN.
  **matched=75 and FP=0 are the never-move invariants; drawn=106 (mapped only) also holds.** Rockwell
  output is **byte-equivalent** across CHAN/NET/RACK+IDX/TIA-FIX-1 (UUID+filename-normalized diff EMPTY).
- **Siemens IMV1 1200 station:** **23 folios** (portada, símbología, Red PROFINET, índice, rack,
  7 I/O cards, 6 bornero, 3 BOM, changelog). IR floor 88 ch / 48 drawn / 40 RESERVA. ISO 7200 title
  block on all, 0 token leaks, 0 unresolved conductors.
- **Suite: 372 tests green** (1 pre-existing skip).

## Key ground-truth CORRECTIONS established this session (memory `tia-import-findings` updated)
- The `.aml` is the **FULL plant (91 station/module entries)**, NOT "1214C + 8×ET200SP". It contains
  **TWO CPUs**: `CPU 1512SP F-1 PN` (`6ES7 512-1SK01-0AB0`, the Q100 floor station, an **S7-1500-class
  F-CPU**) AND `CPU 1214C` (`6ES7 214-1BG40-0XB0`, host .95). So "the 1500 hardware isn't in the .aml"
  was wrong — the 1500-class CPU IS present. The plant PROFINET subnet has **35 nodes** on 192.168.10.x
  (CPUs, IM 155-6 heads per station, SK TU3-PNT drives, EX260 valve terminals, BIS M-4008 RFID, printer).

## Reviews run this session (both surfaced REAL issues — verify findings yourself, don't trust verdicts)
- **TIA phase-boundary review** (5-lens Workflow vs `docs/planning/*` + tracker) → fixed in TIA-FIX-1:
  - subnet `/24` was synthesized from host IPs → now read the **real `SubnetMask`** from the `.aml`.
  - controller highlighted by hard-coded `.10` host → now from the parsed **`DeviceItemType=CPU`**
    (this surfaced the hidden 2nd CPU — both are now tagged `(CONTROLADOR)`; a pending visual call).
  - `hardware_for_station` merged all stations on a name mismatch → now returns `{}` (no contamination).
  - test holes: `_discover_aml` untested; the "no --aml" tests silently auto-discovered the fixture
    `.aml` so NET-omission was never asserted at render level; IDX duplicate-order guard. All fixed.
- **Rockwell-pipeline audit** (Abel-requested, 3-lens Workflow) → confirmed analogues, **NOT yet fixed**
  (they change validated Rockwell output → await Abel desktop eyeball). Tracked in issue #2:
  - (MAJOR) `SUPPLY_DEFAULT_RAILS` seeds a **`24V` rail no WADDING card references** (logix_to_qet.py
    :1298/:1305) + a test that locks it in.
  - (MAJOR) topology **HMI classifier 2-letter `PV` substring** (false-pos + misses real `2711P-*`, :1894).
  - (MINOR) comms-bridge list misses DNB/DHRIO/5094-AENTR (:1843).
  - (MAJOR) **`0 false positives` asserted by PROXY** (generic-terminal count) not a real counter — BOTH
    pipelines; a semantic mis-match (right count, wrong type) ships green. Fix = assert the per-type
    match breakdown from stderr.
  - (nits) magic `256` analog-word base; EPLAN `A/KF` class letters.

## ⚠️ OPEN / PENDING — all tracked in GitHub issue #2 (resolve in this order next cycle)
1. **Abel's DESKTOP eyeball** (he found the phone-preview "looks unreal", deferring final visual calls):
   - NET controller highlight: mark only the in-scope station's CPU, or all CPUs on the subnet?
   - NET layout (drop-leads only on row 0 — spine/ladder?); símbología (1 symbol type); overall sign-off.
2. ~~**No-output-change items**~~ ✅ **DONE 2026-06-16** (both shipped & pushed to `feat/e4-tia-1200`):
   - ✅ **FP=0 real counter** @ `2d2de39` — `_parse_match_breakdown` + `test_floor_match_breakdown_by_type`
     in BOTH floor tests assert the EXACT per-type dict (Rockwell 11-type=75 + 31 generic; Siemens
     push_button 2 + 46 generic). Verified the guard BITES (a 27/16 swap that keeps total=75 fails it).
     Suite 372→374; floor unchanged 11/106/75/0/78/35; no production code touched.
   - ✅ **Docs sync** @ `eb6dae0` — `.decision-log.md` 2026-06-16 E4 entry (floor re-baseline; 1200-first
     + 1512SP correction; FR-8=YES no-Openness; `.aml`-direct catalog; Siemens-first 2.2/2.3; resolves
     the 4 prd §11 open questions, power-config #3 still open) + `epics.md` reconciled (NFR-6, DoD,
     Stories 2.2/2.3/4.1/4.2).
3. **AFTER Abel's desktop go** (these CHANGE validated output → re-eyeball + re-baseline):
   - **TIA-FIX-2**: Siemens cover shows `CONTROLADOR (L5X)` (Rockwell format tag) — make vendor-aware
     (`logix_to_qet.py:1707`; Rockwell keeps it → byte-equiv).
   - Rockwell `24V` rail; supply-rail test; `PV` HMI classifier; comms-bridge families.
   - Epic-2 scope: rack/index shipped Siemens-only vs the plan's "vendor-independent" — decision pending
     (Abel leaned "check if the errors exist on Rockwell" rather than force it; revisit).
4. **ALIM** — Siemens power one-line (Epic 5, config-driven). **BLOCKED on Abel's panel power data**
   (main breaker/disconnect, feeder breakers + ratings, supply voltages 480/240/120 VAC + 24 VDC PSU,
   transformer/UPS). Build the config schema (Story 5.1) + folio (5.2); never invent.
5. **THEN propose the human merge gate** `feat/e4-tia-1200` → `main`.
6. Later: **S7-1500 path** (already mostly present — its I/O is in IO_Channels + PLCTagsS71500.xlsx
   which HAS rich English comments; its CPU is in the .aml) → then **S7-300** (memory
   `siemens-import-findings`, fixture `Fixtures/Siemens/S7300/`).

## Fixtures (GITIGNORED plant data — NEVER `git add` under `Fixtures/`)
- `Fixtures/Rockwell/WADDING_1.L5X` — Rockwell hard-gate fixture. ⚠️ **`Fixtures/Rockwell/WADDING_1.qet`
  is Abel's hand-edited WORKING file — NEVER `-o` over it** (memory `never-overwrite-working-qet`).
- `Fixtures/Siemens/TiaPortal/` — real machine IMV1_QRO001 (the 1200/Q100 target):
  `IMV1_QRO001_08AGO21_V15.aml` (CAx hardware, full plant) + `…_IO_Channels.xml` (THE point source) +
  `PLCTagsS71200.xlsx` + `PLCTagsS71500.xlsx` + a 63MB PDF. Schema in memory `tia-import-findings`.
  Scratch eyeball files `_eyeball_tia.{qet,pdf}` / `_eyeball_wadding.{qet,pdf}` live here (gitignored).

## Gate commands (run from `src/`, re-derive every number; don't trust a summary)
```
# Full suite (372 tests, ~15-150s; VM can be slow):
cd src && python -m unittest discover -p "test_*.py"

# Rockwell WADDING_1 hard gate (floor must hold 11/106/75/0, 78 RESERVA, 35 folios):
cd src && python logix_to_qet.py ../Fixtures/Rockwell/WADDING_1.L5X -o ../Fixtures/Rockwell/_gen_check.qet
#   then delete ../Fixtures/Rockwell/_gen_check.qet + _gen_check_bom.csv

# Rockwell BYTE-EQUIVALENCE (when refactoring shared code) — diff must be EMPTY:
cd src && git show HEAD:src/logix_to_qet.py > _old_lq.py
python _old_lq.py ../Fixtures/Rockwell/WADDING_1.L5X -o ../Fixtures/Rockwell/_b.qet
python logix_to_qet.py ../Fixtures/Rockwell/WADDING_1.L5X -o ../Fixtures/Rockwell/_a.qet
norm(){ sed -E 's/uuid="\{[0-9a-fA-F-]+\}"/uuid="{X}"/g; s/filename="[^"]*"/filename="{F}"/g' "$1"; }
diff <(norm ../Fixtures/Rockwell/_a.qet) <(norm ../Fixtures/Rockwell/_b.qet)   # empty = byte-equivalent
rm -f _old_lq.py ../Fixtures/Rockwell/_a.qet ../Fixtures/Rockwell/_b.qet ../Fixtures/Rockwell/_*_bom.csv

# Siemens render + structural check (23 folios; render to scratch, then delete):
cd src && python tia_to_qet.py ../Fixtures/Siemens/TiaPortal/IMV1_QRO001_08AGO21_V15_IO_Channels.xml \
  --aml ../Fixtures/Siemens/TiaPortal/IMV1_QRO001_08AGO21_V15.aml -o ../Fixtures/Siemens/TiaPortal/_chk.qet
#   assert: 23 folios; ISO title block on all; subnet from REAL SubnetMask; 2 CPUs flagged controller;
#   0 token leaks; without --aml (isolated dir) NET+RACK omitted.
```

## Remote eyeball workflow (Abel reviews from a phone via remote-control)
QET has **no CLI/headless PDF export** (confirmed; GUI-only single binary). Use the dev previewer
`tools/qet_preview.py <in.qet> <out.pdf>` (matplotlib — UNTRACKED, non-stdlib, do NOT `git add`; it
renders the diagram primitives, skips the title-block SVG) → then deliver with the **SendUserFile**
tool (NOT git — the renders are plant data; pushing them to the public repo violates hygiene).
Verify the PDF yourself (Read supports PDFs) before sending. Folio order: portada 0, símbología 1,
Red PROFINET 2, índice 3, rack 4, I/O 101+, bornero 200+, BOM 300+, changelog 900.

## ⚠️ HARD RULES (carry forward — these bit us)
1. **NEVER `-o` over `Fixtures/Rockwell/WADDING_1.qet`** (Abel's working file).
2. **Don't trust a subagent/workflow summary or `shipReady`.** Re-derive every number from ground truth
   (generator stderr; the 372 tests; parse the `.qet`; byte-equiv diff). Read individual review-lens
   findings yourself — both review passes this session found REAL issues behind clean-looking work.
3. **Never invent.** Real Siemens addresses used directly; empty `<Tag>` = spare/RESERVA; missing
   description → ""; missing catalog/PROFINET/slot/mask → blank/None; masked `?` kept; pins `TBD`→`__`.
   The reviews specifically caught synthesized values (subnet /24, `24V` rail, `.10` controller) —
   prefer reading the real source datum or leaving blank. stdlib only; multilingual DBs language-agnostic.
4. **Public-repo hygiene:** never `git add` under `Fixtures/` or any `*.L5X`/`*.qet`/`*.xlsx`/`*.xml`/
   `*.aml`/`*.pdf`/`*_bom.csv`/`*.asc`/`*.cfg`/personal file. **Issue #2 and any GitHub content must be
   SANITIZED** (no project name, IPs, station/device names). `assets/*` logos + `tools/` are untracked.
5. **One focused commit per item; feature branch → human merge gate.** Footer:
   `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## Architecture seam (so the next agent gets it fast)
`plc_ir.PlcProject` is the vendor-neutral IR; `build_rockwell_project(l5x)` and
`build_tia_project(io_channels_xml, tags_xlsx=None, aml_path=None)` both return the same shape. The
renderer `logix_to_qet.render_project(project_ir, out, *, include_hmi, no_symbols, wire_scheme,
emit_vendor_folios)` is shared. Siemens-specific folios are gated by **IR content**, not just
`emit_vendor_folios`: the NET folio renders when `PlcProject.network_nodes` is non-empty (Rockwell IR
leaves it empty); rack/index render when `source_vendor=="siemens"`. `network_nodes` tuples are
`(ip, name, type, subnet_mask, is_controller)` (tolerant accessor handles legacy 3-tuples).
`tia_aml.py` provides `parse_aml` (catalog/slot/PROFINET per module, joined by physical name; split
`[DI]/[DO]` halves share the physical module) + `profinet_nodes` (the 35-node subnet list). The
Rockwell-specific topology/grounding/supply folios stay OFF for Siemens.

## Kickoff prompt — paste into the new session
```
Continue the PLC → mini-EPLAN product, Phase 2. Branch feat/e4-tia-1200 @ cdbc1de holds the
FEATURE-COMPLETE Siemens TIA path (TIA-1/2/3 + CHAN + NET + RACK+IDX + TIA-FIX-1). 372 tests green;
Rockwell WADDING_1 floor 11/106/75/0, 78 RESERVA, 35 folios, byte-equivalent; Siemens render 23 folios.
main @ b403f85 (TIA work NOT merged).

READ FIRST: docs/TIA-tracker.md + GitHub issue #2 (the live open-items list), docs/HANDOFF-next-cycle.md
(this file), docs/planning/*, memory tia-import-findings + siemens-import-findings + never-overwrite-working-qet.

DO NEXT (issue #2 order): (1) the NO-OUTPUT-CHANGE items now — FP=0 real per-type counter (both
pipelines) + docs sync (.decision-log.md + epics.md). (2) Get Abel's DESKTOP eyeball on the Siemens
visual calls (two-CPU highlight, NET layout, símbología). (3) AFTER his go, the output-changing fixes
(TIA-FIX-2 cover (L5X) leak; Rockwell 24V rail + PV classifier + comms-bridge) — each re-eyeballed +
re-baselined. (4) Propose the merge gate feat/e4-tia-1200 → main. (5) ALIM when Abel sends power data.

HARD RULES: never -o Fixtures/Rockwell/WADDING_1.qet; never invent (read the real datum or blank);
stdlib only; never git add Fixtures/; SANITIZE all GitHub content; re-derive every number from ground
truth (stderr floors + 372 tests + parse the .qet + byte-equiv diff); read review-lens findings yourself;
one commit per item, feature branch → human merge gate. Remote eyeball = tools/qet_preview.py + SendUserFile.
```
---
*Overwrite this file at the next milestone (after the merge gate, or once ALIM + the issue-#2 fixes land).*
