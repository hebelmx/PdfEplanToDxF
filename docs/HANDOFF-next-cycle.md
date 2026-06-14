# Handoff — next dev cycle (Document assembly / front matter, "DA.x")

> Self-contained handoff so a **fresh agent in a new session** can continue with
> no prior context. Written 2026-06-14 mid-cycle (Abel had to reboot for an
> unrelated Docker issue). Supersedes the previous Tier 2 #6 handoff.

## TL;DR — read this first

- Product: turn a Rockwell **L5X** export into a near-finished QElectroTech I/O
  drawing set. Driver = `ProductPlanEnhancement.md`. Generator = `src/logix_to_qet.py`.
  Durable task list = `docs/TIER3-tracker.md` **+** the TaskCreate/TaskUpdate tools.
- **ALL of Tier 2 is DONE and on `main`:** #4 cajetín, #5 power/supply, **#6 terminal
  strip / bornero** (commit `1f24259`, ff-merged to local `main`, **NOT pushed**).
- **Current work = a NEW theme, "Document assembly / front matter" (DA.x),** that runs
  BEFORE Tier 3. Branch: **`feat/doc-assembly`** (off `main` @ `1f24259`).
- **DA.1–DA.4 + DA.5a are DONE** on the branch: DA.1 template sync (`44b52e0`),
  DA.2 reorder + DA.5a designation page prefix (`5fa2e4d`), DA.3 Portada (`2db8219`),
  DA.4 Simbología (`39cdd5c`). **Not pushed.** WADDING_1 now emits **27 folios** in the
  gated order (Portada → Simbología → Alimentación → drawings 101–110 → borneros
  200–209 → BOM 300–302 → Historial 900); floor intact; **164 tests** green.
- **REMAINING:** **DA.5b** — the cajetín page cell still shows QET's built-in
  `%{folio-id}` (position 1..N); the sectioned 000/001/100… display is UNVERIFIED and
  needs an in-QET eyeball (the `order` attr is set to the section page, but QET may
  number by position). Fallback if it shows 1..27: swap `%{folio-id}` → a custom
  `%{page}` property in `assets/exxerpro.titleblock` (Abel re-syncs that file, so gate
  first). **DA.5c** — prev/next continuation refs ("viene de / sigue en"), never gated.
  Gated decisions live in memory `da-numbering-decisions`.

## ⚠️ HARD RULES (these just bit us — do not repeat)

1. **NEVER run the generator with `-o Fixtures/WADDING_1.qet`.** That file is **Abel's
   working artifact** — he opens it in QElectroTech and hand-edits the title block /
   layout, then saves. On 2026-06-14 a post-crash gate re-run with `-o
   Fixtures/WADDING_1.qet` **overwrote his hand-edited ~82 KB template** (saved ~Jun 13
   23:30). It was unrecoverable from disk (no `.qet~`/autosave; his QET personal
   collection at `%APPDATA%/QElectroTech/QElectroTech/titleblocks/exxerpro.titleblock`
   still held an OLDER 19:18 copy). Recovery only worked because Abel re-did the edit in
   QET and re-saved the project, after which it was extracted from his fresh `.qet`.
   **For the hard gate / any verification run, output to a SCRATCH path** (e.g.
   `-o Fixtures/_gen_check.qet`) and parse THAT. Back up `Fixtures/WADDING_1.qet` before
   any operation that could touch it. (Memory: `never-overwrite-working-qet`.)
2. **Don't trust a subagent's `shipReady`/summary.** Re-derive every number from ground
   truth (run the generator → read stderr; run the tests). Prior cycles shipped real
   geometry bugs that passed the implementer's own short-circuiting test.
3. **Never force / never invent.** Unmatched → generic; missing/ambiguous data →
   graceful fallback. Physical pins stay `"TBD"` → `__`. Multilingual DBs stay
   language-agnostic. Python 3.10+, **standard library only.**
4. **Public repo hygiene:** NEVER `git add` anything under `Fixtures/` or any
   `*.L5X` / `*.qet` / `*_eplan.csv` / `*_bom.csv` / personal file. Company assets
   (`assets/exxerpro.titleblock`, the logo **`.svg`**) ARE committed; the
   `.png/.bmp/.ai` logo exports are intentionally untracked.
5. **QET caches title-block templates at startup** — fully RESTART QET to see edits.
6. Commit footer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
   One focused commit per item; doc/handoff changes in their OWN commit.

## The theme — Document assembly / front matter (from Abel's PDF review 2026-06-14)

Abel exported the full project to PDF (`Fixtures/WADDING_1.pdf`, 25 pp.) and found the
set was in **dev-order, not natural drawing order**, and lacked front matter.

**Target folio order — CONFIRMED with Abel (decision: "borneros grouped"):**

```
Portada (cover + project data)        <- NEW (DA.3)
Simbología (symbol legend)            <- NEW (DA.4)
Alimentación (power rails)            <- move EARLY (currently near the end)
card drawings (one per I/O card)      <- the 10 drawing folios
borneros -X1 (one per card, GROUPED)  <- currently last; keep grouped, move before BOM
BOM / índice de dispositivos          <- the summary folios
Historial de revisiones               <- LAST (changelog)
```

### Task table (also in `docs/TIER3-tracker.md`, and TaskList #2–#6)

| # | Item | Status |
|---|------|--------|
| DA.1 | Title-block template sync → `assets/exxerpro.titleblock` | **DONE `44b52e0`** |
| DA.2 | Reorder folios to natural drawing order | todo |
| DA.3 | Portada (cover) folio w/ project data | todo |
| DA.4 | Simbología (symbol legend) folio | todo |
| DA.5 | Section page numbering w/ gaps + prev/next continuation refs | todo (**scheme must be gated with Abel**) |

Floor unchanged for all of them: **10 drawing folios / 106 points / 75 matched / 0 FP.**

## Code map (all `src/logix_to_qet.py` unless noted)

`main()` builds folios in this CREATION sequence (each `ET.SubElement(project,
"diagram", {"order": str(order), …})`, so XML position == creation order today):

- `~1390` drawing-folio loop → `build_folio(project, order, mod, pts, …)`, `order += 1`
  per card. Accumulates `bom_rows` AND `drawn_cards` (used later). `sym_counts` (the
  matched-symbol histogram) is also populated here.
- `~1401` `build_summary_folios(project, order, bom_rows)` — the BOM folios.
- `~1406` `build_changelog_folios(project, order, revisions)` — Historial.
- `~1411` `build_supply_folios(project, order, io_mods)` — Alimentación.
- `~1417` `build_bornero_folios(project, order, drawn_cards)` — borneros.
- `~1424` `load_titleblock_template()` + `attach_titleblocks(...)` then
  `embed_titleblock_templates()` injects the template verbatim (preserves the SVG).

The "append a folio → it inherits the title block" pattern (text + shapes only, empty
`<elements>`/`<conductors>`) is shared by `build_summary_folios` /
`build_changelog_folios` / `build_supply_folios` / `build_bornero_folios` (+ their
`_add_*_diagram` helpers). **DA.3/DA.4 should mirror this exact pattern.**

## ⚠️ DA.2 / DA.5 ENTANGLEMENT — design before coding, gate with Abel

The `order` int passed to `build_folio` is **also the page-number prefix** in:
- device designations: `next_designation(sym, designations, order)` → `-K<order>.<n>`
- wire numbers (default scheme): `wire_number(address, order, …)`

So you **cannot** just renumber/reorder drawing folios without shifting every
designation and wire number (e.g. drawing pages 1–10 → 4–13 would make `-K1.1`
become `-K4.1`). Two consequences:

1. **Decide the folio-position mechanism.** Two clean options:
   - **(A) Sort at the end by an explicit position attribute.** Give each diagram a
     separate *position* value (target sequence) and add a
     `reorder_diagrams_by_position(project)` helper that stably re-appends the
     `<diagram>` children in that order right before serialization — decoupling
     build-order (driven by data deps: bom_rows/drawn_cards/sym_counts come from the
     drawing loop) from folio position. **Keep the drawing folios' `order`/page value
     stable (1..10)** so designations/wire numbers DON'T move.
   - **(B) Verify whether QET honors the `order` attribute** independent of XML element
     position. If it does, you may only need to set `order` correctly + still emit
     elements in dependency order. **Test this in QET before relying on it.**
2. **Page numbering (DA.5) must be gated with Abel** — gap size, section boundaries,
   and whether section gaps change the page number used in designations/wire numbers
   (likely NO — keep designation page = drawing-sheet ordinal). Also the prev/next
   continuation refs ("viene de pág X / sigue en pág Y") format.

**Recommendation:** design DA.2 + DA.5 together; gate the numbering scheme with Abel
(AskUserQuestion + previews) BEFORE implementing, because it dictates whether
designations move. Cover/symbology/supply/bom/changelog folios carry no designations,
so their page numbers are free to use the gap scheme.

## DA.3 Portada (cover) — pointers

Mirror `_add_supply_diagram`/`build_supply_folios`. Data-driven from project metadata
already loaded into `tb_fields` (company/owner, project/drawing name, drawing number,
revision, static release date, approver) + the L5X controller name. Never invent
fields not present (blank cell instead). It inherits the ISO 7200 title block via
`attach_titleblocks` (so build it BEFORE that call, like the others).

## DA.4 Simbología (symbol legend) — pointers

List ONLY the symbols actually placed: `used = [e for e in symbols if e["id"] in
sym_counts]` is already computed at `~1427` for `build_collection`. For each used
symbol type show its glyph + Spanish name (the name/label lives in the `symbol_db`
entry; keep language-agnostic — pull the display name from the DB, don't hardcode).
Do NOT list unused symbols. Text + shapes pattern; inherits the title block.

## Hard gate & guardrails (ALWAYS)

- **Validation command (note the SCRATCH output — NOT WADDING_1.qet):**
  `python src/logix_to_qet.py Fixtures/WADDING_1.L5X -o Fixtures/_gen_check.qet`
- Floor that must NOT regress: **10 drawing folios / 106 points / 75 matched / 0 FP.**
  Plus: terminal ids unique per diagram; every conductor `terminal1`/`terminal2`
  resolves; no zero-length conductors; every element `type` has an embedded
  `<definition>`; **ISO 7200 title block on every folio** with a `<property>` for every
  custom token (the current template's tokens: owner, department, ref, approval, type,
  status, code, name, rev, country) so QET leaks no raw `%{token}`; the changelog,
  Alimentación and bornero folios all present.
- Run the full suite from `src/`: `python -m unittest test_logix_to_qet` (**145 tests**).
- Pure helper + integration + regression test for every invariant you claim; assert the
  REAL invariant (full symbol extent vs the real frame; floor numbers parsed from
  `main()`'s stderr summary — not a proxy like "folio count grew").
- **Eyeball in QET** (fully restart it) — structure passing tests is necessary, not
  sufficient. Offer to launch QET on the scratch output.

## Open decisions to gate with Abel (before coding DA.2/DA.5)

- DA.5 numbering scheme: gap size per section; do section gaps affect the drawing-sheet
  page number used in designations/wire numbers (recommend NO)? prev/next ref wording.
- DA.2 mechanism: confirm whether QET orders by `order` attr or XML position (test it),
  which picks option A vs B above.
- Cover (DA.3) content: exactly which project-data fields Abel wants on the portada.
- Symbología (DA.4): glyph + name only, or also a short description column?

## Git state / how to resume

- `main` @ `1f24259` (bornero #6; **not pushed** — ask Abel before pushing).
- Branch `feat/doc-assembly` @ `44b52e0` (DA.1 template sync committed).
- Uncommitted on the branch when this was written: `docs/TIER3-tracker.md` (DA section +
  DA.1 done) and this handoff — to be committed together as the doc commit.
- Backup of Abel's recovered `.qet`: `Fixtures/WADDING_1.qet.abel-backup-20260614`
  (gitignored — do NOT commit; keep as safety).
- Prior-cycle convention: ff-merge the feature branch into `main` per item, then
  Abel pushes. Don't push without asking.

## Kickoff prompt — paste into the new session

```
Continue the PLC → mini-EPLAN product. Current theme: "Document assembly / front
matter" (DA.x) on branch feat/doc-assembly (off main @ 1f24259). DA.1 (title-block
template sync, commit 44b52e0) is DONE. Do DA.2–DA.5 per docs/HANDOFF-next-cycle.md.

READ FIRST: docs/HANDOFF-next-cycle.md (state, HARD RULES, code map, the DA.2/DA.5
designation entanglement), docs/TIER3-tracker.md (task table), ProductPlanEnhancement.md
(vision/guardrails), and in src/logix_to_qet.py the main() build sequence + the
build_*_folios "append a folio → inherits the title block" pattern.

Target folio order (confirmed): Portada → Simbología → Alimentación → card drawings →
borneros (grouped) → BOM → Historial de revisiones (LAST).

HARD RULES: never run the generator with -o Fixtures/WADDING_1.qet (it's Abel's working
file — use -o Fixtures/_gen_check.qet). Never invent data; pins stay TBD→__; stdlib
only; never git add Fixtures/ or *.L5X/*.qet/*_eplan.csv/*_bom.csv. Floor: 10 drawing
folios / 106 points / 75 matched / 0 FP; 145 unittests pass; ISO 7200 title block on
every folio with a property for every custom token.

GATE WITH ABEL before coding DA.2/DA.5 (AskUserQuestion + previews): the page-numbering
scheme (gaps/sections; do they shift designation/wire page numbers — recommend NO) and
the folio-position mechanism (sort-by-position attr vs QET order attr — test in QET).
Then implement, verify from ground truth, one focused commit per item, eyeball in QET.
```

---
*Overwrite this file for the cycle after DA.x.*
