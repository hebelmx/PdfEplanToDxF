# Handoff — next dev cycle (Tier 2 #4: Cajetín / title block)

> Self-contained handoff so a **fresh agent in a new session** can run the next
> backlog item with no prior context. Written 2026-06-13 after Tier 1 #3.

## Where things stand

- Product: turn a Rockwell **L5X** export into a near-finished QElectroTech I/O
  drawing set. Driver = `ProductPlanEnhancement.md`. Generator = `src/logix_to_qet.py`.
- **Tier 1 is now COMPLETE** (all three quick "pure data we already have" wins):
  1. Device designations (`-S1`, `-B1`, `-K1`…) — `next_designation()`.
  2. Wire numbers (EPLAN address verbatim / `W<page>.<n>`) — `wire_number()`.
  3. **Just completed — Tier 1 #3 "Device-index / BOM".** A unified 10-column
     BOM (`category, folio, designation, catalog_or_type, tag, address, vendor,
     description, rack, slot`) collected **during** the existing `build_folio`
     traversal (no second pass) and emitted **two ways**:
     - **CSV sidecar** `<output>_bom.csv` — the complete record, all 10 columns.
       Formula-injection guarded (`_csv_safe`: cells leading with `= + - @`,
       e.g. every `-S1.1` designation, get an apostrophe so spreadsheets show
       text not `#NAME?`).
     - **Paginated QET summary folios** appended after the drawing folios,
       rendering a **legible subset** (`folio / designation / type / tag /
       address / description`) with per-column ellipsizing (`_ellipsize`),
       text+shapes only — **no terminals/conductors**. `SUMMARY_FOLIO_COLUMNS`
       defines the subset; `SUMMARY_ROWS_PER_PAGE` is **derived from geometry**.
     - Three categories, nothing invented: `module` row per card; `device` row
       per matched device (using the actually-emitted designation); `generic`
       row per unmatched/analog point (designation+type blank).
  - Helpers + tests follow the established pattern (see below). WADDING_1:
    10 folios / 106 points / 75 matched / 0 FP; BOM = 116 rows over 3 summary
    folios. **52 unittests pass.**
- Branch **`feat/device-index`** (cut from `main`, HEAD was `33cb66c`) holds the
  BOM commit **`12f2d13`**.
- **`feat/device-index` is NOT merged to `main` yet** at time of writing (Abel
  eyeballs the `.qet` in QElectroTech before merging). **First thing to check:**
  what's merged into `main`?
  - If the BOM work is **merged** → branch fresh from `main`.
  - If **not** → branch from `feat/device-index` so you build on it.
  - Either way: start from a **clean working tree** (`git status` clean).

## ⚠️ Lesson from the last cycle — don't trust the workflow's `shipReady`

The `adversarial-dev-cycle` workflow returned **`shipReady: true` with a log line
"0 blocking/major findings" — but the review lenses actually contained FOUR major
findings** (unreadable summary folio from overlapping columns; a zero-height
"underline" rect that won't render + bled off-page; CSV formula injection; the
byte-identical invariant untested). The verdict agent miscounted. **Always read
the individual lens findings yourself**; treat the top-level `shipReady` as a
claim to verify, not a gate. Rivet's principle holds: a clean/green verdict is a
smell to investigate, not a victory. The four were fixed before commit.

## Conventions established (reuse them)

- **Confirm format with Abel in the Plan phase before implementing.** He has
  strong, specific preferences (e.g. no page-prefix on globally-unique values;
  CSV+folio both wanted; legible subset on the folio while the CSV stays the
  complete record). Don't assume — ask via the Plan-phase confirmation.
- **Never force / never invent.** Unmatched → generic; missing data → graceful
  fallback (`None` / `""`), never garbage. Physical pins stay `"TBD"` → `__`.
- **Pure helper + integration test.** A pure, deterministic helper with stdlib
  unittests AND a `build_folio`/`main`-level integration test so a broken call
  site can't pass silently. Add a **regression test for any invariant you
  claim** (the "drawing folios unchanged" test normalizes per-element uuids and
  asserts XML equality — copy that pattern).
- **Don't let presentation slide.** A feature whose value is legibility (a folio,
  a title block) must actually render legibly — test x-bounds / page-frame, not
  just that data exists.
- One focused commit per backlog item; message names the manual step removed.
  Infrastructure/doc changes (like this handoff) go in their **own** commit.

## The next item — Tier 2 #4: Cajetín (title block)

From `ProductPlanEnhancement.md`: *"Replace hardcoded header with a JSON-config-
driven template (`src/project_template.json`): company, logo path, author,
project title, date, folio `x/total`. Sensible defaults if absent."*
Removes the manual step of filling in every sheet's title block by hand.

Code pointers (all in `src/logix_to_qet.py`):
- **Today there is no real title block** — just a header text line per folio:
  `add_text(inputs, 40, 30, header, FONT_HEADER)` (~line 489) and a sub-line at
  ~line 493. The diagram carries `author="logix_to_qet"`, `folio="%id/%total"`
  (~line 471 for drawing folios, ~line 634 for summary folios).
- Project title is set once in `main()`: `ET.Element("project", {"title":
  f"{controller} I/O", ...})` (~line 729).
- A cajetín is normally a **framed box in a page corner** with labelled fields.
  You'd add a `load_project_template()` (mirror `load_module_db`/`load_symbol_db`:
  stdlib `json`, `utf-8-sig`, graceful defaults if the file is absent) and a
  `draw_title_block(inputs, shapes, ...)` reused by **both** `build_folio` and
  `_add_summary_diagram`.

**Open decisions for the Plan phase to confirm with Abel:**
- Exact field set + labels and the **default values** when `project_template.json`
  is absent (don't block on the file existing).
- **Logo:** QET has no trivial "embed a PNG" path — confirm whether to attempt an
  image element, just reserve a labelled box, or defer the logo to a later cycle.
  (A wrong/broken image is worse than a clean reserved space — same guardrail
  spirit as pins.)
- Title-block **geometry/placement** (bottom-right corner is conventional) and
  whether it appears on summary folios too (it should, for consistency).
- Whether `folio x/total` uses QET's `%id/%total` tokens (already in use) or a
  computed string.

## Kickoff prompt — paste this into the new session

```
Run the next dev cycle for the PLC → mini-EPLAN product using the
adversarial-dev-cycle workflow. This is an explicit opt-in to multi-agent
orchestration — run the workflow. NOTE: the workflow's top-level shipReady has
been wrong before — read the individual review-lens findings yourself and verify.

Backlog item: Tier 2 #4, "Cajetín (title block)" from ProductPlanEnhancement.md.
Replace the hardcoded folio header with a JSON-config-driven title block
(src/project_template.json: company, logo path, author, project title, date,
folio x/total) with sensible defaults when the file is absent. Reuse one
draw_title_block helper across both the drawing folios (build_folio) and the
summary folios (_add_summary_diagram). Never invent data; a missing logo/field
degrades to a clean reserved box, never garbage.

Before running, read these so you have full context:
- ProductPlanEnhancement.md (vision, backlog, guardrails, validation)
- docs/BMAD-Orchestration.md (how Rivet + the workflow work)
- docs/HANDOFF-next-cycle.md (current state, conventions, the shipReady lesson)
- src/logix_to_qet.py — esp. build_folio() (~line 489 header), _add_summary_diagram(),
  load_module_db()/load_symbol_db() (the JSON-load + graceful-default pattern), main()
- src/test_logix_to_qet.py — pure-helper + integration + regression test patterns

In the workflow's Plan phase, CONFIRM with Abel: exact field set + default values;
how to handle the logo (image vs reserved box vs defer); title-block geometry and
whether it appears on summary folios — before implementing.

Then invoke:
  Workflow({ name: "adversarial-dev-cycle",
             args: { item: "Cajetín (title block)",
                     acceptance: "JSON-config-driven title block (src/project_template.json: company/logo/author/project title/date/folio x/total) drawn via one reusable helper on every folio incl. summary folios; sensible defaults when the file is absent or a field is missing; no invented/garbage values (missing logo -> clean reserved box); WADDING_1 still 10 folios / 106 points / 75 matches / 0 false positives with the drawing content unchanged apart from the added title block" } })

Hard gate before any commit:
  python src/logix_to_qet.py Fixtures/WADDING_1.L5X -o Fixtures/WADDING_1.qet
must still report ≥75 symbols matched and 0 false positives, with
terminal-id/conductor/definition assertions passing AND the title block present
and correct (legible, inside the page frame) on every folio. Add/extend stdlib
unittests for the new helper, plus a regression test for any invariant you claim.
The workflow does NOT commit — review the findings (not just shipReady), then
commit yourself with one focused message naming the manual step removed (filling
in each sheet's title block by hand). NEVER git add Fixtures/ or any
*.L5X / *.qet / *_eplan.csv / *_bom.csv.

Start state: branch feat/device-index holds Tier 1 #1+#2+#3. First confirm
whether it's merged to main; if yes branch fresh from main, if not branch from
feat/device-index so you build on it. Verify a clean tree before starting.
```

## Hard gate & guardrails (always)

- Validation command:
  `python src/logix_to_qet.py Fixtures/WADDING_1.L5X -o Fixtures/WADDING_1.qet`
- Floor that must NOT regress: **10 folios / 106 points / 75 matched / 0 false
  positives.** Plus: terminal ids unique per diagram; every conductor
  `terminal1`/`terminal2` resolves to an existing id; every element `type` has an
  embedded `<definition>`. Run the full unittest suite (`python -m unittest
  test_logix_to_qet` from `src/`).
- Python 3.10+, **standard library only.** Multilingual DBs stay
  language-agnostic.
- Never guess physical pin numbers (`module_db` pins stay `"TBD"` → `__`).
- **Public repo:** never `git add` anything under `Fixtures/` or any
  `*.L5X` / `*.qet` / `*_eplan.csv` / `*_bom.csv` / personal file. (This handoff
  doc itself is fine to commit; the generated artifacts never are.)

---
*This file is a convenience handoff; delete it once the next cycle is underway,
or keep it and overwrite for the cycle after.*
