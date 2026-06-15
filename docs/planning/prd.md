---
title: PLC → mini-EPLAN — Phase 2
created: 2026-06-14
updated: 2026-06-14
status: draft
---

# PRD: PLC → mini-EPLAN — Phase 2
*Multi-vendor (Siemens) + LLM-aided diagrams. Working title — confirm.*

## 0. Document Purpose

For the orchestrator and the dev agents it delegates to. Builds on
`docs/planning/brief.md` (business case) and does not duplicate it; architecture
lives in `docs/logix-to-qet-guide.md`; Phase-1 history in `ProductPlanEnhancement.md`
and `docs/TIER3-tracker.md`. Vocabulary is Glossary-anchored; features are grouped
with globally numbered FRs nested; assumptions are tagged inline and indexed in §9.
Decisions are logged in `docs/planning/.decision-log.md`. This PRD feeds
epics/stories (`docs/planning/epics.md`).

## 1. Vision

PLC → mini-EPLAN turns a PLC program export into a near-finished QElectroTech
drawing set so a controls engineer drafts *from* it instead of hand-building it.
Phase 1 shipped this for Rockwell ControlLogix (one `.L5X` → a 32-folio set under
an ISO 7200 title block, anchored by a hard regression gate).

Phase 2 keeps the same renderer and extends it three ways that real upcoming work
needs: a **vendor-neutral core** so the tool stops being Rockwell-only; **Siemens
SIMATIC** support (S7-1200/1500 and legacy S7-300) added as front-end parsers
behind that core; **new diagram types** — led by a network/communications topology
folio that's nearly free because the data is already parsed — plus an **LLM-aided
path** for sheets the PLC program can't contain (power one-line / panel), where the
engineer describes the section in words and a deterministic, tested generator
renders the validated config.

The measure of every feature is unchanged: it must remove a manual finishing step,
and it must never invent data.

## 2. Target User

### 2.1 Jobs To Be Done
- *Draft a complete PLC drawing set in far less time*, for **Siemens projects too**,
  not just Rockwell — start from a generated set and finish the last mile.
- *Trust the output enough to build on it* — never silently guess; a blank or generic
  placeholder beats a wrong symbol/pin/potential.
- *Produce the non-program sheets* (power one-line, panel sections) by **describing**
  them, without hand-drawing, and without the tool fabricating equipment.
- *Extend the tool myself as pure data* — a new module catalog, field device, or
  language — without touching core code.

### 2.2 Non-Users (v1)
- Engineers on PLC platforms no current Exxerpro project uses (CODESYS/Beckhoff,
  Mitsubishi, Omron…) — supported only on demand, not in this phase.
- Anyone needing a GUI — this stays a CLI + JSON + LLM-skill tool.

### 2.3 Key User Journeys
*Single-operator internal tool — journeys kept light (one sentence each).*

- **UJ-1. Abel drafts a Siemens S7-300 retrofit.** He exports the symbol table + a
  hardware-config PDF from STEP 7 on the VM, drops them in `Fixtures/`, curates a
  Siemens module map from the PDF, runs the generator, and opens a QElectroTech set
  he finishes rather than redraws.
- **UJ-2. Abel adds the power section to a job.** He describes the panel's power
  (main breaker, PSU, 24 V distribution, fusing) in plain language to the
  config-authoring skill, gets a validated JSON config, runs the generator, and the
  power one-line folio renders alongside the I/O set.
- **UJ-3. Abel gets a network page for free.** On any existing Rockwell job he reruns
  the generator and the new comms-topology folio shows controller → comms modules →
  remote adapters → drops → HMI, drawn from data already in the export.

## 3. Glossary

- **Folio** — one sheet/`<diagram>` in the QElectroTech project (a card drawing,
  bornero, BOM page, grounding sheet, etc.).
- **Folio builder** — a function that consumes derived data and appends one
  self-contained folio to the project. Adding a diagram type = adding a builder.
- **Renderer** — `logix_to_qet.py`: turns the domain model into folios + the title
  block. Vendor-agnostic.
- **Front-end parser** — a vendor-specific module that translates a native PLC export
  into the IR. Today only Rockwell L5X (`logix_to_eplan_csv.py`).
- **IR / `PlcProject`** — the vendor-neutral intermediate representation: controller →
  chassis/racks → modules (catalog, slot, kind, points) → I/O points (tag, address,
  direction, description) + the comms/parent tree. The contract between parsers and
  the renderer.
- **`module_db`** — JSON, one file per module catalog: vendor, description, RTB,
  per-point names + physical pins (pins `"TBD"` until filled from vendor docs).
- **`symbol_db`** — JSON, one file per field-device type + its QET glyph: keyword/suffix
  match rules and the IEC 81346 class letter.
- **`project_template.json`** — cajetín fields, the `revisions` changelog, and config
  blocks (e.g. `grounding`) consumed by config-driven folios.
- **Config-driven folio** — a folio whose content comes from a JSON config (not from
  the PLC export) rendered by a tested builder. The grounding folio is the template.
- **Floor gate / WADDING_1** — the Rockwell reference project and its invariant
  (**10 drawing folios / 106 points / 75 matched / 0 false positives**) that must
  never regress.
- **Spike** — a time-boxed investigation that produces a decision/finding, not
  shippable code.
- **Fixture** — a real export used to build and test a parser against (gitignored
  plant data, never committed).

## 4. Features

### 4.1 Vendor-neutral core (the IR)
**Description:** Promote today's implicit, Rockwell-named domain model into an
explicit `PlcProject` IR that the renderer consumes, so new vendors plug in as
front-end parsers without touching the renderer. Pure enabling refactor — **no
change to Rockwell output**. Realizes the foundation for UJ-1/UJ-3.

#### FR-1: Explicit `PlcProject` IR
The domain model (controller, chassis/racks, modules, I/O points, comms/parent tree)
is a documented, vendor-neutral structure independent of L5X/Rockwell naming.
**Consequences (testable):**
- The IR carries everything the renderer needs today (verified: every folio builder
  reads only IR fields, no L5X-specific calls).
- The comms/parent tree is represented as first-class IR data (enables FR-4).

#### FR-2: Renderer depends only on the IR
`logix_to_qet.py` references the IR, never Rockwell-specific parsing internals.
**Consequences (testable):**
- Grep/architecture test: the renderer imports the IR, not L5X parsing details.

#### FR-3: Rockwell front-end adapts with zero output change
The existing L5X path becomes a front-end producing the IR; WADDING_1 output is
byte-equivalent (modulo unstable ids) to Phase-1.
**Consequences (testable):**
- WADDING_1 floor holds **10/106/75/0**; full test suite green; folio count and
  section numbering unchanged.

### 4.2 Vendor-independent diagram folios (quick wins)
**Description:** New folio builders that consume only the IR, so they work on Rockwell
today and on Siemens for free once a Siemens parser exists. Parallelizable; immediate
ROI. Realizes UJ-3.

#### FR-4: Network / communications topology folio
A folio drawing the comms tree (controller → EtherNet/IP·ControlNet·Profinet modules →
remote adapters → drops → HMI) from the IR.
**Consequences (testable):**
- On WADDING_1, the folio shows the real tree (`Local → RIO_LOCAL → RIO_RCP → REM_*`
  + HMI); geometry inside the page frame (positional test on full extent); empty
  `<elements>`/`<conductors>`, title block present, no `%{token}` leak; floor holds.

#### FR-5: Drawing index / table-of-contents folio
A folio listing every folio's section page + title.
**Consequences (testable):**
- Lists all folios in document order with correct section pages; updates automatically
  as folios are added; floor holds.

#### FR-6: Rack / chassis layout overview folio
A folio drawing each rack with its modules by slot.
**Consequences (testable):**
- On WADDING_1, both racks render with their modules in slot order, labelled from the
  IR (no invented names); geometry inside the frame; floor holds.

#### FR-7: Purchasing-grouped BOM export
An additional BOM view grouped by vendor/catalog for procurement.
**Consequences (testable):**
- A grouped CSV (and/or folio) is emitted alongside the existing `_bom.csv`; counts
  reconcile exactly with the flat BOM; no invented columns.

### 4.3 Siemens enablement
**Description:** Add Siemens via the IR. A spike first (no speculative code), then
demand-driven parsers built against real fixtures. **TIA Openness is ruled out** — all
input is GUI file exports. Realizes UJ-1.

#### FR-8: Siemens export spike (decision artifact)
A time-boxed spike on real samples (in `Fixtures/`) determines, **per platform**, the
minimal GUI-file-export set that yields *modules+slots+catalogs* **and**
*tag↔address↔comment*, and the parse effort.
**Consequences (testable):**
- A written finding per platform (S7-300, S7-1200, S7-1500): which files, what they
  contain, gaps, and a go/no-go + effort estimate. Output is the decision, not code.
- `[ASSUMPTION]` working hypotheses to confirm: S7-300 = symbol-table/I-O list +
  HW-config PDF; 1200/1500 = PLC tag-table XML + a GUI hardware export (CAx/AML).

#### FR-9: S7-300 front-end parser
Parse the S7-300 symbol table (tag↔address↔comment) into IR I/O points; derive
module/rack grouping from a curated Siemens module map (FR-10).
**Consequences (testable):**
- Against a real S7-300 fixture: IR points carry tag, Siemens address (`I0.0`,
  `Q4.1`, `IW…`), and comment; unmapped/uncertain data degrades to blanks/generics,
  never invented; a Siemens fixture floor (its own counts) is asserted from stderr.

#### FR-10: Siemens module map (data)
A `module_db`-pattern dataset for the Siemens catalogs a project uses, hand-curated
from the HW-config PDF (human-read reference, not auto-parsed).
**Consequences (testable):**
- One JSON per Siemens catalog with the same schema discipline (pins `"TBD"` until
  filled; unknown catalogs degrade gracefully).

#### FR-11: S7-1200/1500 front-end parser
Parse the TIA exports confirmed by FR-8 into the IR.
**Consequences (testable):**
- Against a real 1200/1500 fixture: IR carries modules + tag↔address↔comment from
  GUI exports only (no Openness); same never-invent degradation; its own fixture floor
  asserted. **Gated on FR-8's go/no-go.**

### 4.4 LLM-aided config-driven folios
**Description:** Render design data the PLC can't supply (power one-line / panel) from
a JSON config authored — with LLM help — from the engineer's words. The deterministic,
tested generator renders; the LLM authors **config, never QET code**. Realizes UJ-2.

#### FR-12: Power one-line folio builder
A tested builder renders a power/panel one-line from a config block (mirrors the
grounding folio: text + shape primitives, own section page, inherits the title block).
**Consequences (testable):**
- Renders from a sample config with geometry inside the frame, no `%{token}` leak,
  empty `<elements>`/`<conductors>`; absent config → folio simply not emitted; floor
  holds.

#### FR-13: Power-section config schema + validation
A documented schema (in `project_template.json` or a sibling) with graceful, validated
loading.
**Consequences (testable):**
- Missing/partial/malformed config degrades to safe defaults or omission, never
  garbage; defaults are documented reference values, not invented site data.

#### FR-14: Config-authoring skill/agent
A reusable skill that turns a natural-language description (+ worked examples; the
grounding folio is the gold template) into a **validated** config instance for FR-13.
**Consequences (testable):**
- The skill emits config JSON that passes FR-13 validation and renders via FR-12; it
  **does not** emit QET XML or per-project parser code (builder code is written once,
  in a dev cycle); reusable for future config-driven folio types.

## 5. Non-Goals (Explicit)
- Not building TIA Openness integration (ruled out) — Siemens input is file exports only.
- Not extracting power/panel design data from the PLC program — it isn't there.
- Not letting the LLM emit QET XML or one-off parser code per project.
- Not adding speculative vendors or diagram types no real project needs.
- Not adding a GUI; not adding languages (IT/DE/ZH) until a project demands them.
- Not regressing or "improving" Phase-1 output as a side effect of the refactor.

## 6. MVP Scope

### 6.1 In Scope
- FR-1…FR-3 (IR refactor, floor-preserving).
- FR-4…FR-7 (quick-win folios) — front-loaded for ROI while samples are gathered.
- FR-8 (spike) → FR-9/FR-10 (S7-300) demand-driven against a real fixture.
- FR-12…FR-14 (power folio + config schema + authoring skill), power one-line first.

### 6.2 Out of Scope for MVP
- FR-11 (1200/1500 parser) — **gated on FR-8** and a real TIA fixture; `[NOTE FOR PM]`
  promote the moment a 1200/1500 sample + project lands.
- Any vendor beyond Siemens; any config-driven folio beyond power (the skill is
  reusable, but only power is built now).
- Purchasing-BOM advanced grouping beyond vendor/catalog.

## 7. Success Metrics

**Primary**
- **SM-1**: First real Siemens project drafted *from* a generated set (engineer
  finishes, not redraws). Validates FR-8/FR-9/FR-10.
- **SM-2**: Quick-win folios in use on Rockwell jobs before the first Siemens sample
  lands. Validates FR-4…FR-7.

**Secondary**
- **SM-3**: A power section produced by description (config-authoring skill → render),
  zero hand-drawing. Validates FR-12…FR-14.
- **SM-4**: New vendor/diagram added without modifying the renderer seam. Validates
  FR-1/FR-2.

**Counter-metrics (do not optimize)**
- **SM-C1**: Invented-data incidents = **0**. Never trade trust for coverage —
  counterbalances SM-1/SM-3 (don't guess Siemens pins or power equipment to "look
  complete").
- **SM-C2**: WADDING_1 floor regressions = **0**. Counterbalances SM-4 (don't let the
  refactor or any new feature move the floor).

## 8. Cross-Cutting NFRs / Guardrails *(non-negotiable)*
- **Runtime:** Python 3.10+, **standard library only**; no new dependencies.
- **Never invent data:** uncertainty degrades to a clean placeholder (`TBD`→`__`,
  generic terminal, blank cell) — never garbage. Applies to Siemens pins, symbols,
  and power equipment alike.
- **Language-agnostic data:** databases stay locale-neutral (EN/ES today; IT/DE/ZH as
  pure data on demand).
- **Public-repo hygiene:** never commit anything under `Fixtures/` or any
  `*.L5X`/`*.qet`/`*_bom.csv`/`*.pdf` or Siemens plant export (`*.asc`/`*.seq`/`*.sdf`
  /TIA exports). Commit only code, JSON databases, docs.
- **Test discipline:** one tested builder per folio type, each with floor + positional
  tests asserting the FULL symbol extent against the real frame (not a hotspot) and
  floor numbers parsed from stderr (not a proxy).
- **The WADDING_1 floor never regresses.**
- **Demand-driven:** build a vendor/diagram only when a real project needs it; spikes
  precede speculative parsers.

## 9. Delivery Model *(so epics are delegatable)*
Work flows through BMAD dev cycles + adversarial review under an orchestrator that
**verifies every result from ground truth** (the WADDING_1 gate + unittests + git),
one focused commit per item, feature branch → human merge gate. A subagent's
`shipReady`/summary is a claim, not evidence — the orchestrator re-derives the numbers.

## 10. Risks & Mitigations
- **Siemens export completeness (the real risk).** Openness is out, so everything rides
  on GUI file exports. *Mitigation:* FR-8 spike on real samples, per platform, before
  any parser commitment.
- **Sample/environment latency** (TIA-on-VM is slow). *Mitigation:* sample-gated, not
  date-gated; FR-4…FR-7 deliver value on Rockwell meanwhile.
- **IR refactor silently changing output.** *Mitigation:* FR-3 byte-equivalence + the
  floor gate + full suite.
- **LLM drift on generated artifacts.** *Mitigation:* LLM authors config only; FR-13
  validation; FR-12 floor-gated builder.
- **HW-config-as-PDF for S7-300** isn't auto-parseable. *Mitigation:* treat it as a
  human-read reference for the curated module map (FR-10) — the proven `module_db`
  pattern — not as a parse target.

## 11. Open Questions
1. Which Siemens platform/project is the **first** real fixture (sequences FR-9 vs FR-11)?
2. Does the first TIA export actually carry a complete hardware list without Openness?
   (FR-8 answers this.)
3. Where does the power-section config live — extend `project_template.json` or a new
   `power_template.json` sibling? (FR-13 decision.)
4. Does the topology folio need Profinet-specific representation now, or is the generic
   comms tree enough until a Siemens fixture lands?

## 12. Assumptions Index
- §2.3 / FR-9 — S7-300 export = symbol-table/I-O list + HW-config PDF (to confirm via FR-8).
- FR-8/FR-11 — 1200/1500 = PLC tag-table XML + a GUI hardware export (CAx/AML), no Openness.
- §1 — the comms tree needed for FR-4 is fully present in the L5X today (verified in
  Phase 1) and will be carried by the IR for Siemens.
- Brief — Phase-2 horizon is sample-gated/soft, paced to when Siemens exports land.

---
*Feeds: epics/stories (`docs/planning/epics.md`). Suggested epic mapping —
E1=§4.1 · E2=§4.2 · E3=FR-8+FR-9+FR-10 · E4=FR-11 · E5=§4.4.*
