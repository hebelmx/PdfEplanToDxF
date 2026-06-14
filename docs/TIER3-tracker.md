# Tier 3 tracker — PLC → mini-EPLAN (polish)

> Durable task list for the **autonomous** `bmad-orchestrator` skill. One source
> of truth: this file + the TaskCreate/TaskUpdate tools. Update an item's status
> the moment it changes state. Items are from `ProductPlanEnhancement.md` Tier 3.
> **All of Tier 2 is DONE and merged to `main`** — #4 (cajetín), #5 (power/supply),
> and **#6 (terminal strip / bornero), commit `1f24259`**.
>
> **NEW theme inserted before Tier 3 — "Document assembly / front matter"** (from
> Abel's 2026-06-14 review of the exported PDF). The generated set was in dev-order,
> not natural drawing order, and lacked front matter. See the section below and
> `docs/HANDOFF-next-cycle.md`. This theme runs **before** T3.x.

Status legend: `todo` · `in-progress` · `review` · `done` · `blocked`

## Document assembly / front matter (DA.x) — runs BEFORE Tier 3

Confirmed with Abel 2026-06-14. Target folio order (decision: **borneros grouped**):
`Portada → Simbología → Alimentación → card drawings → borneros (grouped) → BOM/índice → Historial de revisiones (LAST)`.

| # | Item | Status | Manual step removed |
|---|------|--------|---------------------|
| DA.1 | Title-block template sync (extract newest from saved `.qet` → `assets/exxerpro.titleblock`) | `done` `44b52e0` | Re-applying the current cajetín template by hand |
| DA.2 | Reorder folios to natural drawing order | `done` `5fa2e4d` | Hand-resorting the set out of dev-order into drawing-order |
| DA.3 | Portada (cover) folio with project data | `done` `2db8219` | Hand-drawing the cover sheet |
| DA.4 | Simbología (symbol legend) folio | `done` `39cdd5c` | Hand-drawing the symbology legend |
| DA.5a | Designations follow the printed section page (-K101.x … -K110.x) | `done` `5fa2e4d` | — |
| DA.5b | DISPLAYED sectioned page number (000/001/100…) in the cajetín | `review` (needs QET eyeball) | Hand-numbering the cajetín with section gaps |
| DA.5c | prev/next continuation refs ("viene de / sigue en") | `todo` | Hand-writing the continuation references |

- **Coverage / floor unchanged:** 10 drawing folios / 106 points / 75 matched / 0 FP.
  WADDING_1 now emits **27 folios** in the gated order: Portada → Simbología →
  Alimentación → drawings (101–110) → borneros (200–209) → BOM (300–302) →
  Historial (900). Full suite **164 tests** green.
- **DA.1 decision:** extract Abel's newest embedded template (82KB) from
  `Fixtures/WADDING_1.qet` into the committed asset (76KB, stale, older logo SVG).
- **Gated decisions (2026-06-14):** section page scheme = sectioned-with-gaps
  (000/001/100/101–110/200+/300+/900); designations FOLLOW the printed page
  (Abel's choice, overriding the handoff's NO recommendation) → schematic devices
  are now -K101.x … -K110.x. Cover = full title-block metadata set; legend =
  glyph + Spanish name. See memory `da-numbering-decisions`.
- **DA.5b open:** the cajetín page cell uses QET's built-in `%{folio-id}`
  (position 1..N). The diagram `order` attr is set to the section page, but
  whether QET renders that as the displayed number is UNVERIFIED. If QET shows
  1..27, the fallback is swapping `%{folio-id}` → a custom `%{page}` property
  (touches `assets/exxerpro.titleblock`, which Abel re-syncs — gate first).
- **DA.5c open:** continuation-ref wording was never gated; design + gate.

---

| # | Item | Status | Manual step removed |
|---|------|--------|---------------------|
| T3.1 | NO/NC correctness on symbols | `todo` | Hand-flipping each contact to its real normally-open/closed state |
| T3.2 | Spare-point rendering | `todo` | Hand-drawing unused/reserved card points so the strip is complete |
| T3.3 | Column pagination on card overflow | `todo` | Manually splitting a high-point-count card across sheets/columns |
| T3.4 | PE / ground potentials | `todo` | Hand-drawing the protective-earth / ground references on devices |
| T3.5 | Additional languages (IT/DE/ZH) — pure data | `todo` (demand-driven) | Manual re-matching when a project ships in another language |

Recommended order: **T3.1 → T3.2 → T3.3 → T3.4**, then **T3.5 only when a project
demands it** (the plan calls the extra languages drop-in pure data, not a must-do).

---

## T3.1 — NO/NC correctness on symbols
- **Goal:** render each matched contact/symbol in its true normally-open vs
  normally-closed state instead of a single default, so the schematic is
  electrically correct without a manual flip.
- **Acceptance sketch:** `symbol_db` entries carry/derive the NO/NC variant (data,
  not code assumptions); the matcher picks the right variant from the tag/desc
  (e.g. `_NC`, `PARO`/e-stop ⇒ NC); low-confidence keeps the current default
  (never force). The placed element + its `<definition>` reflect the variant.
- **Open decisions (gate):** where the NO/NC signal lives (symbol_db field vs.
  keyword rule); the default when ambiguous; whether NC needs a distinct `.elmt`.
- **Touches:** `src/symbol_db/`, the matcher + `add_symbol_element` in
  `src/logix_to_qet.py`. No physical-pin guessing.

## T3.2 — Spare-point rendering
- **Goal:** draw a card's unused/reserved points (the ones currently skipped) as
  spare terminals so the terminal strip/card is complete for the panel builder.
- **Acceptance sketch:** spare points render as plain terminals (no device, no
  invented tag); clearly marked as spare; counted honestly in the summary. Must
  not inflate the matched/false-positive counts or change the 75-match floor.
- **Open decisions (gate):** which points count as "spare" (card capacity vs.
  mapped); label/format; whether spares appear in the BOM.
- **Touches:** `collect_points`/`per_module` assembly and `build_folio` in
  `src/logix_to_qet.py`. **Floor risk** — verify 106/75/0 carefully.

## T3.3 — Column pagination on card overflow
- **Goal:** when a card has more points than one column/sheet holds, paginate
  across columns/sheets instead of overflowing the page frame.
- **Acceptance sketch:** a card exceeding the per-column capacity flows to the next
  column (already 2 columns) and, if still over, a continued folio; geometry stays
  inside the page frame; title block + folio numbering stay correct.
- **Open decisions (gate):** the overflow threshold; continue-on-same-folio vs.
  a `(2/2)` continuation folio; how the BOM/summary reference a paginated card.
- **Touches:** `COL_X`/`POINTS_PER_COL`/`ROW_*` geometry + `build_folio`. The
  ~660-px folio height is already near-full at 16 rows — mind the box bounds.

## T3.4 — PE / ground potentials
- **Goal:** show protective-earth / ground references on devices that need them
  (complements the PE rail already on the `Alimentación` folio from Tier 2 #5).
- **Acceptance sketch:** devices/cards that declare an earth reference draw a PE
  terminal/symbol cross-referenced to the PE rail; data-driven (module_db/symbol_db),
  never invented; pins stay TBD if unknown.
- **Open decisions (gate):** PE as a per-device terminal vs. a symbol; which
  devices get one; label style (Spanish, consistent with the cajetín).
- **Touches:** `module_db` power block (already has the structure) + `symbol_db`;
  `add_power_terminals`/`build_folio`; the `Alimentación` rail set.

## T3.5 — Additional languages (IT / DE / ZH)
- **Goal:** match tag/description text in Italian, German, Chinese the same way
  EN/ES work today — **pure data**, no English assumptions in code.
- **Acceptance sketch:** new keyword/abbreviation entries in the language-agnostic
  DBs; an existing or sample multilingual L5X matches without code changes.
- **Open decisions (gate):** which language(s) a real project actually needs
  (don't build speculative data). Pull in on demand only.
- **Touches:** the keyword/abbreviation databases consumed by `l2e` + the matcher.

---
*Maintained by the orchestrator. When an item lands: flip its status, add its
commit hash, and refresh `docs/HANDOFF-next-cycle.md`.*
