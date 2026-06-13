# Handoff — next dev cycle (Tier 1 #3: Device-index / BOM folio)

> Self-contained handoff so a **fresh agent in a new session** can run the next
> backlog item with no prior context. Written 2026-06-13 after Tier 1 #2.

## Where things stand

- Product: turn a Rockwell **L5X** export into a near-finished QElectroTech I/O
  drawing set. Driver = `ProductPlanEnhancement.md`. Generator = `src/logix_to_qet.py`.
- **Just completed — Tier 1 #2 "Wire numbers".** Every field conductor (the
  conductor wiring an I/O terminal to its matched field-device symbol) now
  carries a **visible** wire number that QElectroTech renders.
  - **Confirmed format (Abel chose in the Plan-phase confirmation):**
    - Default scheme = **EPLAN I/O address verbatim, NO page prefix**
      (`I0.0`, `Q1.3`, `IW100`). Rationale: the byte/word base is derived from
      rack/slot, so addresses are already project-unique — a page prefix would
      add noise. *(This deviates from the original "with page prefix" wording in
      the kickoff; the user overrode it during confirmation.)*
    - Configurable fallback = **`W<page>.<n>`** (`W3.1`, `W3.2`…), a `W`
      wire-class letter + folio page + per-folio count resetting each page.
    - CLI flag `--wire-scheme {address,sequential}`, default `address`.
  - Helper: pure `wire_number(address, page, scheme, counters)` in
    `logix_to_qet.py` (mirrors `next_designation`); returns `None` when there is
    no defined source so the caller leaves `num=""` (no invented numbers — the
    `<defaultconductor>` template stays empty). Tests in
    `src/test_logix_to_qet.py`: pure-helper cases **plus** `build_folio`
    integration tests asserting the call site actually emits `num`.
- Branch **`feat/wire-numbers`** (cut from `feat/device-designations`) holds:
  - `aadcccb` — wire-numbers feature
  - and below it `727be47` (device designations) + `4a9a557` (workflow fix).
- **Neither `feat/device-designations` nor `feat/wire-numbers` is merged to
  `main` yet** at time of writing (Abel was eyeballing in QElectroTech first).
  **First thing to check:** what's merged into `main`?
  - If the wire-numbers work is **merged** → branch fresh from `main`.
  - If **not** → branch from `feat/wire-numbers` so you build on it.
  - Either way: start from a **clean working tree** (`git status` clean).

## Conventions established (reuse them)

- **Page prefix is a per-folio convention, not project-wide continuity.** Device
  designations use it (`-K3.1`); wire numbers use it only in the *sequential*
  fallback (`W3.1`). When a value is already globally unique (EPLAN address),
  Abel prefers **no** prefix. Confirm prefix/format with Abel before
  implementing any new label — don't assume.
- **Never force an uncertain value.** Unmatched/low-confidence → keep the generic
  output; missing/invalid data → graceful fallback (`None` / `""`), never garbage.
- **Pure helper + integration test.** Follow the `next_designation` /
  `wire_number` pattern: a pure, deterministic helper with stdlib unittests,
  AND a `build_folio`-level test so a broken call site can't pass silently.
- One focused commit per backlog item; the message names the manual step removed.
  Infrastructure/unrelated fixes go in their **own** commit.

## The next item — Tier 1 #3: Device-index / BOM folio (or CSV)

From `ProductPlanEnhancement.md`: *"One summary sheet listing every I/O module
(catalog + vendor + description from `module_db`) and every matched field device
(designation, type, tag, address, folio). Pure data we have."*

Code pointers (all in `src/logix_to_qet.py`):
- Module metadata: `load_module_db()` → `vendor` / `description` / `rtb`.
- Per-device data already computed in `build_folio`: the designation
  (`next_designation(...)`), the matched symbol `entry` (type/`dt`), `pt.tag`,
  the EPLAN `address`, and the folio `order` (page). You'd collect these into a
  list as folios are built, then emit one extra summary diagram (or a `.csv`).
- `build_collection()` shows how an extra diagram/section is appended to the
  project before serialization in `main()`.

**Open decisions for the Plan phase to confirm with Abel:** summary **folio vs
CSV** (or both); column set/order; whether to list generic-terminal (unmatched)
points too, or only matched field devices + modules.

## Kickoff prompt — paste this into the new session

```
Run the next dev cycle for the PLC → mini-EPLAN product using the
adversarial-dev-cycle workflow. This is an explicit opt-in to multi-agent
orchestration — run the workflow.

Backlog item: Tier 1 #3, "Device-index / BOM folio" from ProductPlanEnhancement.md.
Emit one summary sheet (or CSV) listing every I/O module (catalog + vendor +
description from module_db) and every matched field device (designation, type,
tag, EPLAN address, folio). Pure data already computed in build_folio — collect
it as folios are built, then emit the summary. Never invent data we don't have
(module_db pins stay TBD; unmatched points stay generic).

Before running, read these so you have full context:
- ProductPlanEnhancement.md (vision, backlog, guardrails, validation)
- docs/BMAD-Orchestration.md (how Rivet + the workflow work)
- docs/HANDOFF-next-cycle.md (current state + conventions)
- src/logix_to_qet.py — esp. build_folio() (where designation/tag/address/folio
  are known), load_module_db(), build_collection(), and main()
- src/test_logix_to_qet.py — the pure-helper + build_folio integration test pattern

In the workflow's Plan phase, CONFIRM with Abel: summary folio vs CSV (or both);
column set/order; whether to include unmatched/generic points — before implementing.

Then invoke:
  Workflow({ name: "adversarial-dev-cycle",
             args: { item: "Device-index / BOM folio",
                     acceptance: "summary of every I/O module (catalog/vendor/description) and every matched field device (designation/type/tag/EPLAN address/folio); pure data already computed, nothing invented; WADDING_1 still 10 folios / 106 points / 75 matches / 0 false positives with the drawing folios unchanged" } })

Hard gate before any commit:
  python src/logix_to_qet.py Fixtures/WADDING_1.L5X -o Fixtures/WADDING_1.qet
must still report ≥75 symbols matched and 0 false positives, with
terminal-id/conductor/definition assertions passing AND the new summary present
and correct. Add/extend stdlib unittests for any new helper. The workflow does
NOT commit — review the Verdict, then commit yourself with one focused message
naming the manual step removed (compiling the device index by hand). NEVER git
add Fixtures/ or any *.L5X / *.qet / *_eplan.csv.

Start state: branch feat/wire-numbers holds Tier 1 #1+#2. First confirm whether
it's merged to main; if yes branch fresh from main, if not branch from
feat/wire-numbers so you build on it. Verify a clean tree before starting.
```

## Hard gate & guardrails (always)

- Validation command:
  `python src/logix_to_qet.py Fixtures/WADDING_1.L5X -o Fixtures/WADDING_1.qet`
- Floor that must NOT regress: **10 folios / 106 points / 75 matched / 0 false
  positives.** Plus: terminal ids unique per diagram; every conductor
  `terminal1`/`terminal2` resolves to an existing id; every element `type` has an
  embedded `<definition>`.
- Python 3.10+, **standard library only.** Multilingual DBs stay
  language-agnostic.
- Never guess physical pin numbers (`module_db` pins stay `"TBD"` → `__`).
- **Public repo:** never `git add` anything under `Fixtures/` or any
  `*.L5X` / `*.qet` / `*_eplan.csv` / personal file (incl. this handoff is fine
  to commit, but the generated artifacts never are).

---
*This file is a convenience handoff; delete it once the next cycle is underway,
or keep it and overwrite for the cycle after.*
