# Handoff — next dev cycle (finish DA.5c, then Tier 3)

> Self-contained handoff so a **fresh agent in a new session** can continue with no
> prior context. Rewritten 2026-06-14 after the Document-assembly theme (DA.x) was
> implemented and reviewed in QET. Supersedes the previous DA.x handoff.

## TL;DR — read this first

- Product: turn a Rockwell **L5X** export into a near-finished QElectroTech I/O drawing
  set. Driver = `ProductPlanEnhancement.md`. Generator = `src/logix_to_qet.py`. Tests =
  `src/test_logix_to_qet.py` (**173 tests**, stdlib unittest). Durable task list =
  `docs/TIER3-tracker.md`.
- **Document-assembly theme (DA.x) is essentially DONE** on branch
  **`feat/doc-assembly`** (off `main` @ `1f24259`, **NOT pushed**). WADDING_1 emits **27
  folios** in the natural order: **Portada → Simbología → Alimentación → card drawings
  (101–110) → borneros (200–209) → BOM (300–302) → Historial (900)**.
- **THE NEXT TASK = DA.5c** — prev/next continuation references ("viene de pág. X /
  sigue en pág. Y"). It was never gated. **Gate the wording/scope with Abel before
  coding** (see the DA.5c section below). After that, the theme is complete → move to
  **Tier 3** (`docs/TIER3-tracker.md`).
- **Also pending Abel's word: pushing `feat/doc-assembly`.** Don't push without asking.

## What was done this cycle (all on `feat/doc-assembly`, floor intact, 173 tests green)

| # | Item | Commit |
|---|------|--------|
| DA.1 | Title-block template sync → `assets/exxerpro.titleblock` | `44b52e0` |
| DA.2 | Reorder folios to natural drawing order (`reorder_diagrams_by_position`) | `5fa2e4d` |
| DA.5a | Designations FOLLOW the printed section page → `-K101.x … -K110.x` | `5fa2e4d` |
| DA.3 | Portada (cover) folio with the title-block metadata + controller name | `2db8219` |
| DA.4 | Simbología (symbol legend) folio — real glyphs + Spanish names | `39cdd5c` |
| DA.5b | Section page shown in the cajetín (`sectionize_titleblock_page` → `%{page}`) | `7b2151b` |
| DA.6 | Hide schematic grid rulers on the non-schematic list folios (BOM "out of box") | `7b2151b` |
| DA.7 | Lift the card header so the power band stops overprinting the sub-header | `ad6afe8` |

Gated decisions live in memory **`da-numbering-decisions`** (sectioned page scheme;
designations follow printed page; cover = full metadata set; legend = glyph + Spanish
name). Status memory: **`qet-generator-status`**.

## ⚠️ HARD RULES (these bit us — do not repeat)

1. **NEVER run the generator with `-o Fixtures/WADDING_1.qet`.** That is **Abel's
   working artifact** (he hand-edits the title block in QET). A post-crash run once
   overwrote it. **Verify to a SCRATCH path** (`-o Fixtures/_gen_check.qet`) and parse
   THAT. Back up before any op that could touch it. (Memory: `never-overwrite-working-qet`.)
2. **Don't trust a subagent's `shipReady`/summary.** Re-derive every number from ground
   truth (run the generator → read stderr; run the tests; eyeball in QET). Prior cycles
   shipped real geometry bugs that passed the implementer's own short-circuiting test.
3. **Never force / never invent.** Unmatched → generic; missing/ambiguous → graceful
   fallback. Physical pins stay `"TBD"` → `__`. Multilingual DBs stay language-agnostic
   (pull display names from the DB, e.g. `symbol_display_name`). Python 3.10+, **stdlib only.**
4. **Public-repo hygiene:** NEVER `git add` anything under `Fixtures/` or any
   `*.L5X` / `*.qet` / `*_eplan.csv` / `*_bom.csv` / `*.pdf` / personal file. Company
   assets (`assets/exxerpro.titleblock`, the logo **`.svg`**) ARE committed; the
   `.png/.bmp/.ai` logo exports are intentionally untracked.
5. **QET caches title-block templates at startup** — fully RESTART QET to see template edits.
6. **QET numbers folios by DOCUMENT POSITION, not the `order` attribute.** The cajetín's
   built-in `%{folio-id}` showed 1..27, ignoring our section pages. `sectionize_titleblock_page`
   rewrites `%{folio-id}/%{folio-total}` → a custom `%{page}` token in the EMBEDDED copy
   only; `apply_titleblock` fills it per folio from `diagram.get("order")`, zero-padded.
   The committed asset stays standard (re-syncable). **To show any custom folio number,
   use a custom property — never rely on `order` driving the display.**
7. **Card-drawing top band is geometrically tight.** The inline power band sits *between*
   the two terminal columns (x≈150–410), wedged between the sub-header and the first I/O
   row's strip label (~y 87). It can't move down (hits the first row's strip/symbol at
   the same x) or up (header). Don't reintroduce the overprint; the regression test
   `test_subheader_clears_power_band` guards ≥12px clearance.
8. Commit footer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
   One focused commit per item; doc/handoff changes in their OWN commit.

## THE NEXT TASK — DA.5c: prev/next continuation references

**Goal:** on multi-sheet sections, add "comes from / continues on" references so a reader
can follow a section across its folios (classic EPLAN "viene de pág. X / sigue en pág. Y").

**GATE WITH ABEL FIRST (AskUserQuestion + previews) — this was never gated:**
- **Wording/format** — Spanish "viene de pág. {X} / sigue en pág. {Y}"? abbreviations?
  which corner of the sheet?
- **Which sections get them** — only the genuinely multi-folio ones (today: BOM is 3
  folios `300–302`; borneros are 10 folios `200–209`; the drawings are 10 sheets
  `101–110`). Does Abel want them on the schematic sheets too, or only the paginated
  lists? Single-folio sections (Portada, Simbología, Alimentación, Historial) need none.
- **Which page number to show** — the SECTION page (the `%{page}` value, e.g. 301/303),
  since that's what the cajetín now displays (DA.5b). NOT QET's position.

**Implementation pointers:** the continuation ref is just text on the folio (an
`add_text` line), like the other folio annotations. The folios already carry their
section page as the diagram `order` and as the `%{page}` title-block property. For a
paginated section you know the page set (e.g. summary pages `SECTION_BOM + n`), so the
"prev/next" targets are computable at build time. Mirror the existing text-annotation
style; no new element types. Keep it data-driven and graceful (first sheet has no
"viene de", last has no "sigue en").

## Code map (current `src/logix_to_qet.py`)

`main()` builds folios in DEPENDENCY order, stamps each with a SECTION page, then
re-sorts into document order before serialization:

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
- `reorder_diagrams_by_position(project)` — stable sort `<diagram>` by int `order` (DA.2).
- `template_text = sectionize_titleblock_page(load_titleblock_template())` then
  `attach_titleblocks(...)` (fills `%{page}` per folio, DA.5b), `build_collection`,
  `embed_titleblock_templates` (injects the template verbatim — preserves the SVG).

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
- Run the full suite from `src/`: `python -m unittest test_logix_to_qet` (**173 tests**).
- Pure helper + integration + regression test for every invariant you claim; assert the
  REAL invariant (full symbol extent vs the real frame; floor numbers from stderr).
- **Eyeball in QET** (fully restart it) — offer to launch QET on the scratch output.
  Abel's QET reviews caught DA.5b (numbering), DA.6 (BOM out-of-box) and DA.7 (power
  overprint) that tests alone did not.

## Git state / how to resume

- `main` @ `1f24259` (bornero Tier 2 #6; **not pushed**).
- Branch `feat/doc-assembly` (off `main`) — all DA.1–DA.7 commits above + doc/tracker
  commits. **NOT pushed** — ask Abel before pushing.
- Prior-cycle convention: ff-merge the feature branch into `main` per theme, then Abel
  pushes. Don't push without asking.

## Kickoff prompt — paste into the new session

```
Continue the PLC → mini-EPLAN product (src/logix_to_qet.py) on branch
feat/doc-assembly. The Document-assembly theme (DA.1–DA.7) is DONE and reviewed in QET;
27 folios in natural order, floor 10/106/75/0 FP, 173 tests green. NEXT TASK = DA.5c:
prev/next continuation refs ("viene de pág. X / sigue en pág. Y") on multi-folio
sections.

READ FIRST: docs/HANDOFF-next-cycle.md (state, HARD RULES incl. #6 QET-numbers-by-
position and #7 tight top band, code map, the DA.5c gating points), docs/TIER3-tracker.md,
ProductPlanEnhancement.md, and memory da-numbering-decisions + qet-generator-status.

GATE DA.5c WITH ABEL (AskUserQuestion + previews) BEFORE coding: exact Spanish wording +
sheet corner; which sections get refs (paginated lists only, or schematics too); confirm
the page number shown is the SECTION page (%{page}, e.g. 301/303), not QET's position.
Then implement as text annotations mirroring the existing folio annotations; first/last
sheet of a section omit the absent direction. Verify from ground truth, one focused
commit, eyeball in QET. After DA.5c the theme is complete → Tier 3 (TIER3-tracker.md).

HARD RULES: never -o Fixtures/WADDING_1.qet (use Fixtures/_gen_check.qet); never invent
(TBD→__, blank cells); stdlib only; never git add Fixtures/ or *.L5X/*.qet/*.pdf/*_bom.csv;
restart QET to see template edits; don't push without Abel's OK.
```

---
*Overwrite this file for the cycle after DA.5c.*
