export const meta = {
  name: 'adversarial-dev-cycle',
  description: 'Run one PLC→mini-EPLAN backlog item through implement → adversarial review (3 lenses) → WADDING_1 validation gate → verdict. Does NOT commit — leaves that to the human/orchestrator gate.',
  phases: [
    { title: 'Plan',     detail: 'pick the backlog item + acceptance criteria' },
    { title: 'Implement', detail: 'single writer implements the item in the working tree' },
    { title: 'Review',    detail: 'three adversarial lenses inspect the diff in parallel' },
    { title: 'Validate',  detail: 'run the WADDING_1 hard gate + structural assertions' },
    { title: 'Verdict',   detail: 'synthesize ship-ready / needs-rework' },
  ],
}

// ---- Shared context every agent must respect -----------------------------
const GUARDRAILS = `
NON-NEGOTIABLE GUARDRAILS (from ProductPlanEnhancement.md):
- Python 3.10+, STANDARD LIBRARY ONLY. Multilingual DBs stay language-agnostic.
- Never guess physical pin numbers — module_db pins stay "TBD" (rendered __).
- Never force an uncertain symbol/designation — low-confidence keeps the generic terminal.
- WADDING_1 is the floor and must NOT regress: 10 folios, 106 points, 75 symbols matched, 0 false positives.
- Public-repo hygiene: never stage anything under Fixtures/ or any *.L5X / *.qet / *_eplan.csv / personal file.
- The validation command is: python src/logix_to_qet.py Fixtures/WADDING_1.L5X -o Fixtures/WADDING_1.qet
`.trim()

// args may be: a string (item description), or { item, acceptance }, or undefined.
const itemText = typeof args === 'string' ? args : (args && args.item) || null
const acceptance = (args && args.acceptance) || null

// ---- Schemas --------------------------------------------------------------
const IMPL_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['item', 'summary', 'filesChanged', 'completed'],
  properties: {
    item: { type: 'string', description: 'The backlog item that was worked' },
    summary: { type: 'string', description: 'What was changed and why, in 2-4 sentences' },
    filesChanged: { type: 'array', items: { type: 'string' }, description: 'Repo-relative paths touched' },
    approach: { type: 'string' },
    completed: { type: 'boolean', description: 'true only if the change is fully implemented (not partial)' },
    notes: { type: 'string', description: 'Anything the reviewers/validator should know' },
  },
}

const REVIEW_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['lens', 'blocking', 'findings'],
  properties: {
    lens: { type: 'string' },
    blocking: { type: 'boolean', description: 'true if at least one finding must be fixed before shipping' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['severity', 'title', 'detail'],
        properties: {
          severity: { type: 'string', enum: ['blocker', 'major', 'minor', 'nit'] },
          title: { type: 'string' },
          detail: { type: 'string' },
          file: { type: 'string' },
        },
      },
    },
  },
}

const VALIDATE_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['ran', 'pass', 'matchCount', 'falsePositives', 'assertions'],
  properties: {
    ran: { type: 'boolean', description: 'did the generator command run to completion' },
    pass: { type: 'boolean', description: 'true only if every assertion passed AND no regression' },
    folios: { type: 'number' },
    points: { type: 'number' },
    matchCount: { type: 'number', description: 'symbols matched; floor is 75' },
    falsePositives: { type: 'number', description: 'must be 0' },
    assertions: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['name', 'pass', 'evidence'],
        properties: {
          name: { type: 'string' },
          pass: { type: 'boolean' },
          evidence: { type: 'string' },
        },
      },
    },
    output: { type: 'string', description: 'the stderr summary line(s) from the generator' },
  },
}

const VERDICT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['shipReady', 'blockers', 'summary'],
  properties: {
    shipReady: { type: 'boolean' },
    blockers: { type: 'array', items: { type: 'string' } },
    suggestedCommitMessage: { type: 'string', description: 'one line naming the manual step removed; empty if not ship-ready' },
    summary: { type: 'string' },
  },
}

// ---- Phase 1: Plan --------------------------------------------------------
phase('Plan')
const plan = await agent(
  `You are scoping ONE backlog item for the PLC→mini-EPLAN product.
Read {project-root}/ProductPlanEnhancement.md.
${itemText
    ? `The user has chosen this item: "${itemText}".`
    : `Pick the NEXT unstarted item in the plan's Recommended order (Tier 1 first: device designations → wire numbers → BOM folio).`}
${acceptance ? `Acceptance criteria provided by the user: ${acceptance}` : 'Derive crisp, testable acceptance criteria from the plan text for this item.'}

Return the item title and acceptance criteria. Do not write code yet.
${GUARDRAILS}`,
  {
    phase: 'Plan',
    label: 'plan:scope-item',
    schema: {
      type: 'object',
      additionalProperties: false,
      required: ['item', 'acceptance'],
      properties: {
        item: { type: 'string' },
        acceptance: { type: 'array', items: { type: 'string' } },
        manualStepRemoved: { type: 'string' },
      },
    },
  }
)

if (!plan) {
  log('Plan phase failed — aborting cycle.')
  return { aborted: true, reason: 'could not scope a backlog item' }
}
log(`Item: ${plan.item}`)

// ---- Phase 2: Implement (single writer, working tree) ---------------------
phase('Implement')
const impl = await agent(
  `You are Amelia, a senior engineer implementing ONE backlog item in the working tree of this repo.

ITEM: ${plan.item}
ACCEPTANCE CRITERIA:
${(plan.acceptance || []).map((a, i) => `  ${i + 1}. ${a}`).join('\n')}

Implement it fully with test-first discipline where practical. Edit the real files under src/.
Keep the change focused to THIS item only. Do not touch Fixtures/ or generated artifacts.
When done, leave the working tree with your changes in place (do NOT commit, do NOT git add).
Report exactly which files you changed.

${GUARDRAILS}`,
  { phase: 'Implement', label: 'implement', schema: IMPL_SCHEMA, agentType: 'bmad-agent-dev' }
)

if (!impl || !impl.completed) {
  log('Implementation incomplete — sending to verdict as needs-rework.')
}

// ---- Phase 3: Adversarial review — 3 orthogonal lenses in parallel --------
phase('Review')
const LENSES = [
  {
    key: 'cynic',
    prompt: `You are a cynical, jaded reviewer with zero patience for sloppy work. Inspect the uncommitted change (run \`git diff\` and \`git status\`). Assume problems exist. Be skeptical of everything; look for what's MISSING, not just what's wrong. Professional tone, no profanity.`,
  },
  {
    key: 'edge-case',
    prompt: `You are an exhaustive edge-case hunter. Walk every branching path and boundary condition introduced by the uncommitted change (run \`git diff\`). Report ONLY unhandled edge cases: empty/missing data, multilingual input, low-confidence matches, spare points, overflow, TBD pins, malformed L5X. Method-driven, not attitude-driven.`,
  },
  {
    key: 'guardrail-auditor',
    prompt: `You are the guardrail & acceptance auditor. Inspect the uncommitted change (run \`git diff\`). Verify EVERY acceptance criterion is met AND no guardrail is violated:
ACCEPTANCE: ${(plan.acceptance || []).join(' | ')}
Flag as a BLOCKER any: non-stdlib import; hardcoded English assumption; a guessed pin number (must stay "TBD"); a forced/low-confidence symbol or designation; any staged Fixtures/ or *.L5X/*.qet/*_eplan.csv path. If an acceptance criterion is unverifiable from the diff, that is a finding.`,
  },
]

const reviews = (await parallel(
  LENSES.map((lens) => () =>
    agent(
      `${lens.prompt}

Context — the change implements backlog item "${plan.item}".
Implementer's summary: ${impl ? impl.summary : '(implementation did not complete)'}

Find concrete, specific issues with file references. A review with zero findings is suspicious — look harder before claiming the change is clean.
${GUARDRAILS}`,
      { phase: 'Review', label: `review:${lens.key}`, schema: REVIEW_SCHEMA }
    ).then((r) => (r ? { ...r, lens: r.lens || lens.key } : null))
  )
)).filter(Boolean)

const blockingFindings = reviews
  .filter((r) => r.blocking)
  .flatMap((r) => r.findings.filter((f) => f.severity === 'blocker' || f.severity === 'major').map((f) => `[${r.lens}] ${f.title}: ${f.detail}`))
log(`Reviews complete: ${reviews.length} lenses, ${blockingFindings.length} blocking/major findings.`)

// ---- Phase 4: Validate — the hard gate ------------------------------------
phase('Validate')
const validation = await agent(
  `Run the WADDING_1 validation hard gate for the PLC→mini-EPLAN generator.

1. Run: python src/logix_to_qet.py Fixtures/WADDING_1.L5X -o Fixtures/WADDING_1.qet
   (If Fixtures/WADDING_1.L5X does not exist locally, set ran=false and explain — the gate cannot pass without it.)
2. Capture the stderr summary (folios / points / symbols matched / false positives).
3. Parse the output .qet and assert each of these, with evidence:
   - terminal ids are unique per diagram
   - every conductor terminal1/terminal2 references an existing terminal id
   - every element 'type' has a matching embedded <definition> in <collection>
4. Confirm match count >= 75 and false positives == 0 (no regression vs the WADDING_1 baseline).

'pass' is true ONLY if the command ran, every assertion passed, matchCount >= 75, and falsePositives == 0.
Do NOT git add or commit the generated .qet — it is plant data and must never be published.
${GUARDRAILS}`,
  { phase: 'Validate', label: 'validate:wadding_1', schema: VALIDATE_SCHEMA }
)

// ---- Phase 5: Verdict -----------------------------------------------------
phase('Verdict')
const validationPass = !!(validation && validation.pass)
const implOk = !!(impl && impl.completed)

const verdict = await agent(
  `Synthesize the final verdict for backlog item "${plan.item}".

IMPLEMENTATION completed: ${implOk}
IMPLEMENTATION summary: ${impl ? impl.summary : '(none)'}
FILES CHANGED: ${impl ? (impl.filesChanged || []).join(', ') : '(none)'}

ADVERSARIAL REVIEW blocking/major findings (${blockingFindings.length}):
${blockingFindings.length ? blockingFindings.map((f) => `  - ${f}`).join('\n') : '  (none reported)'}

VALIDATION gate: ran=${validation ? validation.ran : false}, pass=${validationPass}, matchCount=${validation ? validation.matchCount : '?'}, falsePositives=${validation ? validation.falsePositives : '?'}
VALIDATION assertions:
${validation && validation.assertions ? validation.assertions.map((a) => `  - ${a.name}: ${a.pass ? 'PASS' : 'FAIL'} — ${a.evidence}`).join('\n') : '  (validation did not run)'}

RULES:
- shipReady is true ONLY if: implementation completed, validation passed (gate above), AND there are no unresolved blocker/major findings.
- If not ship-ready, list every concrete blocker the next implement pass must fix.
- If ship-ready, propose a one-line commit message that NAMES the manual drafting step this item removes.
${GUARDRAILS}`,
  { phase: 'Verdict', label: 'verdict', schema: VERDICT_SCHEMA }
)

const shipReady = !!(verdict && verdict.shipReady) && validationPass && implOk && blockingFindings.length === 0

return {
  item: plan.item,
  shipReady,
  implementation: impl,
  reviews,
  validation,
  verdict,
  blockingFindings,
  note: shipReady
    ? 'Ship-ready. Human gate: review the diff, then commit (never stage Fixtures/ or generated artifacts).'
    : 'NOT ship-ready. Re-run a dev cycle addressing the blockers before committing.',
}
