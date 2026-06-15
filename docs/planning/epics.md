---
stepsCompleted: ["step-01-validate-prerequisites", "step-02-design-epics", "step-03-create-stories"]
inputDocuments:
  - docs/planning/prd.md
  - docs/planning/brief.md
  - docs/planning/.decision-log.md
  - docs/logix-to-qet-guide.md
  - ProductPlanEnhancement.md
title: PLC → mini-EPLAN — Phase 2 — Epic Breakdown
created: 2026-06-14
updated: 2026-06-14
status: draft
---

# PLC → mini-EPLAN — Phase 2 — Epic Breakdown

## Overview

Decomposes the Phase-2 PRD (`docs/planning/prd.md`, FR-1…FR-14) into 5 epics and
their stories. Each story is **one cohesive, independently-verifiable, single-commit
unit** suitable for the `adversarial-dev-cycle` and delegation by the orchestrator.
No UX or Architecture spec exists (CLI/backend tool); the architecture context is
`docs/logix-to-qet-guide.md` (the parser→renderer seam + the JSON data model).

**Sequencing at a glance:** E1 (IR) and E2 (quick-win folios) are **actionable now**
on Rockwell and front-load value. E3/E4 (Siemens) are **sample-gated** — do not
schedule a parser story before its real fixture lands in `Fixtures/`. E5 (LLM config
folios) is independent and can run in parallel with E1/E2.

## Requirements Inventory

### Functional Requirements
- **FR-1** Explicit vendor-neutral `PlcProject` IR (controller→chassis→modules→points + comms tree).
- **FR-2** Renderer depends only on the IR, not Rockwell parsing internals.
- **FR-3** Rockwell L5X path adapts to produce the IR with byte-equivalent output (floor holds).
- **FR-4** Network / communications topology folio (from the IR comms tree).
- **FR-5** Drawing index / table-of-contents folio.
- **FR-6** Rack / chassis layout overview folio.
- **FR-7** Purchasing-grouped BOM export.
- **FR-8** Siemens export spike (decision artifact, per platform, GUI-file-export-only).
- **FR-9** S7-300 front-end parser (symbol table → IR points).
- **FR-10** Siemens module map data (curated from the HW-config PDF; `module_db` pattern).
- **FR-11** S7-1200/1500 front-end parser (TIA exports per FR-8).
- **FR-12** Power one-line folio builder (config-driven, deterministic).
- **FR-13** Power-section config schema + graceful validation.
- **FR-14** LLM config-authoring skill (NL + examples → validated config; never QET code).

### NonFunctional Requirements
- **NFR-1** Python 3.10+, **standard library only**; no new dependencies.
- **NFR-2** **Never invent data** — uncertainty degrades to a clean placeholder
  (`TBD`→`__`, generic terminal, blank cell), never garbage.
- **NFR-3** Databases language-agnostic (EN/ES today; IT/DE/ZH as pure data on demand).
- **NFR-4** Public-repo hygiene — never commit `Fixtures/` or any
  `*.L5X`/`*.qet`/`*_bom.csv`/`*.pdf`/Siemens export (`*.asc`/`*.seq`/`*.sdf`/TIA).
- **NFR-5** One tested builder per folio; positional tests assert the FULL symbol
  extent vs the real frame (not a hotspot); floor numbers parsed from stderr (not a proxy).
- **NFR-6** The **WADDING_1 floor (10 drawing folios / 106 points / 75 matched / 0
  false positives)** never regresses.
- **NFR-7** Demand-driven — build a vendor/diagram only when a real project needs it;
  spikes precede speculative parsers.

### Additional Requirements (delivery / architecture)
- Work flows through BMAD dev cycles + adversarial review under the orchestrator, which
  **verifies every result from ground truth** (WADDING_1 gate + unittests + git); a
  subagent's `shipReady`/summary is a claim, not evidence.
- One focused commit per story; **feature branch → human merge gate**.
- New list/config folios follow the established "append a folio → inherits the title
  block" pattern (text + shape primitives only, empty `<elements>`/`<conductors>`),
  mirroring `build_supply_folios` / `build_grounding_folios`.

### UX Design Requirements
- None (CLI + JSON + LLM-skill tool; no UI).

### FR Coverage Map
| FR | Epic | Story |
|----|------|-------|
| FR-1 | E1 | 1.1 |
| FR-2 | E1 | 1.2 |
| FR-3 | E1 | 1.3 |
| FR-4 | E2 | 2.1 |
| FR-5 | E2 | 2.2 |
| FR-6 | E2 | 2.3 |
| FR-7 | E2 | 2.4 |
| FR-8 | E3 | 3.1 |
| FR-10 | E3 | 3.2 |
| FR-9 | E3 | 3.3 |
| FR-11 | E4 | 4.1, 4.2 |
| FR-13 | E5 | 5.1 |
| FR-12 | E5 | 5.2 |
| FR-14 | E5 | 5.3 |

## Epic List
- **E1 — Vendor-neutral IR refactor** (enabling; floor-preserving). Actionable now.
- **E2 — Vendor-independent quick-win folios** (immediate ROI on Rockwell). Actionable now; parallelizable.
- **E3 — Siemens spike + S7-300 enablement** (demand-driven, sample-gated).
- **E4 — Siemens TIA S7-1200/1500 enablement** (gated on E3 spike + a TIA fixture).
- **E5 — LLM config-authoring + power one-line folio** (independent; parallel with E1/E2).

---

## Epic 1: Vendor-neutral IR refactor

**Goal:** Promote the implicit, Rockwell-named domain model into an explicit
`PlcProject` IR that the renderer consumes, so future vendors plug in as front-end
parsers without touching the renderer. Pure enabling work — **zero change to Rockwell
output.** Unblocks all of E3/E4 and lets E2 folios be vendor-independent.

### Story 1.1: Define the explicit `PlcProject` IR
As a **maintainer**, I want a documented vendor-neutral `PlcProject` model, so that
parsers and the renderer share one stable contract independent of any PLC brand.

**Acceptance Criteria:**
**Given** the data the renderer needs today (controller, chassis/racks, modules with
catalog/slot/kind/points, I/O points with tag/address/direction/description, and the
comms/parent tree)
**When** the IR is defined
**Then** every one of those fields is represented in the IR as vendor-neutral data
**And** the comms/parent tree is first-class (enabling Story 2.1)
**And** the IR carries no L5X/Rockwell-specific naming or assumptions.

### Story 1.2: Renderer consumes only the IR
As a **maintainer**, I want `logix_to_qet.py` to depend solely on the IR, so that the
renderer is decoupled from Rockwell parsing internals.

**Acceptance Criteria:**
**Given** the IR from Story 1.1
**When** the renderer is refactored
**Then** an architecture/import test confirms the renderer references the IR and not
L5X parsing details
**And** all existing folio builders read only IR fields.

### Story 1.3: Rockwell L5X front-end → IR, byte-equivalent output
As a **controls engineer**, I want the existing Rockwell path to keep producing
identical drawings, so that the refactor is invisible in output.

**Acceptance Criteria:**
**Given** the WADDING_1 reference project
**When** the L5X path is rebuilt as a front-end producing the IR
**Then** the WADDING_1 floor holds **10 / 106 / 75 / 0** (parsed from stderr)
**And** folio count, section numbering, and designations are unchanged from Phase-1
**And** the full unittest suite is green (≥226) under Python 3.10+ stdlib only
**And** no `Fixtures/` artifact is committed.

---

## Epic 2: Vendor-independent quick-win folios

**Goal:** New folio builders that consume only the IR — they ship on Rockwell now and
ride onto Siemens for free once a parser exists. Each is an independent single-commit
story; all preserve the floor. (Best sequenced after Story 1.1 so they read the IR,
but each is otherwise standalone and parallelizable.)

### Story 2.1: Network / communications topology folio  *(headliner)*
As a **controls engineer**, I want a network topology folio, so that the comms
architecture (controller → comms modules → remote adapters → drops → HMI) is drawn
automatically instead of by hand.

**Acceptance Criteria:**
**Given** WADDING_1 (whose tree is `Local → RIO_LOCAL → RIO_RCP → REM_*` + HMI)
**When** the topology folio is generated
**Then** the folio shows the real tree from the IR, with nodes labelled from parsed
data only (no invented names)
**And** a positional test asserts the FULL drawn extent lies inside the page frame
**And** the folio has empty `<elements>`/`<conductors>`, carries the ISO 7200 title
block, and leaks no raw `%{token}`
**And** the WADDING_1 floor holds 10 / 106 / 75 / 0.

### Story 2.2: Drawing index / table-of-contents folio
As a **controls engineer**, I want a drawing index folio, so that the set reads as a
finished document without hand-listing every sheet.

**Acceptance Criteria:**
**Given** a generated set
**When** the index folio is built
**Then** it lists every folio's section page + title in document order
**And** it updates automatically as folios are added/removed
**And** geometry stays inside the frame; floor holds.

### Story 2.3: Rack / chassis layout overview folio
As a **controls engineer**, I want a rack layout folio, so that the physical chassis
population is documented automatically.

**Acceptance Criteria:**
**Given** WADDING_1 (2 racks)
**When** the rack-layout folio is built
**Then** each rack renders its modules in slot order, labelled from the IR (no invented
names; unknown catalogs degrade gracefully)
**And** a positional test asserts the full extent vs the frame; floor holds.

### Story 2.4: Purchasing-grouped BOM export
As a **purchaser/engineer**, I want a vendor/catalog-grouped BOM, so that ordering
parts doesn't require re-sorting the flat BOM by hand.

**Acceptance Criteria:**
**Given** the existing `_bom.csv`
**When** the grouped export is generated
**Then** a grouped CSV (and/or folio) is emitted alongside it, grouped by vendor/catalog
**And** the grouped counts reconcile exactly with the flat BOM (no rows invented or lost)
**And** no invented columns; floor holds.

---

## Epic 3: Siemens spike + S7-300 enablement  *(demand-driven, sample-gated)*

**Goal:** Bring Siemens into the tool via the IR, beginning with a no-code spike and
then S7-300, built against a real fixture. **Do not start a parser story before its
fixture lands in `Fixtures/`.** Depends on Epic 1 (the IR).

### Story 3.1: Siemens export spike  *(decision artifact — gated on samples)*
As a **maintainer**, I want a per-platform finding on the minimal GUI file exports, so
that we know the parse effort and a complete-enough source before writing a parser.

**Acceptance Criteria:**
**Given** real sample exports placed in `Fixtures/` (S7-300, and S7-1200/1500 when
available), and that **TIA Openness is out of scope**
**When** the spike is run
**Then** a written finding per platform records which GUI-exported files yield
*modules+slots+catalogs* AND *tag↔address↔comment*, the gaps, and a go/no-go + effort
estimate
**And** the output is a documented decision, not shippable code
**And** the working hypotheses are confirmed or corrected (S7-300 = symbol table +
HW-config PDF; 1200/1500 = tag-table XML + a GUI hardware export).

### Story 3.2: Siemens module map (S7-300 catalogs)  *(gated on samples)*
As a **maintainer**, I want a curated Siemens module dataset, so that S7-300 racks/slots
resolve from catalog data the same way Rockwell does.

**Acceptance Criteria:**
**Given** the HW-config PDF as a human-read reference (not auto-parsed)
**When** the Siemens catalogs in the fixture are curated into JSON
**Then** each catalog file follows the `module_db` schema discipline (pins `"TBD"`
until filled; unknown catalogs degrade gracefully)
**And** nothing is invented from the PDF — unread fields stay blank.

### Story 3.3: S7-300 front-end parser → IR  *(gated on 3.1, 3.2, Epic 1)*
As a **controls engineer**, I want S7-300 projects to generate a drawing set, so that
legacy Siemens jobs get the same automation as Rockwell.

**Acceptance Criteria:**
**Given** a real S7-300 fixture (symbol table + the curated module map)
**When** the parser runs
**Then** the IR carries each point's tag, Siemens address (`I0.0`, `Q4.1`, `IW…`), and
comment
**And** unmapped/uncertain data degrades to blanks/generics — never invented
**And** a Siemens-fixture floor (its own counts) is asserted from stderr
**And** the existing Rockwell WADDING_1 floor still holds; stdlib only; no fixture
committed.

---

## Epic 4: Siemens TIA S7-1200/1500 enablement  *(gated on E3 spike + TIA fixture)*

**Goal:** Extend Siemens support to TIA Portal platforms once the spike confirms a
viable GUI-export path and a real 1200/1500 fixture exists. **Out of MVP until those
gates clear** (PRD §6.2).

### Story 4.1: Siemens module map (1200/1500 catalogs)  *(gated)*
As a **maintainer**, I want curated TIA module data, so that 1200/1500 hardware resolves
via catalog data.

**Acceptance Criteria:**
**Given** the hardware export confirmed by Story 3.1
**When** the TIA catalogs are curated
**Then** each follows the `module_db` schema (pins `"TBD"`; graceful unknowns; nothing
invented).

### Story 4.2: S7-1200/1500 front-end parser → IR  *(gated on 3.1 go/no-go + TIA fixture)*
As a **controls engineer**, I want TIA projects to generate a drawing set, so that
current Siemens jobs get the automation too.

**Acceptance Criteria:**
**Given** a real 1200/1500 fixture (tag-table XML + the GUI hardware export, **no
Openness**)
**When** the parser runs
**Then** the IR carries modules + tag↔address↔comment from GUI exports only
**And** never-invent degradation holds; a fixture floor is asserted from stderr
**And** the Rockwell floor still holds; stdlib only; no fixture committed.

---

## Epic 5: LLM config-authoring + power one-line folio  *(independent; parallel with E1/E2)*

**Goal:** Render design data the PLC program can't supply (power one-line / panel) from
a JSON config authored with LLM help. The deterministic, tested generator renders; the
LLM authors **config, never QET code**. The grounding folio is the proven template.

### Story 5.1: Power-section config schema + validation
As a **maintainer**, I want a documented, gracefully-validated power-section config, so
that the renderer has a safe contract to draw from.

**Acceptance Criteria:**
**Given** a power-section config (in `project_template.json` or a sibling — decide and
document)
**When** it is loaded
**Then** missing/partial/malformed config degrades to safe defaults or omission, never
garbage
**And** any defaults are documented reference values, not invented site data
**And** the schema is documented for the authoring skill (Story 5.3).

### Story 5.2: Power one-line folio builder  *(depends 5.1)*
As a **controls engineer**, I want a power one-line folio rendered from config, so that
the panel power section is produced without hand-drawing.

**Acceptance Criteria:**
**Given** a sample power config
**When** the folio is built (mirroring `build_grounding_folios`: text + shape
primitives, own section page, inherits the title block)
**Then** geometry stays inside the frame (positional test on full extent), with no
`%{token}` leak and empty `<elements>`/`<conductors>`
**And** absent config → the folio is simply not emitted
**And** the WADDING_1 floor holds; stdlib only.

### Story 5.3: Config-authoring skill/agent  *(depends 5.1, 5.2)*
As a **controls engineer**, I want to describe a section in words and get a validated
config, so that I author power/panel sections by description, not by hand.

**Acceptance Criteria:**
**Given** a natural-language description + worked examples (grounding folio as the gold
template)
**When** the skill runs
**Then** it emits config JSON that passes Story 5.1 validation and renders via Story 5.2
**And** it does **not** emit QET XML or per-project parser code (builder code is written
once, in a dev cycle)
**And** the skill is reusable for future config-driven folio types.

---

## Dependencies & gating summary
- **Actionable now (Rockwell, no external input):** E1 (1.1→1.2→1.3), E2 (2.1–2.4 after
  1.1), E5 (5.1→5.2→5.3).
- **Sample-gated (do not start before the fixture lands in `Fixtures/`):** E3 (3.1 spike,
  then 3.2/3.3), E4 (4.1/4.2 — also gated on 3.1's go/no-go).
- **Hard dependency:** E3/E4 parsers require E1's IR. E2 folios are best after Story 1.1.
- **Per-story DoD (all):** WADDING_1 floor 10/106/75/0 from stderr; full suite green;
  positional tests assert full extent vs the real frame; stdlib only; never invent;
  public-repo hygiene; one focused commit; feature branch → human merge gate;
  orchestrator verifies from ground truth.

---
*Source: `docs/planning/prd.md`. Decisions: `docs/planning/.decision-log.md`.
Delivery via the BMAD orchestrator + `adversarial-dev-cycle`.*
