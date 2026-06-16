# Handoff — Phase 2 in progress (E2.1 topology DONE → next: network addresses → IR → S7-300)

> Self-contained handoff so a **fresh agent in a new session** can continue with no prior
> context. Written 2026-06-15. Supersedes the post-T3.4 handoff. The product MVP (Phase 1)
> is shipped; this is **Phase 2** (multi-vendor + LLM-aided diagrams), driven by
> `docs/planning/{brief,prd,epics}.md`.

## TL;DR — read this first

- Product: turn a PLC program export into a near-finished **QElectroTech** drawing set.
  Generator = `src/logix_to_qet.py`; parser/domain-model = `src/logix_to_eplan_csv.py`
  (`l2e`); tests = `src/test_logix_to_qet.py` (**247 tests**, stdlib unittest).
- **Phase 1 (Rockwell L5X → 32-folio set) is COMPLETE and merged.** Docs: `README.md` §3 +
  `docs/logix-to-qet-guide.md`.
- **Phase 2 plan is captured as a BMAD chain:** `docs/planning/brief.md` → `prd.md`
  (FR-1…FR-14) → `epics.md` (E1–E5) + `.decision-log.md`. **Read these** — they hold the
  whole business case (Siemens multi-vendor, LLM config-driven folios, quick-win folios).
- **DONE this session:** **E2.1 network/communications topology folio** ("Red de
  comunicaciones", section page 2) — chassis-grouped layout, merged to `main` @ `18519cc`,
  pushed. Floor held; now **33 folios** (was 32). The **S7-300 Siemens spike** ran on a real
  sample → **strong GO** (see memory `siemens-import-findings`).
- **`main` @ `18519cc` == `origin/main`.** Branch `feat/e2-network-topology` is ff-merged
  (safe to delete).
- **DONE 2026-06-16:** network addresses on the topology folio (inline `Nodo N` from the L5X
  non-ICP port `Address`; floor held; 256 tests) — `feat/e2-network-addresses` @ `0c6f80c`,
  pushed, **awaiting human merge-to-main gate.**
- **TIA fixtures LANDED 2026-06-16** (`Fixtures/Siemens/TiaPortal/`, project IMV1_QRO001):
  `.aml` CAx hardware + `IO_Channels.xml` (pre-joined addr↔tag) + `PLCTags*.xlsx` + 63MB PDF.
  Characterized — see memory `tia-import-findings`. **Decision: TIA is the FIRST Siemens
  target** (cleaner than S7-300), after the IR refactor.
- **NEXT, in order:** (1) **Epic 1 — the vendor-neutral IR refactor** (Abel chose IR-first;
  prerequisite for all parsers); (2) **TIA S7-1200/1500 parser** (samples in hand, TIA-first);
  (3) **S7-300 parser** (spike GO, fixture in hand). ⚠️ TIA `.aml` has only the S7-1200 +
  8×ET200SP — the S7-1500 hardware export is missing; confirm with Abel in the TIA cycle.

## ⚠️ Fixtures were REORGANIZED by vendor (2026-06-14) — paths changed

- `Fixtures/Rockwell/` — `WADDING_1.{L5X,ACD,AML,L5K,RDF,qet,pdf,...}` + an `abel-backup`.
  **The fixture is now `Fixtures/Rockwell/WADDING_1.L5X`.** **Abel's WORKING file is
  `Fixtures/Rockwell/WADDING_1.qet` — NEVER `-o` over it** (memory `never-overwrite-working-qet`).
- `Fixtures/Siemens/S7300/` — the first Siemens sample: `brpl2twin.txt.asc` (symbol table)
  + `brpl2twin.txt.cfg` (HW config). More Siemens samples (1200/1500 CAx + PDF) coming.
- The test suite was fixed for the move: `_wadding_fixture()` in the test file resolves
  `Fixtures/Rockwell/WADDING_1.L5X` (fallback to the old flat path). **Before that fix the
  move made the floor tests SILENTLY SKIP** — if you ever see `skipped` jump, the fixture
  path is wrong, not the floor passing.
- `Fixtures/` is **gitignored plant data** — never commit anything under it (now also
  Siemens `*.asc`/`*.cfg`/`*.aml`/`*.pdf`).

## Hard gate (run after every change)

```
cd src && python logix_to_qet.py ../Fixtures/Rockwell/WADDING_1.L5X -o ../Fixtures/Rockwell/_gen_check.qet
```
Floor that must NOT regress (from `main()`'s stderr): **10 drawing folios / 106 points /
75 matched / 0 false positives**, 62 RESERVA spares, **33 total folios** (now incl. the
topology folio at order 2). On the `.qet`: terminal ids unique; conductors resolve; every
element `type` has an embedded `<definition>`; ISO 7200 title block on every folio; no raw
`%{token}` in folio **properties** (the `%{...}` inside the embedded `<titleblocktemplate>`
are native QET vars — expected, not a leak). Then run `python -m unittest test_logix_to_qet`
from `src/` (**247 tests**) and **delete the scratch `_gen_check.qet`/`_bom.csv`/`.pdf`**.
(Note: the VM has been very slow — the suite takes ~60-90 s; that's environmental.)

## THE NEXT TASKS (in order)

### 1. Add network addresses to the topology folio  ← do this first
Abel reviewed the topology folio in QET and it "looks much better"; the one thing missing is
**the network address of each node** (ControlNet node number / EtherNet IP / device address).
- **First confirm the data exists** in the L5X — likely on each `<Module>`'s `Ports/Port`
  (`Address`/`NodeAddress`) or the comms/connection elements. `l2e` may not parse it today;
  you may need to extend the parse (and the `Module`/IR to carry an optional `address`).
  **NEVER invent an address** — if absent, show nothing for that node (blank), like every
  other never-invent fallback.
- Render the address as an extra line in each node's row (topology folio is at
  `build_topology_folio` / `_add_topology_diagram`, chassis-grouped). Keep it inside the box,
  no strike-through, floor unchanged.
- It is a **visual folio** — Abel iterates visually. Verify floor + positional tests, then
  **render to a scratch `.qet` and launch QET** for his eyeball (workflow below).

### 2. Epic 1 — vendor-neutral IR refactor  (PRD FR-1/2/3; epics E1)
Promote `l2e`'s implicit, Rockwell-named domain model into an explicit `PlcProject` IR the
renderer consumes. **Pure enabling refactor — Rockwell output must stay byte-equivalent;
floor holds.** This is the prerequisite for the S7-300 parser. Abel explicitly chose
**IR-first** over building the parser against the current model.

### 3. S7-300 parser  (PRD FR-9/FR-10; epics E3) — spike is GO, fixture in hand
Build a Siemens S7-300 front-end producing the IR, against `Fixtures/Siemens/S7300/`.
**Full spike findings are in memory `siemens-import-findings`** — summary:
- `.asc` symbol table: fixed-width `symbol · operand(I/Q/M/…) · address · datatype · comment`.
  Use `I`/`Q` rows for physical I/O; filter `M`/`FC`/`FB`/`DB`/`T`/`C`.
- `.cfg` HW config (CFG text, `FILEVERSION "3.2"`): `RACK n, SLOT m, "<6ES7 order#>", "<type>"`
  lines where the **type string encodes kind + point count** (`DI32xDC24V`, `DO32xDC24V/0.5A`,
  `AI8x12Bit`), each followed by `LOCAL_IN/OUT_ADDRESSES` (byte range). PROFIBUS-DP slaves are
  `DPSUBSYSTEM … SLOT … "<x output bytes, y input bytes>"`. **Join `.asc`↔`.cfg` on the byte
  address.** Masked `?` digits in order numbers are **version-independent wildcards — keep
  as-is, never fill them.** Symbol-optional: tags from `.asc` when present, else address-only.

### 4. (later) E4 — TIA S7-1200/1500  (PRD FR-11; epics E4) — sample-gated
Tag-table XML is the clean tag source. Hardware via **CAx/AML export** — which needs Abel's
user in the Windows "Siemens TIA Openness" group (he's granting it; this is a permission on
*his* manual export, NOT a runtime dep — our parser just reads the `.aml` with stdlib). If
CAx works, 1200/1500 hardware is clean like the `.cfg`; else a print-report PDF is the
human-read reference for a curated Siemens `module_db`. **Samples LANDED 2026-06-16 and the
CAx `.aml` parses cleanly (stdlib xml.etree) — see memory `tia-import-findings`; this is now
the FIRST Siemens target, ahead of S7-300.**

## QET eyeball workflow (how Abel reviews visual folios)
1. Render to a SCRATCH path: `python logix_to_qet.py ../Fixtures/Rockwell/WADDING_1.L5X -o ../Fixtures/Rockwell/_eyeball.qet`
2. Launch QET: `"/c/Program Files/QElectroTech/bin/qelectrotech.exe" "E:\Dynamic\PdfEplanToDxF\Fixtures\Rockwell\_eyeball.qet" &` (background).
3. Abel prints to PDF and tells you the page; **`Read` that PDF page** to see the render
   (`Read` supports PDFs via the `pages` param). Page N = the Nth folio in document order
   (Portada 0, Simbología 1, **topología 2**, …). QET has **no headless PDF export** CLI.

## Topology folio code map (current)
`build_topology_folio(project, start_order, controller, modules)` (returns 1) called in
`main()` right after `build_symbology_folio`; `SECTION_TOPOLOGY = 2`. Helpers:
`build_topology_tree` (classified graph), `classify_node` (controller/bridge/hmi/io/generic by
kind+catalog pattern), `topology_root` (self-parented root), `topology_protocol`
(CNB/CN2→ControlNet, EN..→EtherNet/IP), `build_topology_chassis` (groups the full tree into
physical chassis), `_add_chassis_box`/`_add_hmi_box`/`_add_topology_drop`/`_add_topology_diagram`.
**Layout:** one enclosing box PER CHASSIS with plain-text module rows (no per-module boxes),
a full-width network bus, drops at box edges. Uses the FULL `modules` dict (not `io_mods`) so
it includes the controller, comms bridges and HMI. Mirrors `build_grounding_folios` (text +
shape primitives only, empty `<elements>`/`<conductors>`).

## ⚠️ HARD RULES (carry forward — these bit us)
1. **NEVER `-o Fixtures/Rockwell/WADDING_1.qet`** (Abel's working file). Verify to a scratch
   path; delete it after.
2. **Don't trust a subagent's summary/`shipReady`.** Re-derive every number from ground truth
   (run the generator → stderr; run the tests; parse the `.qet`). Verified twice this session.
3. **Never invent.** Unmatched → generic; uncertain → graceful fallback; pins `TBD`→`__`;
   Siemens catalogs keep masked `?`; missing network address → blank. Multilingual DBs stay
   language-agnostic. Python 3.10+, **stdlib only**.
4. **Public-repo hygiene:** never `git add` under `Fixtures/` or any
   `*.L5X`/`*.qet`/`*_bom.csv`/`*.pdf`/`*.asc`/`*.cfg`/`*.aml`/personal file. The `assets/*.png/.bmp/.ai`
   logo exports are intentionally untracked (`??`) — leave them.
5. **QET caches title-block templates at startup** — restart QET to see `.titleblock` edits.
6. **Verify from ground truth; one focused commit per item; feature branch → human merge gate.**
   Footer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## Deferred housekeeping (small, do when convenient)
- `docs/logix-to-qet-guide.md` §9 and a couple of planning-doc gate commands still cite the
  old `Fixtures/WADDING_1.L5X`/`.qet` path — repoint to `Fixtures/Rockwell/`.
- Fold the Siemens spike specifics (now in memory `siemens-import-findings`) into
  `docs/planning/{brief,prd,epics}` when next on a clean main.

## Git state / how to resume
- **`main` @ `18519cc` == `origin/main`** — Phase-1 + planning chain + fixture-test-fix +
  E2.1 topology, all pushed. `feat/e2-network-topology` is ff-merged (deletable).
- Memory to read: `siemens-import-findings`, `never-overwrite-working-qet` (updated paths),
  `qet-generator-status`, `bmad-orchestration`. Plan: `docs/planning/`.

## Kickoff prompt — paste into the new session
```
Continue the PLC → mini-EPLAN product (src/logix_to_qet.py), Phase 2. main @ 18519cc ==
origin/main; 247 tests; floor 10 drawing folios/106/75/0; WADDING_1 emits 33 folios incl.
the new "Red de comunicaciones" topology folio (order 2). Phase-1 done; Phase-2 plan in
docs/planning/{brief,prd,epics}.md.

READ FIRST: docs/HANDOFF-next-cycle.md (this file — fixture reorg to Fixtures/Rockwell,
gate command, code map, HARD RULES), docs/planning/* , and memory siemens-import-findings +
never-overwrite-working-qet.

NEXT IN ORDER: (1) add NETWORK ADDRESSES to the topology folio — first confirm the L5X
carries them (Module Ports/Port Address?), extend l2e/IR if needed, never invent (blank if
absent); visual folio → QET eyeball. (2) Epic 1: vendor-neutral PlcProject IR refactor,
byte-equivalent Rockwell output, floor holds (Abel chose IR-first). (3) S7-300 parser against
Fixtures/Siemens/S7300/ (.cfg+.asc join, spike is GO — see the memory).

HARD RULES: never -o Fixtures/Rockwell/WADDING_1.qet (use a scratch path); never invent;
stdlib only; never git add Fixtures/ (incl. Siemens *.asc/*.cfg/*.aml/*.pdf); verify every
result from ground truth (stderr floor + 247 tests + parse the .qet); restart QET for
template edits; one commit per item, feature branch → human merge gate.
```
---
*Overwrite this file for the cycle after the network-address + IR work.*
