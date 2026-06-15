---
title: "Product Brief — PLC → mini-EPLAN, Phase 2 (Multi-vendor + LLM-aided diagrams)"
status: draft
created: 2026-06-14
updated: 2026-06-14
author: Abel Briones (Exxerpro Solutions)
facilitated_by: BMAD Product Brief (orchestrator-driven)
---

# Product Brief: PLC → mini-EPLAN — Phase 2

## Executive Summary

**PLC → mini-EPLAN** turns a PLC program export into a near-finished electrical
drawing set in QElectroTech, so a controls engineer drafts *from* a generated set
instead of hand-building it. Phase 1 (shipped) does this for Rockwell
ControlLogix: one `.L5X` export becomes a 32-folio set — cover, symbol legend,
supply rails, per-chassis grounding, I/O card drawings, terminal strips, BOM, and
revision history — all under an ISO 7200 title block, anchored by a hard
regression gate so the output stays trustworthy.

Phase 2 takes the same engine three directions that real upcoming work demands:
(1) **Siemens SIMATIC support** — S7-1200/1500 and legacy S7-300 — by formalizing a
vendor-neutral data model and adding per-vendor parsers behind the existing
renderer; (2) **LLM-aided, config-driven diagrams** for the parts a PLC program
can't contain (power one-line, panel sections), where an engineer describes the
section in words and an LLM authors a validated config that the deterministic
generator renders; and (3) **quick-win diagram types** that are pure renderer work
and pay off immediately on Rockwell while riding for free onto Siemens — starting
with a network/communications topology folio.

Why now: Exxerpro has Siemens projects on the near horizon, and the Phase-1
architecture already has the clean seam (parser → renderer, all domain data in
JSON) that makes this expansion cheap rather than a rewrite.

## Where We Are Today (Phase 1, shipped)

- **One `.L5X` → a 32-folio QElectroTech set** (`src/logix_to_qet.py`): Portada,
  Simbología, Alimentación, per-chassis Puesta a tierra, I/O card drawings (terminals,
  tags, EPLAN-style addresses, IEC 81346 designations, RESERVA spares, matched
  field-device symbols), borneros, BOM (+ `_bom.csv`), Historial — under an ISO 7200
  cajetín.
- **Trust by construction:** 226 tests; the **WADDING_1 floor gate** (10 drawing
  folios / 106 points / 75 matched / 0 false positives) must never regress.
- **Clean architecture seam:** `logix_to_eplan_csv.py` (parser + Rockwell domain
  model) → `logix_to_qet.py` (renderer); all domain knowledge in JSON
  (`module_db`, `symbol_db`, `project_template.json`). Python 3.10+, stdlib only.

## The Problem

Producing a complete PLC drawing set by hand is slow, mechanical, and error-prone.
A controls engineer re-enters into a CAD tool everything the PLC project already
knows — every module, slot, tag, address, description — then letters every device,
numbers every wire, builds every terminal strip, and assembles the front matter,
keeping a title block consistent across dozens of sheets. The data exists; the
*drawing* doesn't, and bridging that gap is hours of drudgery per machine.

Two compounding pains motivate Phase 2 specifically:
- **The work isn't all Rockwell.** Exxerpro serves clients standardized on Siemens
  (1200/1500 and legacy 300). Today those projects get none of the automation —
  they're drafted entirely by hand.
- **Some sheets aren't in the PLC program at all.** Power distribution, panel
  layout, and similar design data (breakers, PSUs, fusing) cannot be extracted from
  any PLC export, so they stay manual even on Rockwell jobs.

## The Solution (Phase 2)

Three coordinated moves on the existing engine:

1. **Multi-vendor via a vendor-neutral core.** Promote today's implicit,
   Rockwell-named domain model into an explicit intermediate representation
   (`PlcProject`). Add per-vendor *front-end parsers* (Siemens first) that translate
   a native export into that model; the renderer never changes. Each vendor parser
   is built **demand-driven against a real project fixture**, exactly as WADDING_1
   anchors Rockwell — never speculatively.

2. **LLM-aided, config-driven diagrams** for design data the PLC can't supply. The
   grounding folio is the proven template: a JSON config + a tested builder. An LLM
   "skill" takes the engineer's natural-language description (plus worked examples)
   and authors a **validated config**, which the deterministic, floor-gated
   generator renders. Firm division of labor: the LLM writes the **builder code
   once** (in a dev cycle); thereafter it authors only **config data per project**.
   The LLM never emits QET XML or one-off parser code.

3. **Quick-win renderer folios** that are vendor-independent — they work on Rockwell
   today and ride onto Siemens for free through the shared model. Lead with the
   **network/communications topology** folio (the comms tree is already parsed),
   followed by a drawing index, a rack/chassis layout overview, and a
   purchasing-grouped BOM.

## What Makes This Different

- **Trust is the product.** The non-negotiable rule — *never invent data; uncertainty
  degrades to a clean placeholder, never garbage* — is what lets an engineer draft
  *from* the output instead of re-checking every line. Most "automation" tools lose
  trust the moment they guess; this one is built to refuse.
- **A seam that turns expansion into addition, not rewrite.** New vendor = new parser
  behind the model. New diagram = new builder in the list. The core renderer is
  untouched either way.
- **Right division of labor with the LLM.** The model does the fuzzy
  natural-language-to-structured-config translation; the deterministic, tested engine
  does the rendering. This keeps every project on the same floor-gated rails.
- **Honest moat: execution discipline, not magic.** The advantage is the regression
  gate, the JSON data model, and the demand-driven cadence — not a proprietary
  algorithm.

## Who This Serves

- **Primary — the Exxerpro controls/electrical engineer (Abel).** Native in AutoCAD
  Electrical, uses EPLAN for one client, adopting QElectroTech. Does machine
  retrofits and new builds; today hand-drafts every PLC I/O set. Success = opening a
  generated set and drafting the last mile instead of building from scratch — for
  Siemens jobs as well as Rockwell.
- **Secondary — collaborating engineers / future contributors** who extend the JSON
  databases (new module catalog, new field device, new language) or add a new
  folio type, without needing to touch the core.
- The immediate Siemens demand spans **both** legacy **S7-300** (STEP 7 Classic) and
  **TIA Portal S7-1200/1500**. Real sample exports are being gathered into `Fixtures/`
  (in progress; bounded by VM/TIA export speed). Specific project size/first-target
  still being assembled by Abel.

## Success Criteria

- **First Siemens set drafted from generated output** — a real S7 project produces a
  QElectroTech set an engineer finishes rather than redraws.
- **Manual finishing steps removed** stays the single test for every feature (each
  must remove one).
- **Trust signal:** engineers draft *from* the set, not *around* it; zero invented
  data reaches a drawing.
- **The floor never regresses** — WADDING_1 (10/106/75/0) holds through every change.
- **Additivity:** new vendors and new diagram types land without modifying the core
  renderer seam.
- **Sample-gated, soft horizon** — paced to when real Siemens exports land in
  `Fixtures/` (gathering in progress; bounded by VM/TIA export speed, not by the plan).
  Build each parser when its fixture exists.

## Scope

**In (Phase 2):**
- Vendor-neutral `PlcProject` model (the enabling refactor).
- Siemens export **spike** (1200/1500/300) on real samples (placed in `Fixtures/`) to
  confirm a **GUI-file-export-only** path that is complete enough — *before* committing
  to a parser. **TIA Openness is ruled out** (not viable in Exxerpro's environment), so
  every Siemens input is a file export. Working hypotheses: **S7-300** = symbol-table /
  I-O-list export (tag↔address↔comment) + a hardware-config **PDF** read by a human to
  hand-curate the Siemens module map (the existing `module_db` pattern); **1200/1500** =
  PLC tag-table XML + a GUI hardware export (e.g. CAx/AML) — the spike confirms what's
  actually complete.
- Siemens parser(s), built demand-driven against a real project fixture.
- Network/comms topology folio (lead quick-win); drawing index; rack layout overview;
  purchasing-grouped BOM.
- Config-driven power one-line folio + an LLM config-authoring skill (grounding folio
  as the template), with the power section as its first use case.

**Out (for now):**
- Speculative vendors/diagrams no real project needs (CODESYS/Beckhoff, Mitsubishi,
  Omron, etc. — pulled in only on demand).
- LLM generating QET XML or per-project parser code.
- Extracting power/panel design data from the PLC program (it isn't there — it comes
  from config).
- Additional UI; this stays a CLI + JSON + LLM-skill tool.
- Additional languages (IT/DE/ZH) until a project demands them (pure data when it does).

## Key Risks & Mitigations

- **Siemens export completeness (the real risk, not the parser).** With **TIA Openness
  ruled out**, everything must come from GUI file exports — the question is whether
  those are complete enough. S7-300's tag data is easy (symbol-table / I-O list) but its
  hardware arrives as a **PDF** (human-read into a curated module map, not auto-parsed);
  1200/1500's tag XML is clean but the hardware-export completeness is unproven.
  **Mitigation:** the spike on real samples answers "2-day job or 2-week slog" before
  any commitment, per platform.
- **Sample/environment latency.** Exports are slow to produce (TIA on VMs is sluggish;
  VM optimization is a separate effort). **Mitigation:** the plan is sample-gated, not
  date-gated; vendor-independent quick-wins (topology, index, rack, BOM) proceed on
  Rockwell meanwhile so Phase 2 delivers value before the first Siemens sample lands.
- **Building blind.** A parser written without a real export targets the wrong shape.
  **Mitigation:** demand-driven — build against a project fixture, like WADDING_1.
- **LLM drift on generated artifacts.** **Mitigation:** the LLM authors config only;
  one tested builder per folio; the floor gate catches regressions.

## Vision

In 2–3 years, PLC → mini-EPLAN is the default first step for any controls drawing
set at Exxerpro regardless of PLC brand: point it at a Rockwell, Siemens, or
(on-demand) other export and get a near-finished, trustworthy QElectroTech set;
describe the non-program sections in plain language and have them rendered to the
same standard. The engineer's job shifts from drafting to reviewing — the drudgery
gone, the trust intact.

---
*Feeds: a PRD (`docs/planning/prd.md`) then epics/stories (`docs/planning/epics.md`).
Companion context: `README.md`, `docs/logix-to-qet-guide.md`, `ProductPlanEnhancement.md`.*
