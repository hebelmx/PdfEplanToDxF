# Handoff — next dev cycle (Document-assembly DONE → start Tier 3)

> Self-contained handoff so a **fresh agent in a new session** can continue with no
> prior context. Rewritten 2026-06-14 after **DA.5c** landed and the whole
> Document-assembly theme (DA.x) completed. Supersedes the previous DA.5c handoff.

## TL;DR — read this first

- Product: turn a Rockwell **L5X** export into a near-finished QElectroTech I/O drawing
  set. Driver = `ProductPlanEnhancement.md`. Generator = `src/logix_to_qet.py`. Tests =
  `src/test_logix_to_qet.py` (**180 tests**, stdlib unittest). Durable task list =
  `docs/TIER3-tracker.md`.
- **The Document-assembly theme (DA.1–DA.7 + DA.5a/b/c) is DONE** on branch
  **`feat/doc-assembly`** (off `main` @ `1f24259`, **NOT pushed**). WADDING_1 emits **27
  folios** in natural order: **Portada → Simbología → Alimentación → card drawings
  (101–110) → borneros (200–209) → BOM (300–302) → Historial (900)**, with prev/next
  continuation refs on the three multi-sheet sections.
- **THE NEXT TASK = Tier 3, starting `T3.1` (NO/NC correctness on symbols).** See
  `docs/TIER3-tracker.md` for the full T3.1–T3.5 specs and the recommended order
  (T3.1 → T3.2 → T3.3 → T3.4, then T3.5 on demand). **Each Tier-3 item has "Open
  decisions (gate)" — surface those to Abel before/with implementing.**
- **Two things still need Abel's word (don't act without asking):**
  1. **Eyeball DA.5c in QET** — the arrow glyphs `◄ ►` rendering, and the bottom-lane
     clearance on a *full* 16-row drawing folio (card box bottom 645 / ref lane 648).
  2. **Pushing / merging `feat/doc-assembly`.** Prior convention: ff-merge the feature
     branch into `main` per theme, then Abel pushes. **Don't push without asking.**

## What was done across this theme (all on `feat/doc-assembly`, floor intact)

| # | Item | Commit |
|---|------|--------|
| DA.1 | Title-block template sync → `assets/exxerpro.titleblock` | `44b52e0` |
| DA.2 | Reorder folios to natural drawing order (`reorder_diagrams_by_position`) | `5fa2e4d` |
| DA.5a | Designations FOLLOW the printed section page → `-K101.x … -K110.x` | `5fa2e4d` |
| DA.3 | Portada (cover) folio with title-block metadata + controller name | `2db8219` |
| DA.4 | Simbología (symbol legend) folio — real glyphs + Spanish names | `39cdd5c` |
| DA.5b | Section page shown in the cajetín (`sectionize_titleblock_page` → `%{page}`) | `7b2151b` |
| DA.6 | Hide schematic grid rulers on the non-schematic list folios | `7b2151b` |
| DA.7 | Lift the card header so the power band stops overprinting the sub-header | `ad6afe8` |
| DA.5c | prev/next continuation refs (`◄ pág. X` / `pág. Y ►`) on multi-sheet sections | `c2ba9b7` |

Gated decisions live in memory **`da-numbering-decisions`** and **`qet-generator-status`**.

### DA.5c specifics (just landed — for the QET eyeball)
- **Gated format (Abel, 2026-06-14):** arrow + SECTION page; `◄ pág. X` points back,
  `pág. Y ►` points forward; **both on the bottom lane** near the cajetín; page = the
  diagram `order` (the SECTION page the cajetín shows since DA.5b), NOT QET position.
- **Scope:** the three multi-sheet sections — **drawings 101–110, borneros 200–209,
  BOM 300–302**. Single-folio sections (Portada 0, Simbología 1, Alimentación 100,
  Historial 900) are excluded; a section that is a single folio gets none.
- **Code:** `add_continuation_refs(project)` (called in `main()` just before
  `reorder_diagrams_by_position`) groups `<diagram>`s by `CONTINUATION_RANGES` page
  bands and stamps each sheet's neighbours' pages as `<input>` text only — pure
  annotation, so element/terminal/conductor/folio counts are untouched. First sheet
  of a section omits the back ref, last omits the forward ref. Constants:
  `CONTINUATION_Y=648`, `CONTINUATION_PREV_X=60`, `CONTINUATION_NEXT_X=860`.
- **Visual risk to confirm in QET:** the lane is geometrically tight — 648 is only 3 px
  below a full 16-row card box (bottom 645) and its text descends toward the 660 frame.
  Tests assert `648 > box_bottom` and `648 < 660`, but only a QET render confirms it
  reads cleanly. Also confirm the `◄`/`►` glyphs render in QET's Sans-Serif (fallback if
  not: ASCII `<`/`>`).

## ⚠️ HARD RULES (these bit us — do not repeat)

1. **NEVER run the generator with `-o Fixtures/WADDING_1.qet`.** That is **Abel's
   working artifact** (he hand-edits the title block in QET). **Verify to a SCRATCH path**
   (`-o Fixtures/_gen_check.qet`) and parse THAT; delete the scratch `.qet`/`_bom.csv`
   after. (Memory: `never-overwrite-working-qet`.)
2. **Don't trust a subagent's `shipReady`/summary.** Re-derive every number from ground
   truth (run the generator → read stderr; run the tests; eyeball in QET). Prior cycles
   shipped real geometry bugs that passed the implementer's own short-circuiting test.
3. **Never force / never invent.** Unmatched → generic; missing/ambiguous → graceful
   fallback. Physical pins stay `"TBD"` → `__`. Multilingual DBs stay language-agnostic
   (pull display names from the DB). Python 3.10+, **stdlib only.**
4. **Public-repo hygiene:** NEVER `git add` anything under `Fixtures/` or any
   `*.L5X` / `*.qet` / `*_eplan.csv` / `*_bom.csv` / `*.pdf` / personal file. Company
   assets (`assets/exxerpro.titleblock`, the logo **`.svg`**) ARE committed; the
   `.png/.bmp/.ai` logo exports are intentionally untracked.
5. **QET caches title-block templates at startup** — fully RESTART QET to see template edits.
6. **QET numbers folios by DOCUMENT POSITION, not the `order` attribute.** To show any
   custom folio number, use a custom property (`%{page}`, filled per folio by
   `apply_titleblock` from `diagram.get("order")`) — never rely on `order` driving the
   display. The committed asset stays standard (re-syncable).
7. **Card-drawing top band is geometrically tight (~36 px).** Layout above the box must be
   a single horizontal lane; the regression test `test_subheader_clears_power_band` guards
   ≥12px clearance. **The bottom band is tight too** — a full 16-row card box bottoms at
   645 and the frame is 660; the DA.5c continuation lane (648) lives in that 15-px gap.
8. Commit footer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
   One focused commit per item; doc/handoff changes in their OWN commit.

## THE NEXT TASK — Tier 3 (start T3.1)

Full specs in `docs/TIER3-tracker.md`. Summary + the decisions to gate with Abel:

- **T3.1 NO/NC correctness on symbols** *(recommended first)* — render each matched
  contact in its true normally-open vs normally-closed state instead of a single default.
  **Gate:** where the NO/NC signal lives (a `symbol_db` field vs. a keyword rule, e.g.
  `_NC`/`PARO`/e-stop ⇒ NC); the default when ambiguous (never force — low confidence
  keeps the current default); whether NC needs a distinct `.elmt`. Touches `src/symbol_db/`,
  the matcher + `add_symbol_element` in `src/logix_to_qet.py`.
- **T3.2 Spare-point rendering** — draw a card's unused/reserved points as spare terminals
  (no device, no invented tag), counted honestly. **Floor risk** — verify 106/75/0 stays.
- **T3.3 Column pagination on card overflow** — paginate a card with more points than one
  column/sheet holds; mind the ~660-px height already near-full at 16 rows.
- **T3.4 PE / ground potentials** — PE references on devices, cross-referenced to the
  Alimentación rail; data-driven, pins stay TBD if unknown.
- **T3.5 Additional languages (IT/DE/ZH)** — pure data, demand-driven only.

## Code map (current `src/logix_to_qet.py`)

`main()` builds folios in DEPENDENCY order, stamps each with a SECTION page, adds
continuation refs, then re-sorts into document order before serialization:

- Drawing-folio loop: `build_folio(project, page, …)` with `page = SECTION_DRAWINGS+i`
  (101..110). `page` is ALSO the designation/wire-number prefix (DA.5a). Accumulates
  `bom_rows`, `drawn_cards`, `sym_counts`.
- `used = [e for e in symbols if e["id"] in sym_counts]` (shared by legend + collection).
- `build_portada_folio(project, SECTION_PORTADA=0, tb_fields, controller)` (DA.3).
- `build_symbology_folio(project, SECTION_SIMBOLOGIA=1, used)` (DA.4) — real glyphs.
- `build_supply_folios(project, SECTION_SUPPLY=100, io_mods)` — Alimentación.
- `build_bornero_folios(project, SECTION_BORNERO=200, drawn_cards)` — 200..209.
- `build_summary_folios(project, SECTION_BOM=300, bom_rows)` — 300..302.
- `build_changelog_folios(project, SECTION_CHANGELOG=900, revisions)` — Historial.
- `add_continuation_refs(project)` (DA.5c) — stamps prev/next refs on `CONTINUATION_RANGES`
  page bands (drawings/borneros/BOM); pure `<input>` text, run before the reorder.
- `reorder_diagrams_by_position(project)` — stable sort `<diagram>` by int `order` (DA.2).
- `sectionize_titleblock_page(load_titleblock_template())` then `attach_titleblocks(...)`
  (fills `%{page}` per folio, DA.5b), `build_collection`, embed templates verbatim.

The "append a folio → inherits the title block" pattern (text + shapes only) is shared by
`build_summary/changelog/supply/bornero/portada_folios`. The non-schematic list folios set
`displaycols/displayrows="false"` (DA.6); the drawing folios keep them `"true"`.

## Hard gate & guardrails (ALWAYS, after every change)

- **Validation command (SCRATCH output — NOT WADDING_1.qet):**
  `python src/logix_to_qet.py Fixtures/WADDING_1.L5X -o Fixtures/_gen_check.qet`
- **Floor that must NOT regress:** **10 drawing folios / 106 points / 75 matched / 0 FP**,
  parsed from `main()`'s stderr summary (not a proxy). Plus: **27 total folios** in the
  section order; terminal ids unique per diagram; every conductor `terminal1/2` resolves;
  no zero-length conductors; every element `type` has an embedded `<definition>`; ISO 7200
  title block on every folio with a `<property>` for every custom token (incl. `page`) so
  QET leaks no raw `%{token}`.
- Run the full suite from `src/`: `python -m unittest test_logix_to_qet` (**180 tests**).
- Pure helper + integration + regression test for every invariant you claim; assert the
  REAL invariant (full symbol extent vs the real frame; floor numbers from stderr).
- **Eyeball in QET** (fully restart it) — offer to launch QET on the scratch output.
  Abel's QET reviews caught DA.5b/DA.6/DA.7 that tests alone did not.

## Git state / how to resume

- `main` @ `1f24259` (bornero Tier 2 #6; **not pushed**).
- Branch `feat/doc-assembly` (off `main`) — all DA.1–DA.7 + DA.5a/b/c commits + doc/tracker
  commits. **NOT pushed** — ask Abel before pushing/merging.

## Kickoff prompt — paste into the new session

```
Continue the PLC → mini-EPLAN product (src/logix_to_qet.py) on branch
feat/doc-assembly. The Document-assembly theme (DA.1–DA.7 + DA.5a/b/c) is DONE and
reviewed; 27 folios in natural order with prev/next continuation refs, floor
10/106/75/0 FP, 180 tests green. NEXT = Tier 3, starting T3.1 (NO/NC correctness).

READ FIRST: docs/HANDOFF-next-cycle.md (state, HARD RULES incl. #6 QET-numbers-by-
position and #7 tight top+bottom bands, code map), docs/TIER3-tracker.md (T3.1–T3.5
specs + Open-decisions to gate), ProductPlanEnhancement.md, and memory
da-numbering-decisions + qet-generator-status.

For T3.1: gate the Open decisions with Abel (where the NO/NC signal lives — symbol_db
field vs keyword rule; the ambiguous default; whether NC needs a distinct .elmt) BEFORE
coding. Implement data-driven (never force; low confidence keeps the default), verify
from ground truth, one focused commit, eyeball in QET.

STILL PENDING ABEL: (1) eyeball DA.5c in QET — arrow glyphs ◄ ► + bottom-lane clearance
on full drawing folios; (2) pushing/merging feat/doc-assembly (NOT pushed — ask first).

HARD RULES: never -o Fixtures/WADDING_1.qet (use Fixtures/_gen_check.qet); never invent
(TBD→__, blank cells); stdlib only; never git add Fixtures/ or *.L5X/*.qet/*.pdf/*_bom.csv;
restart QET to see template edits; don't push without Abel's OK.
```

---
*Overwrite this file for the cycle after T3.1.*
