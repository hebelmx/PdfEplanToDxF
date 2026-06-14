# Handoff — next dev cycle (T3.4 DONE → Tier-3 must-do complete)

> Self-contained handoff so a **fresh agent in a new session** can continue with no
> prior context. Rewritten 2026-06-14 after **T3.4** (chassis grounding folio) landed
> on branch `feat/t3-grounding`. Supersedes the T3.1+T3.2 handoff.

## TL;DR — read this first

- Product: turn a Rockwell **L5X** export into a near-finished QElectroTech I/O drawing
  set. Driver = `ProductPlanEnhancement.md`. Generator = `src/logix_to_qet.py`. Tests =
  `src/test_logix_to_qet.py` (**226 tests**, stdlib unittest). Durable task list =
  `docs/TIER3-tracker.md`.
- **Tier 2 + the Document-assembly theme (DA.1–DA.8) + T3.1 + T3.2 are DONE and merged to
  `main`** (`main @ 89b9208`, pushed to origin 2026-06-14).
- **T3.3 (Column pagination) is DEFERRED** — [issue #1](https://github.com/hebelmx/PdfEplanToDxF/issues/1)
  (no card in the wild >32 ch).
- **T3.4 (chassis grounding folio) is DONE `778ad2b` on `feat/t3-grounding`** (branched off
  `main @ 89b9208`). Floor HELD **10/106/75/0 FP**; **226 tests** green; WADDING_1 now emits
  **32 folios** (was 30) — two new **"Puesta a tierra"** folios, one per chassis. **NOT yet
  pushed or merged. Status = `review` pending Abel's QET eyeball + the human merge gate.**
- **With T3.4 the Tier-3 MUST-DO work is complete.** Only **T3.5 (extra languages IT/DE/ZH)**
  remains and it is **demand-driven** (pure data, pull in only when a real project needs it).

## ⚠️ Things still needing Abel's word (don't act without asking)

1. **Eyeball T3.4 in QET** — the two grounding folios (`Puesta a tierra — Chasis R1 (Local)`
   at section page 099 and `Chasis R2 (RIO_RCP)` at 100), plus confirm Alimentación reads
   098 and the cards still read 101+. This is a VISUAL folio and Abel iterates visually.
   Offer to launch QET on a SCRATCH render. The layout (chassis box → FE/PE leads → Barra de
   tierra → electrode glyph) is geometry-verified inside the frame but only a render confirms
   it reads cleanly and the Spanish labels/glyph look right.
2. **Push + merge `feat/t3-grounding` → `main`** — `778ad2b` (feature) + the docs commit are
   LOCAL only. Ask before pushing and before any merge to `main`.
3. **(Earlier, still open) Eyeball T3.2 in QET** — the RESERVA spares, the grown bornero
   (incl. REM_IN_1's 2-sheet strip) and the 5-folio BOM, if not already done.

## What T3.4 did (branch `feat/t3-grounding`, floor intact)

A dedicated grounding folio **per physical chassis**, modeled on AB **1756-IN621 pp.12-14**
(`docs/1756-in621_-en-p.pdf`, "Grounding Configuration Example"). Gated decisions (Abel,
2026-06-14, memory `t3-pe-grounding-decisions`):

- **One folio PER CHASSIS.** Chassis = a distinct `rack` among `io_mods`
  (`group_chassis()`); WADDING_1 has 2 — **R1 / parent `Local` / 6 modules** and **R2 /
  parent `RIO_RCP` / 5 modules**.
- **Each chassis = a labelled box** (`Chasis R<rack> (<parent>)` + `<n> módulos`, derived
  from the parse — never invented) with **FE** (Tierra funcional) + **PE** (Tierra de
  protección) studs, gauge-labelled leads → a central **Barra de tierra** → a lead to the
  **Sistema de electrodos de tierra** (standard 3-bar earth glyph).
- **Build pattern:** new `build_grounding_folios()` + `_add_grounding_diagram()` mirror
  `build_supply_folios` — **text + shape primitives only**, empty `<elements>`/`<conductors>`
  (so they inherit the ISO 7200 title block, zero floor impact). Only `add_rect`/`add_text`
  exist (no line/circle helper) — leads are thin 2-px rects, like the supply rails.
- **Gauges CONFIGURABLE** via `project_template.json` `"grounding"` block
  (`fe_gauge`/`pe_gauge`/`electrode_gauge`), defaulting to the 1756-IN621 values (FE
  `8 AWG (8.3 mm²)`, PE `14 AWG (2.1 mm², 1.35 N·m)`, electrode `mín. 8 AWG (8.3 mm²)`).
  `PROJECT_TEMPLATE_DEFAULTS` gained a nested `"grounding"` dict; `load_project_template`
  now merges that nested dict gracefully (string sub-values only).

### Numbering — gated "keep cards at -K101.x" (Abel chose this over renumbering the cards)

The card drawings stay at section pages **101..110** (`-K101.x..-K110.x` UNCHANGED). The
power+grounding block **floats just below 101**:

- `main()` computes `n_grounding = len(group_chassis(io_mods))`, then
  **`supply_order = SECTION_SUPPLY - n_grounding`** (`SECTION_SUPPLY` constant stays 100).
  Grounding folios take `supply_order+1 .. 100` in rack order.
- WADDING_1 (n=2): **Alimentación 098 → grounding 099 (R1) → grounding 100 (R2) → cards
  101-110.** Reading order Alimentación → Puesta a tierra → cards.
- **Backward-compatible:** `n_grounding = 0` ⇒ `supply_order = 100` (unchanged).
- Grounding folios at 099/100 sit **below** the drawings continuation band (101-199), so
  `add_continuation_refs` does NOT chain them (each chassis folio is a standalone sheet).

### Verified from GROUND TRUTH (not the implementer's summary)

- `python logix_to_qet.py ../Fixtures/WADDING_1.L5X -o ../Fixtures/_gen_check.qet` (scratch,
  deleted after): floor **10 folios / 106 drawn / 75 matched / 0 FP**; 62 spares; **32
  folios**; supply order 98, grounding 99..100.
- Parsed the `.qet`: orders `[0,1,98,99,100,101..110,200..210,300..304,900]`; grounding
  titles `Puesta a tierra — Chasis R1 (Local)` / `… R2 (RIO_RCP)`; grounding folios have
  **0 elements / 0 conductors**, `titleblocktemplate="exxerpro"`, **no `%{token}` leak**, no
  duplicate terminal ids, all conductors resolve. Geometry inside the frame (box 120-640 ×
  80-200, bus y=380, electrode glyph ends y≈506, bottom label y=530 < 660).
- `python -m unittest test_logix_to_qet` from `src/`: **226 tests OK (1 skip)**. The 1 skip
  is the optional 2-column-card right-column spare-extent test — WADDING_1 has no
  right-column card so it honestly skips (a triaged minor from the T3.1/T3.2 review).

## Phase-boundary adversarial review (T3.1 + T3.2) — CLEARED 2026-06-14

A fresh skeptic re-derived everything from ground truth: **both items SOUND.** Floor held;
T3.1 is pure data (a JSON `priority 0→1`, no NO/NC logic in Python; the tiebreak resolves all
adversarial cases correctly and no fixture tag flipped); T3.2 spares carry no conductors,
blank designation/type, counted separately, no proxy-assertion smell. One **minor** test gap
(right-column spare extent on a 2-column card — triaged into the T3.4 cycle, test added but
skips) + two nits (a `ResourceWarning` from an unclosed CSV handle at `test_logix_to_qet.py`
~452; spare direction/analog derived from `mod.kind` — fine for homogeneous ControlLogix
cards). Nothing blocking.

## ⚠️ HARD RULES (these bit us — do not repeat)

1. **NEVER run the generator with `-o Fixtures/WADDING_1.qet`.** That is **Abel's working
   artifact**. **Verify to a SCRATCH path** (`-o Fixtures/_gen_check.qet`) and parse THAT;
   delete the scratch `.qet`/`_bom.csv` after. (Memory: `never-overwrite-working-qet`.)
2. **Don't trust a subagent's `shipReady`/summary.** Re-derive every number from ground
   truth (run the generator → read stderr; run the tests; parse the `.qet`; eyeball in QET).
3. **Never force / never invent.** Unmatched → generic; missing/ambiguous → graceful
   fallback. Physical pins stay `"TBD"` → `__`. Grounding gauges are standard 1756-IN621
   defaults (configurable), not per-site data; chassis labels are derived (`rack`/`parent`),
   never invented Spanish names. Multilingual DBs stay language-agnostic. Python 3.10+,
   **stdlib only.**
4. **Public-repo hygiene:** NEVER `git add` anything under `Fixtures/` or any
   `*.L5X` / `*.qet` / `*_eplan.csv` / `*_bom.csv` / `*.pdf` / personal file. Company assets
   (`assets/exxerpro.titleblock`, the logo **`.svg`**) ARE committed; the `.png/.bmp/.ai`
   logo exports are intentionally untracked (they show as `??` — leave them).
5. **QET caches title-block templates at startup** — fully RESTART QET to see template edits.
6. **QET numbers folios by DOCUMENT POSITION, not the `order` attribute.** Custom folio
   numbers go through `%{page}` (filled per folio by `apply_titleblock` from `order`).
7. **Card / folio bands are geometrically tight.** Drawing folios: power table top-right,
   box title `y1-24`, continuation lane at 648 in the 15-px gap above the 660 frame. The new
   grounding folios are roomy (one chassis per sheet) but still keep all text lifted clear of
   lines (DA.8 lesson) and inside the frame.
8. Commit footer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
   One focused commit per item; doc/handoff changes in their OWN commit.

## Code map (current `src/logix_to_qet.py`)

`main()` builds folios in DEPENDENCY order, stamps each with a SECTION page, adds
continuation refs, then re-sorts into document order before serialization:

- `n_grounding = len(group_chassis(io_mods))`; `supply_order = SECTION_SUPPLY - n_grounding`.
- Drawing-folio loop: `build_folio(project, page, …)` with `page = SECTION_DRAWINGS+i`
  (101..110); `page` is ALSO the designation/wire-number prefix (DA.5a). Accumulates
  `bom_rows`, `drawn_cards`, `sym_counts`. Spare RESERVA terminals per unused channel (T3.2).
- `build_portada_folio(SECTION_PORTADA=0)` (DA.3); `build_symbology_folio(SECTION_SIMBOLOGIA=1)`
  (DA.4); `build_supply_folios(supply_order)` — Alimentación;
  **`build_grounding_folios(supply_order+1, io_mods, gauges)`** — one Puesta a tierra folio
  per chassis (T3.4); `build_bornero_folios(SECTION_BORNERO=200)`;
  `build_summary_folios(SECTION_BOM=300)`; `build_changelog_folios(SECTION_CHANGELOG=900)`.
- `add_continuation_refs(project)` — stamps prev/next refs on the 101-199 / 200-299 / 300-899
  bands (drawings/borneros/BOM); grounding (099/100) is below the band, untouched.
- `reorder_diagrams_by_position(project)` — stable sort by int `order` (DA.2). Then
  `sectionize_titleblock_page` + `attach_titleblocks` (fills `%{page}`), `build_collection`,
  embed templates verbatim.

The "append a folio → inherits the title block" pattern (text + shapes only) is shared by
`build_summary/changelog/supply/grounding/bornero/portada_folios`. Non-schematic list folios
set `displaycols/displayrows="false"` (DA.6); drawing folios keep them `"true"`.

## Hard gate & guardrails (ALWAYS, after every change)

- **Validation (SCRATCH output — NOT WADDING_1.qet):**
  `python src/logix_to_qet.py Fixtures/WADDING_1.L5X -o Fixtures/_gen_check.qet`
- **Floor that must NOT regress:** **10 drawing folios / 106 points / 75 matched / 0 FP**,
  from `main()`'s stderr summary. Since T3.2: **62 spare RESERVA** (separate count). Since
  T3.4: **2 grounding folios** and **32 total folios** (supply at 100−n_grounding); terminal
  ids unique per diagram; every conductor resolves; no zero-length conductors; every element
  `type` has an embedded `<definition>`; ISO 7200 title block on every folio with a
  `<property>` for every custom token (incl. `page`) so QET leaks no raw `%{token}`.
- Run the full suite from `src/`: `python -m unittest test_logix_to_qet` (**226 tests**).
- **Eyeball in QET** (fully restart it) — offer to launch QET on the scratch output.

## Git state / how to resume

- **`main` @ `89b9208`** — Tier 2 + DA + T3.1 + T3.2, pushed to origin.
- **`feat/t3-grounding`** (off `main`): `778ad2b` (T3.4 feature) + the docs commit. **NOT
  pushed, NOT merged** — awaiting Abel's QET eyeball + the human merge gate.
- **T3.3 deferred** (issue #1). **T3.4 done.** Next actionable backlog = **T3.5 (demand-driven)**.

## Kickoff prompt — paste into the new session

```
Continue the PLC → mini-EPLAN product (src/logix_to_qet.py). Tier 2 + DA + T3.1 + T3.2 are
merged to main (89b9208, pushed). T3.3 DEFERRED (issue #1). T3.4 (chassis grounding folio,
778ad2b on feat/t3-grounding) is DONE & verified — floor 10/106/75/0 FP; 226 tests green;
WADDING_1 emits 32 folios (2 new "Puesta a tierra" per-chassis folios; Alimentación 098,
grounding 099/100, cards 101-110 unchanged). NOT pushed/merged — status review pending
Abel's QET eyeball + the human merge gate. Tier-3 must-do work is COMPLETE; only T3.5
(extra languages, demand-driven) remains.

READ FIRST: docs/HANDOFF-next-cycle.md (this file — state, HARD RULES, code map),
docs/TIER3-tracker.md, ProductPlanEnhancement.md, memory t3-pe-grounding-decisions +
qet-generator-status.

PENDING ABEL: (1) eyeball T3.4 in QET (the 2 grounding folios at 099/100, Alimentación 098,
cards 101+); (2) push + merge feat/t3-grounding → main; (3) if not done, eyeball T3.2.

HARD RULES: never -o Fixtures/WADDING_1.qet (use Fixtures/_gen_check.qet); never invent
(TBD→__, derived labels, configurable gauge defaults); stdlib only; never git add Fixtures/
or *.L5X/*.qet/*.pdf/*_bom.csv; restart QET to see template edits; don't push without Abel's OK.
```

---
*Overwrite this file for the cycle after the T3.4 merge / T3.5.*
