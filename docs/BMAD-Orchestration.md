# BMAD Orchestration — PLC → mini-EPLAN

This project uses the [BMAD Method](https://bmadcode.com/) (v6.8.0, modules
`bmm` + `bmb`) to grow `logix_to_qet.py` from a script into a product, driven by
`ProductPlanEnhancement.md`. BMAD is installed under `_bmad/` and its agents/skills
under `.claude/skills/bmad-*`. Two custom pieces tie it to this product:

## 1. Rivet — the Delivery Orchestrator (agent persona)

`.claude/skills/bmad-orchestrator-pdfeplan/`

A BMAD-style agent that sequences the dev cycle one backlog item at a time and
enforces the WADDING_1 guardrails. It is **not** managed by the BMAD installer
manifest, so `bmad install --update` will not overwrite it.

**Invoke it:** ask to "talk to Rivet", "run a dev cycle", or "work the next
backlog item". On activation it loads `ProductPlanEnhancement.md` + `README.md`
as persistent facts, states the current backlog position, and shows a menu:

| Code | Action |
|------|--------|
| `DC`  | Run a full adversarial dev cycle on the next backlog item |
| `PRD` | Create/update the product PRD (`bmad-prd`) |
| `AR`  | Capture solution architecture (`bmad-create-architecture`) |
| `SP` / `ST` | Sprint planning / status |
| `CS` / `DS` / `QD` | Create story / dev story / quick dev |
| `CR` / `AV` / `EC` | Code review / adversarial review / edge-case hunt |
| `VAL` | Run the WADDING_1 validation gate |
| `RT`  | Retrospective |
| `HELP`| Ask BMAD what to do next |

The `DC` loop: pick item → `bmad-create-story` → implement → adversarial review →
**WADDING_1 validation hard gate** → commit (human-gated) → stop and report.
A cycle never closes on unverified work, and never regresses the baseline
(10 folios / 106 points / 75 symbols / 0 false positives).

## 2. `adversarial-dev-cycle` — the executable workflow

`.claude/workflows/adversarial-dev-cycle.js`

A deterministic Claude Code Workflow that runs one backlog item through five
phases with real fan-out:

```
Plan → Implement → Review (3 parallel lenses) → Validate → Verdict
```

- **Plan** — scopes the item + acceptance criteria from `ProductPlanEnhancement.md`.
- **Implement** — a single `bmad-agent-dev` writer edits `src/` in the working tree.
- **Review** — three orthogonal adversarial lenses inspect the diff in parallel:
  *cynic* (what's missing), *edge-case hunter* (boundaries/multilingual/TBD/spare),
  *guardrail & acceptance auditor* (stdlib-only, no guessed pins, no forced symbols,
  no staged plant data, every acceptance criterion met).
- **Validate** — runs `python src/logix_to_qet.py Fixtures/WADDING_1.L5X -o Fixtures/WADDING_1.qet`,
  parses the `.qet`, and asserts terminal-id uniqueness, conductor references,
  embedded definitions, `matchCount ≥ 75`, `falsePositives == 0`.
- **Verdict** — `shipReady` only if implementation completed, the gate passed,
  and no blocker/major findings remain. It proposes a commit message naming the
  manual drafting step removed — but **does not commit**; that stays a human gate.

**Run it** (requires explicit opt-in to multi-agent orchestration):

```
Workflow({ name: "adversarial-dev-cycle", args: "device designations" })
# or let it pick the next Tier-1 item:
Workflow({ name: "adversarial-dev-cycle" })
# or with explicit acceptance criteria:
Workflow({ name: "adversarial-dev-cycle",
           args: { item: "wire numbers", acceptance: "<conductor num> populated; QET renders it" } })
```

> Note: the Implement phase mutates the working tree. Run it on a clean branch,
> review the diff, then commit yourself. The workflow never `git add`s anything —
> and the validation phase never publishes the generated `.qet` (plant data).

## Guardrails (enforced by both pieces)

Straight from `ProductPlanEnhancement.md`: stdlib-only Python; never guess pins
(`"TBD"`); never force uncertain symbols; never regress WADDING_1; never stage
`Fixtures/` or `*.L5X` / `*.qet` / `*_eplan.csv` / personal files.
