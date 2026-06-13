---
name: start-dev-cycle
description: Kick off one adversarial dev cycle for the PLC → mini-EPLAN product (logix_to_qet.py) against the ProductPlanEnhancement.md backlog. Self-contained so a fresh agent can start with no prior context. Use when the user says "start the next dev cycle", "work the next backlog item", "run a dev cycle", or names a specific backlog item to implement.
---

# Start Dev Cycle — PLC → mini-EPLAN

Drives **one** backlog item from `ProductPlanEnhancement.md` through the
`adversarial-dev-cycle` workflow: Plan → Implement → Review (3 lenses) →
Validate → Verdict. Built to be invoked cold — load context here, don't rely on
prior conversation.

## Step 1 — Load context

Read these (don't skip — they hold the vision, guardrails, and validation):

- `{project-root}/ProductPlanEnhancement.md` — vision, backlog (Tier 1→3), guardrails, validation command, working style.
- `{project-root}/docs/BMAD-Orchestration.md` — how Rivet + the workflow work.
- `{project-root}/src/logix_to_qet.py` and `{project-root}/src/symbol_db/` — where device types and rendering live.

## Step 2 — Pick the item

- If the user named an item (via args or message), use it.
- Otherwise pick the **next unstarted item in the plan's Recommended order**:
  Tier 1 (device designations → wire numbers → BOM folio) → cajetín → power → borneros.
- Confirm the chosen item with the user in one line before running.

### Acceptance-criteria templates (Tier 1)

- **Device designations** — every matched symbol gets an IEC 81346 class letter
  (limit/proximity/pressure/level/flow sensor → `B`; push button/selector/e-stop/foot
  switch → `S`; relay/contactor coil → `K`; pilot light/horn → `H`; solenoid valve →
  `Y`; aux contact → parent's letter) + sequential number; designation is the symbol
  label; PLC tag stays in function/description text; **no** designation on
  unmatched/low-confidence symbols.
- **Wire numbers** — `<conductor num="">` populated (default = the point's EPLAN
  address; configurable to sequential-per-folio); QET renders the number.
- **BOM / device-index folio** — one sheet or CSV listing every I/O module
  (catalog + vendor + description) and every matched field device (designation,
  type, tag, address, folio), from data we already have.

## Step 3 — Run the workflow

This is an explicit opt-in to multi-agent orchestration. Invoke:

```
Workflow({
  name: "adversarial-dev-cycle",
  args: { item: "<chosen item>", acceptance: "<criteria from the template above>" }
})
```

Or with no `args` to let the workflow auto-pick the next Tier-1 item.

> Run on a **clean working tree** — the Implement phase edits `src/` directly, so
> `git status` should be clean first. The validation phase needs
> `Fixtures/WADDING_1.L5X` present locally (gitignored plant data); without it the
> gate reports `ran=false` and cannot pass.

## Step 4 — Verdict → human commit gate

The workflow does **not** commit. When it returns:

- If **not ship-ready**: report the blockers and offer to re-run a cycle addressing them.
- If **ship-ready**: re-run the WADDING_1 gate yourself to confirm
  (`python src/logix_to_qet.py Fixtures/WADDING_1.L5X -o Fixtures/WADDING_1.qet`,
  ≥75 matches, 0 false positives, structural assertions pass), then commit with **one
  focused message naming the manual drafting step removed**, footer
  `Co-Authored-By: Claude`. Stop and show the result before starting the next item.

## Guardrails (never override)

- Python 3.10+, **standard library only**; multilingual DBs stay language-agnostic.
- Never guess physical pin numbers — `module_db` pins stay `"TBD"`.
- Never force an uncertain symbol/designation — low-confidence keeps the generic terminal.
- Don't regress WADDING_1: 10 folios / 106 points / 75 symbols / 0 false positives is the floor.
- **Never `git add`** anything under `Fixtures/` or any `*.L5X` / `*.qet` / `*_eplan.csv` / personal file.
