# Handoff — next dev cycle (Tier 2 #5: Power / supply)

> Self-contained handoff so a **fresh agent in a new session** can run the next
> backlog item with no prior context. Written 2026-06-13 after Tier 2 #4.

## Where things stand

- Product: turn a Rockwell **L5X** export into a near-finished QElectroTech I/O
  drawing set. Driver = `ProductPlanEnhancement.md`. Generator = `src/logix_to_qet.py`.
- **Tier 1 COMPLETE** (designations, wire numbers, device-index/BOM).
- **Tier 2 #4 COMPLETE and MERGED to `main`** (commits `7c3ffb4` title block,
  `a1bf412` changelog; fast-forwarded into `main`). What it added:
  - **Native QElectroTech ISO 7200 title block** on every folio — *not* hand-drawn.
    (Abel is ISO 9001 certified and chose pure ISO 7200.) `src/build_titleblock.py`
    clones QET's bundled `ISO7200_A4_V1.titleblock` into `assets/exxerpro.titleblock`,
    embedding the Exxerpro **SVG** logo in the big left cell (col0 rowspan4),
    viewBox padded to the cell aspect so QET's stretch-to-fill can't distort it.
  - The generator **embeds the template verbatim as TEXT** (ElementTree would
    mangle the SVG's namespaces) and sets per-folio
    `titleblocktemplate="exxerpro" titleblocktemplateCollection="embedded"
    displayAt="bottom"` + a `<property name=token>` for **every** custom token
    (blank when no data — else QET renders the raw `%{token}`). Built-ins
    (`%{author}`/`%{title}`/`%{date}`/`%{filename}`, and the auto sheet number
    `%{folio-id}/%{folio-total}`) come from diagram attributes / QET itself.
  - Field values come from **`src/project_template.json`** (company→owner,
    project→drawingname, drawing_number→ref, revision→rev, approved_by→approval;
    **date is the STATIC release date, never `today()`**, for traceability).
  - **Changelog / revision-history folio** (`Historial de revisiones`, last sheet):
    REV/FECHA/DESCRIPCIÓN/DIBUJÓ/APROBÓ, driven by a `revisions` array in
    project_template.json; no config → one synthesised "Primera emisión" row.
  - WADDING_1 now = 10 drawing + 3 summary + 1 changelog = **14 folios**.
    **88 unittests pass.**
- **`main` is ahead of `origin/main`** (not pushed). Start the next cycle by
  **branching fresh from `main`** with a clean tree (`git status` clean — note
  `assets/*.png/*.bmp/*.ai` are intentionally untracked; only the SVG +
  `.titleblock` are committed).

## ⚠️ Lessons (carry forward)

- **Don't trust the workflow's `shipReady`.** It once returned `shipReady:true`
  with "0 blocking findings" while the lenses held FOUR majors. Always read the
  individual review-lens findings yourself; a clean verdict is a smell to verify.
- **QET caches title-block templates at startup.** After editing a `.titleblock`,
  you must *fully restart* QElectroTech to see the change — reopening the file is
  not enough. (This caused a long "it's not updating" detour.)
- **The `.qet` integration is reverse-engineered from QET's own examples**
  (`C:\Program Files\QElectroTech\examples\`). `iso_sfc_example.qet` uses
  `ISO7200_A4_V1` and shows exactly how a diagram references a template and
  stores custom field values (`<property name=...>`). When in doubt about QET
  markup, read a shipped example, don't guess.

## Conventions established (reuse them)

- **Confirm format with Abel in the Plan phase before implementing.** He has
  strong, specific preferences and iterates visually (he exports a PDF / screen-
  shots the folio). Ask before building; offer to launch QET on the output.
- **Never force / never invent.** Unmatched → generic; missing data → graceful
  fallback (`None`/`""`/empty `<property>`), never garbage. Physical pins stay
  `"TBD"` → `__`.
- **Pure helper + integration + regression test.** A deterministic helper with
  stdlib unittests, a `build_folio`/`main`-level integration test, and a
  regression test for any invariant you claim (e.g. "title block doesn't touch
  `<elements>`/`<conductors>`").
- **Presentation must actually render.** A feature whose value is legibility must
  be tested for page-frame / cell bounds, and **eyeballed in QET** — structure
  passing tests is necessary but not sufficient.
- One focused commit per backlog item; message names the manual step removed.
  Doc/handoff changes go in their **own** commit.

## The next item — Tier 2 #5: Power / supply

From `ProductPlanEnhancement.md`:
> **(a)** Draw each card's own **power/common terminals** — extend `module_db`
> with the group-common structure (e.g. `1756-OA16` = 2 groups of 8, separate L1
> commons; DC input cards share a common); pins stay `"TBD"` if unfilled.
> **(b)** A **supply-rail folio** (L+/L‑/24 V/PE) that the cards reference.

Removes the manual step of drawing power/common wiring and the supply distribution.

Code pointers (all in `src/logix_to_qet.py` unless noted):
- `load_module_db(catalog)` (~line 156) loads `src/module_db/<base>.json`; today it
  exposes `_wiring_by_point` (per-point `pin`/`name`). **(a) extends this schema**
  with a group/common structure — keep the same load + graceful-default pattern,
  and keep `"TBD"` pins as `__` (never guess RTB pins from manuals — Abel fills).
- `build_folio()` (~line 520) draws the card box + terminals per point. Power/
  common terminals would be drawn here (or in a sibling helper) from the new
  module_db structure.
- For **(b)** the supply-rail folio, mirror the **summary/changelog folio**
  pattern: a dedicated `build_*_folios(project, start_order, ...)` appended after
  the drawing folios, then it gets the title block automatically (the title block
  is attached to *every* diagram in `attach_titleblocks`).
- `main()` (~line 879) is the assembly order: drawing folios → summary →
  changelog → `attach_titleblocks` → collection → CSV → pretty-print → inject
  template text. Insert the supply folio in that order (before the title-block
  attach so it inherits the cajetín).

**Open decisions for the Plan phase to confirm with Abel:**
- Exact `module_db` group-common schema (how to express "2 groups of 8 with
  separate L1 commons", shared DC commons) — and which sample cards to model
  first (the WADDING_1 cards: `1756-IA16`, `1756-OA16`, …).
- Whether power/commons render **inline on each card folio**, on a **dedicated
  supply-rail folio**, or both; and how cards "reference" the rail (cross-ref
  text vs. drawn conductor).
- Potentials to show (L1/N for AC cards, L+/24 V/0 V/PE for DC) and labels
  (Spanish, consistent with the title block).

## Kickoff prompt — paste this into the new session

```
Run the next dev cycle for the PLC → mini-EPLAN product. Backlog item: Tier 2 #5,
"Power / supply" from ProductPlanEnhancement.md — (a) draw each card's power/
common terminals from an extended module_db group-common schema (pins stay "TBD"
-> __ if unfilled, never guessed), and (b) a supply-rail folio (L+/L-/24V/PE) the
cards reference. Reuse the established patterns; never invent data.

Before implementing, read for full context:
- ProductPlanEnhancement.md (vision, backlog, guardrails, validation)
- docs/HANDOFF-next-cycle.md (this file — current state, lessons, conventions)
- docs/BMAD-Orchestration.md (how Rivet + the dev cycle work)
- src/logix_to_qet.py — load_module_db(), build_folio(), the summary/changelog
  folio builders (the "append a folio + it gets the title block" pattern), main()
- src/module_db/*.json (current per-point wiring schema to extend)
- src/test_logix_to_qet.py (pure-helper + integration + regression test patterns)

CONFIRM with Abel in the Plan phase: the module_db group-common schema; inline
vs dedicated supply folio (or both); which cards to model first; potentials/labels.

Hard gate before any commit:
  python src/logix_to_qet.py Fixtures/WADDING_1.L5X -o Fixtures/WADDING_1.qet
must still report 10 drawing folios / 106 points / 75 matched / 0 false positives,
with terminal-id/conductor/definition assertions passing AND the ISO 7200 title
block present on every folio AND the changelog folio intact. Run the full unittest
suite from src/ (python -m unittest test_logix_to_qet). Eyeball the .qet in QET
(remember: fully restart QET to reload title-block templates). One focused commit
naming the manual step removed. NEVER git add Fixtures/ or any
*.L5X / *.qet / *_eplan.csv / *_bom.csv.

Start state: branch fresh from main (Tier 2 #4 is merged). Verify a clean tree.
```

## Hard gate & guardrails (always)

- Validation command:
  `python src/logix_to_qet.py Fixtures/WADDING_1.L5X -o Fixtures/WADDING_1.qet`
- Floor that must NOT regress: **10 drawing folios / 106 points / 75 matched / 0
  false positives.** Plus: terminal ids unique per diagram; every conductor
  `terminal1`/`terminal2` resolves to an existing id; every element `type` has an
  embedded `<definition>`; the **ISO 7200 title block on every folio** (no raw
  `%{tokens}`); the **changelog folio** present. Run the full unittest suite
  (`python -m unittest test_logix_to_qet` from `src/`).
- Python 3.10+, **standard library only.** Multilingual DBs stay language-agnostic.
- Never guess physical pin numbers (`module_db` pins stay `"TBD"` → `__`).
- **Public repo:** never `git add` anything under `Fixtures/` or any
  `*.L5X` / `*.qet` / `*_eplan.csv` / `*_bom.csv` / personal file. Company assets
  (`assets/exxerpro.titleblock`, the logo **SVG**) are committed; the `.png/.bmp/.ai`
  logo exports are intentionally untracked.

---
*This file is a convenience handoff; overwrite it for the cycle after Tier 2 #5.*
