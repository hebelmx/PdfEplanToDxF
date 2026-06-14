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
| DA.5b | DISPLAYED sectioned page number (000/001/100…) in the cajetín | `done` `7b2151b` | Hand-numbering the cajetín with section gaps |
| DA.6 | Hide schematic grid rulers (0–16 / A–H) on the non-schematic list/front-matter folios — they drew tables "out of the box" over the rulers | `done` `7b2151b` | — (review fix) |
| DA.7 | Lift the card header so the inline power band stops overprinting the sub-header on I/O drawings | `done` | — (review fix) |
| DA.5c | prev/next continuation refs ("viene de / sigue en") | `done` `c2ba9b7` | Hand-writing the continuation references |
| DA.8 | PDF-review layout fixes (power table top-right; símbología 2 columns; remove struck-through header rules; lift Alimentación rail labels; lift card-box title) | `done` `95515a5`+`c40d95f` | Hand-fixing each readability defect after a print review |

- **Coverage / floor unchanged:** 10 drawing folios / 106 points / 75 matched / 0 FP.
  WADDING_1 now emits **27 folios** in the gated order: Portada → Simbología →
  Alimentación → drawings (101–110) → borneros (200–209) → BOM (300–302) →
  Historial (900). Full suite **185 tests** green (DA.5c + DA.8 review fixes).
- **DA.8 (review fixes, `95515a5`, status `review` pending Abel's re-eyeball):**
  from Abel's QET/PDF review — (F1) the inline power band overlapped on
  multi-group cards → moved to a boxed table in the clear top-right corner
  (gated choice; text+box only, no terminal element); (F2) símbología ran
  symbols off the page into the cajetín → 2 columns/folio + pagination; (F3)
  the header rule struck through the header text on símbología/bornero/BOM/
  historial → removed; (F4) Alimentación rail labels touched their lines →
  lifted 22px clear. Floor + 27 folios unchanged; geometry re-verified from
  the generated .qet. **Abel blessed DA.8 in QET 2026-06-14 → the whole
  Document-assembly theme (DA.1–DA.8) is COMPLETE.** Remaining: Tier 3 (T3.1
  first) + the human gate to ff-merge `feat/doc-assembly` into `main`.
- **DA.1 decision:** extract Abel's newest embedded template (82KB) from
  `Fixtures/WADDING_1.qet` into the committed asset (76KB, stale, older logo SVG).
- **Gated decisions (2026-06-14):** section page scheme = sectioned-with-gaps
  (000/001/100/101–110/200+/300+/900); designations FOLLOW the printed page
  (Abel's choice, overriding the handoff's NO recommendation) → schematic devices
  are now -K101.x … -K110.x. Cover = full title-block metadata set; legend =
  glyph + Spanish name. See memory `da-numbering-decisions`.
- **DA.5b done `7b2151b`:** QET eyeball confirmed it numbers by POSITION (the
  export showed 24/27 on the BOM). Fixed generator-side: `sectionize_titleblock_page`
  rewrites `%{folio-id}/%{folio-total}` → custom `%{page}` in the EMBEDDED copy
  only; `apply_titleblock` fills it per folio from the diagram order, zero-padded
  (000/001/100/101…/900). `assets/exxerpro.titleblock` untouched (re-syncable).
- **DA.6 done `7b2151b`:** Abel's "BOM out of the box" — list/front-matter folios
  drew tables over QET's schematic grid rulers; hid the rulers
  (displaycols/displayrows=false) on the non-schematic folios; drawings keep them.
- **DA.5c done `c2ba9b7`:** gated with Abel 2026-06-14 — arrow+page format
  (`◄ pág. X` back / `pág. Y ►` forward), both on the bottom lane near the
  cajetín, page = SECTION page (DA.5b's `order`), on drawings (101–110) +
  borneros (200–209) + BOM (300–302); single-folio sections excluded.
  `add_continuation_refs()` groups by section page range and stamps neighbours'
  pages as `<input>` text only (pure annotation; floor/folio counts untouched).
  Full suite **180 tests**. **DA theme COMPLETE → next = Tier 3 (T3.1).**

---

| # | Item | Status | Manual step removed |
|---|------|--------|---------------------|
| T3.1 | NO/NC correctness on symbols | `done` `9518e77` | Hand-flipping each contact to its real normally-open/closed state |
| T3.2 | Spare-point rendering | `done` `3aa6187` | Hand-drawing unused/reserved card points so the strip is complete |
| T3.3 | Column pagination on card overflow | `deferred` [#1](https://github.com/hebelmx/PdfEplanToDxF/issues/1) | Manually splitting a high-point-count card across sheets/columns |
| T3.4 | PE / ground potentials | `todo` | Hand-drawing the protective-earth / ground references on devices |
| T3.5 | Additional languages (IT/DE/ZH) — pure data | `todo` (demand-driven) | Manual re-matching when a project ships in another language |

Recommended order: **T3.1 ✅ → T3.2 ✅ → ~~T3.3~~ (deferred, issue #1) → T3.4**, then
**T3.5 only when a project demands it** (the plan calls the extra languages drop-in
pure data, not a must-do). **T3.3 deferred 2026-06-14** — no card in the wild yet
exceeds 32 channels (the 2×16 one-folio limit); 64-ch cards exist but are rarely seen,
so it's tracked in **issue #1** and implemented when needed. **NEXT actionable = T3.4.**

---

## T3.1 — NO/NC correctness on symbols
- **Goal:** render each matched contact/symbol in its true normally-open vs
  normally-closed state instead of a single default, so the schematic is
  electrically correct without a manual flip.
- **Acceptance sketch:** `symbol_db` entries carry/derive the NO/NC variant (data,
  not code assumptions); the matcher picks the right variant from the tag/desc
  (e.g. `_NC`, `PARO`/e-stop ⇒ NC); low-confidence keeps the current default
  (never force). The placed element + its `<definition>` reflect the variant.
- **Open decisions — RESOLVED with Abel 2026-06-14** (memory `t3-no-nc-decisions`):
  - **NC signal = extend the separate-entry pattern** (`_nc` JSON + `.elmt` matched by
    DB keywords/suffixes — as push_button_nc/limit_switch_nc/emergency_stop already do).
    No NO/NC logic in Python; data-driven & language-agnostic.
  - **Scope this pass = switches with real NC forms QET ships:** level, flow, pressure,
    foot switch, thermostat (`limit_switch_nc` already exists). Sensors/lights/coils/
    valves stay single-state.
  - **Ambiguous default = keep current default (NO)** — never force.
  - **Glyphs extracted (no guessing) from `C:\Program Files\QElectroTech\elements`:**
    `niv_liquide_nf`, `debit_fluide_nf`, `pressostat_nc`, `thermostat_nc`, `foot_nc`.
- **Touches:** `src/symbol_db/` (5 new `_nc` JSON + `.elmt`), matcher already handles
  variant selection via keywords. No physical-pin guessing.
- **DONE `9518e77`:** added level/flow/pressure/foot/thermostat `_nc` entries (real
  QET-library glyphs, 2 terminals each). **Bug fixed:** the pre-existing
  `limit_switch_nc` was unreachable — its keywords overlap the base, tying the score
  and losing by id-sort; `priority=1` breaks the tie only when the NC keyword fires
  (added ES "cerrado" for parity). No Python change — fully data-driven. Verified:
  194 tests green; WADDING_1 floor 10/106/75/0 FP, 27 folios, limit_switch still 17
  (the new NC entries are pure added capability — no fixture variant flipped).

## T3.2 — Spare-point rendering
- **Goal:** draw a card's unused/reserved points (the ones currently skipped) as
  spare terminals so the terminal strip/card is complete for the panel builder.
- **Acceptance sketch:** spare points render as plain terminals (no device, no
  invented tag); clearly marked as spare; counted honestly in the summary. Must
  not inflate the matched/false-positive counts or change the 75-match floor.
- **Open decisions — RESOLVED with Abel 2026-06-14** (memory `t3-spare-decisions`):
  - **Spare = empty channel slots** (capacity − mapped); box is already sized to
    `mod.points`. ~62 in WADDING_1. NOT the l2e "skipped" records.
  - **Label = terminal + "RESERVA" + channel** (no device, no invented tag).
  - **In the BOM (new `spare` category) AND on the bornero strip**, counted
    SEPARATELY — matched stays 75, mapped "drawn" stays 106, 0 FP.
- **Touches:** `build_folio` (spare-slot loop), `_add_bornero_diagram`, a new
  `spare_bom_row`, the stderr summary. **Floor risk** — n_points/matched/FP must NOT
  change; spares are a new separate count. BOM grows ~62 rows ⇒ summary folios gain
  ~2 (total rises above 27, expected) — verify DA.5c continuation refs still attach.
- **DONE `3aa6187`:** 62 reserve terminals over 10 cards (RESERVA in the strip lane +
  bornero + BOM `spare` category, designation/type blank). Floor HELD 10/106/75/0;
  spares counted separately. New totals: BOM 178 rows over 5 folios, bornero 11 folios
  (REM_IN_1's 32 ch paginates 2 sheets), **30 folios total** (was 27). 203 tests green;
  ids unique / conductors resolve / no token leak; continuation refs chain 300→304.
  **Pending Abel: QET eyeball of the spare terminals + grown BOM/bornero.**

## T3.3 — Column pagination on card overflow  ⏸ DEFERRED (issue #1)
- **Deferred 2026-06-14 (Abel):** no card seen in the wild exceeds the 2×16=32 one-folio
  limit (64-ch cards exist but are rare); a >32 card currently overprints column 2.
  Tracked in [issue #1](https://github.com/hebelmx/PdfEplanToDxF/issues/1) with the gated
  design (≥32 ⇒ `(n/m)` continuation folio; one module BOM row, rows carry their folio;
  designations/wire numbers continue across sheets). Implement when a real >32 card appears.
- **Goal:** when a card has more points than one column/sheet holds, paginate
  across columns/sheets instead of overflowing the page frame.
- **Acceptance sketch:** a card exceeding the per-column capacity flows to the next
  column (already 2 columns) and, if still over, a continued folio; geometry stays
  inside the page frame; title block + folio numbering stay correct.
- **Open decisions (gate):** the overflow threshold; continue-on-same-folio vs.
  a `(2/2)` continuation folio; how the BOM/summary reference a paginated card.
- **Touches:** `COL_X`/`POINTS_PER_COL`/`ROW_*` geometry + `build_folio`. The
  ~660-px folio height is already near-full at 16 rows — mind the box bounds.

## T3.4 — PE / ground (chassis grounding folio)  ← NEXT (scoped, handed off)
- **Scope RESOLVED with Abel 2026-06-14** (memory `t3-pe-grounding-decisions`): NOT
  per-I/O-point PE. Build a dedicated **grounding / "Puesta a tierra" folio** modeled on
  AB **1756-IN621** pp. 12–14 (`docs/1756-in621_-en-p.pdf`, "Grounding Configuration
  Example"): each chassis/rack draws a **Functional Earth (FE, 8 AWG / 8.3 mm²)** + a
  **Protective Earth (PE, 14 AWG / 2.1 mm², torque 1.35 N·m; optional 2nd PE)**, all
  running to a **central Ground Bus**, which connects to the **Grounding-electrode System**
  (min 8 AWG). Gauges per the manual (standard defaults, ideally configurable; never
  invent site data).
- **Build like the other dedicated folios:** a new `build_grounding_folio(...)` like
  `build_supply_folios`/`build_portada_folio` — **text + shape primitives only, empty
  <elements>/<conductors>** (inherits the title block, zero floor impact). Own SECTION
  page near Alimentación (100-series).
- **Still to gate (VISUAL folio — Abel iterates):** Spanish labels; one folio for all
  chassis vs per-chassis; chassis as boxes vs nodes; section-page placement; gauges fixed
  vs project_template config. Offer a QET eyeball.
- **Touches:** new `build_grounding_folio` + its SECTION constant in `main()`; rack data
  from the parsed L5X. Floor 10/106/75/0 must hold (additive folio).

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
