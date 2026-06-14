# Handoff — next dev cycle (Tier 2 #6: Terminal strip / bornero)

> Self-contained handoff so a **fresh agent in a new session** can run the next
> backlog item with no prior context. Written 2026-06-13 after Tier 2 #5.

## Where things stand

- Product: turn a Rockwell **L5X** export into a near-finished QElectroTech I/O
  drawing set. Driver = `ProductPlanEnhancement.md`. Generator = `src/logix_to_qet.py`.
- **Tier 1 COMPLETE** (designations, wire numbers, device-index/BOM).
- **Tier 2 #4 COMPLETE & MERGED to `main`** (ISO 7200 cajetín + changelog folio).
- **Tier 2 #5 COMPLETE on branch `feat/power-supply`** (commit `6b9894d`, **not yet
  merged to `main`, not pushed**). What it added:
  - **Optional `power` block** in `module_db` (`src/module_db/<catalog>.json`):
    `{ "type": "AC"|"DC", "groups": [ { "points":[…], "supply":"L1",
    "common":"N", "supply_pin":"TBD", "common_pin":"TBD" } ] }`. `supply`/`common`
    are **potential names** (L1/N for AC, L+/0V for DC); pins stay `"TBD"` → `__`
    (never guessed, same rule as `wiring[].pin`). Parsed by the pure
    `parse_power_block()` helper; `load_module_db()` exposes `db["power_groups"]`.
  - Five shipped cards modelled per real type: **IA16** one L1/N group; **OA16**
    two isolated groups of 8 (0–7 / 8–15) on L1/N; **IB32** L+/0V. **IF16 / OX8I
    omit the block** (no single supply pair → draw nothing; *never invent*).
  - **Inline** in `build_folio()`: each group's supply + common terminal drawn on a
    **single horizontal lane above the card box** (`POWER_BAND_Y=60`, `POWER_X0=150`,
    `POWER_PAIR_DX=80`, `POWER_GROUP_DX=180`), reusing the embedded `borne_2`
    (no new element type). Each carries a compact `→ /Alim <potential>` **text
    annotation** (a label, *not* a navigable QET cross-ref); multi-group cards get a
    `(G1)`/`(G2)` suffix so isolated groups sharing a potential name stay distinct.
  - **Dedicated `Alimentación` rail folio** appended after the changelog via
    `build_supply_folios()` (mirrors summary/changelog: text + shapes only, empty
    `<elements>`/`<conductors>`) so it inherits the title block. Rails default to the
    standard set `SUPPLY_DEFAULT_RAILS = (L1, N, L+, 24V, 0V, PE)` plus any extra
    potential a card declares.
  - WADDING_1 now = 10 drawing + 3 summary + 1 changelog + **1 supply** = **15 folios**.
    **125 unittests pass.**

## ⚠️ Lessons (carry forward — these keep biting)

- **Don't trust the workflow's `shipReady`.** Tier 2 #5's run *correctly* returned
  `shipReady:false` — but its IMPLEMENT pass shipped a real geometry bug (a power
  terminal's pin extent crossing the card-box border, and the first group drawn at
  **negative x / off-sheet**) that **passed the implementer's own test** because the
  test short-circuited (`y < box_top OR x < box_left` is always true when every
  `y` is small). Always read each review lens yourself and re-derive the numbers.
- **Positional/visual tests must assert the FULL symbol extent against the REAL
  frame, not the center point.** `borne_2` pins reach `y ± 10` and `x … x+10`; assert
  `y+10 ≤ box_top` and `x ≥ 0`, not just the hotspot. A "floor" regression test must
  assert the actual numbers (parse `main()`'s stderr summary for `106 points` /
  `75 matched`), never a proxy like "folio count grew".
- **The band above the card box is tiny (~36 px: sub-header y≈44 → box top
  ROW_Y0−20 = 80).** You can't stack two terminals there; use a single horizontal
  lane. Long Spanish words (`Alimentación`) don't fit as per-terminal labels — use a
  compact tag inline (`→ /Alim`) and put the full word on the dedicated folio.
- **QET caches title-block templates at startup** — fully *restart* QET (not just
  reopen) to see `.titleblock` edits.
- **Reverse-engineer QET markup from shipped examples**
  (`C:\Program Files\QElectroTech\examples\`, e.g. `iso_sfc_example.qet`), don't guess.

## Conventions established (reuse them)

- **Confirm format with Abel in the Plan phase before implementing.** He has strong,
  specific preferences and iterates visually (exports a PDF / screenshots the folio).
  Use `AskUserQuestion` with concrete option previews; offer to launch QET on output.
- **Never force / never invent.** Unmatched → generic; missing/ambiguous data →
  graceful fallback (`None`/`""`/drop the group), never garbage. Pins stay `"TBD"` → `__`.
- **Pure helper + integration + regression test.** A deterministic stdlib helper,
  a `build_*`/`main`-level integration test, and a regression test for any invariant
  you claim — and make the assertion exercise the real invariant (see Lessons).
- **Presentation must actually render.** A legibility feature must be tested for
  frame/box/sheet bounds AND eyeballed in QET. Structure passing tests is necessary
  but not sufficient.
- One focused commit per backlog item; message names the manual step removed.
  Doc/handoff changes go in their **own** commit. Footer:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## The next item — Tier 2 #6: Terminal strip (bornero)

From `ProductPlanEnhancement.md`:
> Insert a **numbered terminal block** inline on each field conductor between the
> card terminal and the field device, **or** a dedicated strip folio per card.
> Classic EPLAN output, big manual task.

Removes the manual step of drawing the terminal strip (regletero/bornero) and
numbering its terminals.

Code pointers (all in `src/logix_to_qet.py` unless noted):
- `build_folio()` (~line 760) draws each point's I/O terminal and, for matched
  points, the field-device symbol wired by a conductor
  (`add_conductor(conductors, term_ids[2], pin_ids[west], num)` ~line 862). A bornero
  terminal would be **inserted on that conductor**: split it into card-terminal →
  **strip terminal** → device, or collect strip terminals for a per-card strip folio.
- `add_terminal_element()` / `_add_element()` (~lines 629 / 537) place a `borne_2`
  instance and allocate diagram-unique pin ids — reuse for the strip terminals.
- For a **dedicated strip folio**, mirror `build_supply_folios()` /
  `build_summary_folios()` / `build_changelog_folios()` (the "append a folio after the
  drawing folios → it inherits the title block in `attach_titleblocks`" pattern) and
  insert it in `main()` (~line 1180) **before** `attach_titleblocks`.
- Terminal numbering: reuse the deterministic per-folio counter idea from
  `next_designation()` / `wire_number()` (pure, repeatable). A strip terminal number
  is typically `-X<n>:<term>` (EPLAN style) — keep it data-driven, language-agnostic.

**Open decisions for the Plan phase to confirm with Abel:**
- **Inline on each conductor** (a numbered terminal between card and device, per
  point), a **dedicated strip folio per card**, or **both** (matches how he did #5)?
- Terminal **numbering scheme** (sequential per card? per project? `-X1:1`, `-X1:2…`?)
  and the strip **designation** (`-X1` per card? one project strip?).
- Which conductors get a strip terminal — every field device, or also the
  generic/unmatched points? (Spare-point handling is Tier 3, so likely matched only.)
- Geometry: where the strip terminal sits on the row without colliding with the
  existing terminal / symbol / wire-number text (the row is already busy — see the
  tight-canvas lessons above).

## Kickoff prompt — paste this into the new session

```
Run the next dev cycle for the PLC → mini-EPLAN product. Backlog item: Tier 2 #6,
"Terminal strip (bornero)" from ProductPlanEnhancement.md — insert a numbered
terminal block on each field conductor (card terminal → strip terminal → device)
and/or a dedicated strip folio per card. Reuse the established patterns; never
invent data; terminal numbers are deterministic and language-agnostic.

Before implementing, read for full context:
- ProductPlanEnhancement.md (vision, backlog, guardrails, validation)
- docs/HANDOFF-next-cycle.md (this file — current state, lessons, conventions)
- docs/BMAD-Orchestration.md (how Rivet + the dev cycle work)
- src/logix_to_qet.py — build_folio() and its add_conductor call, add_terminal_element,
  the build_*_folios "append a folio + it gets the title block" pattern, main()
- src/test_logix_to_qet.py (pure-helper + integration + regression test patterns)

CONFIRM with Abel in the Plan phase (AskUserQuestion, with previews): inline vs
dedicated strip folio (or both); the terminal numbering scheme + strip designation;
which conductors get a strip terminal; row geometry to avoid collisions.

Hard gate before any commit:
  python src/logix_to_qet.py Fixtures/WADDING_1.L5X -o Fixtures/WADDING_1.qet
must still report 10 drawing folios / 106 points / 75 matched / 0 false positives,
with terminal-id/conductor/definition assertions passing AND the ISO 7200 title
block on every folio AND the changelog + Alimentación folios intact. Run the full
unittest suite from src/ (python -m unittest test_logix_to_qet). Eyeball the .qet in
QET (fully restart QET to reload title-block templates). One focused commit naming
the manual step removed. NEVER git add Fixtures/ or any *.L5X / *.qet / *_eplan.csv
/ *_bom.csv.

Start state: Tier 2 #5 is on branch feat/power-supply (commit 6b9894d), NOT yet
merged to main. Decide with Abel whether to merge feat/power-supply into main first
(prior cycles fast-forwarded feature branches into main), then branch fresh from the
integration point with a clean tree.
```

## Hard gate & guardrails (always)

- Validation command:
  `python src/logix_to_qet.py Fixtures/WADDING_1.L5X -o Fixtures/WADDING_1.qet`
- Floor that must NOT regress: **10 drawing folios / 106 points / 75 matched / 0
  false positives.** Plus: terminal ids unique per diagram; every conductor
  `terminal1`/`terminal2` resolves to an existing id; every element `type` has an
  embedded `<definition>`; the **ISO 7200 title block on every folio** (no raw
  `%{tokens}`); the **changelog** and **Alimentación** folios present. Run the full
  unittest suite (`python -m unittest test_logix_to_qet` from `src/`).
- Python 3.10+, **standard library only.** Multilingual DBs stay language-agnostic.
- Never guess physical pin numbers (`module_db` pins stay `"TBD"` → `__`).
- **Public repo:** never `git add` anything under `Fixtures/` or any
  `*.L5X` / `*.qet` / `*_eplan.csv` / `*_bom.csv` / personal file. Company assets
  (`assets/exxerpro.titleblock`, the logo **SVG**) are committed; the `.png/.bmp/.ai`
  logo exports are intentionally untracked.

---
*This file is a convenience handoff; overwrite it for the cycle after Tier 2 #6.*
