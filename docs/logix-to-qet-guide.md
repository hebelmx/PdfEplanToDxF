# `logix_to_qet` — guide

In-depth guide to `src/logix_to_qet.py`: the problem it solves, how to run it,
the anatomy of the drawing set it produces, the data model behind it, how to
extend it, and the guardrails that keep its output trustworthy.

For a one-screen overview see §3 of the [root README](../README.md). For the
schema of the JSON databases see [`src/module_db/README.md`](../src/module_db/README.md)
and [`src/symbol_db/README.md`](../src/symbol_db/README.md).

---

## 1. The problem

A Rockwell ControlLogix project knows an enormous amount about a machine's I/O —
every module, its catalog and slot, every tag, its address, the engineer's own
description — but none of that is in a *drawing*. The drafter re-enters it by
hand: drawing each card, lettering each device, numbering each wire, building
each terminal strip, assembling the front matter, and keeping a title block
consistent across dozens of sheets. It is slow, mechanical, and error-prone.

`logix_to_qet` turns an **L5X export** into a QElectroTech project that is as
close to a *finished* drawing set as the source data allows, so the drafter does
the **least manual work possible**. The guiding rule of the whole tool:

> Every feature must remove a manual finishing step. If it doesn't, it doesn't
> belong here.

And its mirror-image rule:

> Never invent data. An uncertain symbol, a guessed pin, or a made-up potential
> is worse than a blank — it produces a drawing nobody can trust. Uncertainty
> degrades to a clean placeholder (`__`, a generic terminal), never to garbage.

## 2. Why L5X

L5X is the only Rockwell export that is **both standard XML and complete** — it
carries the full module tree (catalogs, slots, chassis, comms adapters) *and* the
entire tag database. The alternatives don't: `.ACD` is closed binary, `.L5K` is a
bespoke text grammar, `.RDF`/`.AML` carry the hardware tree but no tags. Export
one from Studio 5000 with *File ▸ Save As ▸ L5X*.

## 3. Running it

```bash
python src/logix_to_qet.py PROJECT.L5X -o project.qet
```

| Flag | Effect |
|------|--------|
| `-o, --output PATH` | output `.qet` path (default `<l5x>.qet`); a `<output>_bom.csv` sidecar is written next to it |
| `--include-hmi` | include PanelView/HMI-mapped points (off by default — they are not hardwired field I/O) |
| `--no-symbols` | skip field-device symbol matching; draw plain terminals only |
| `--wire-scheme {address,sequential}` | conductor wire numbers: `address` = the EPLAN address verbatim (default, already globally unique); `sequential` = `W<page>.<n>` per folio |

Open the `.qet` in **QElectroTech 0.100+** (the project format was
reverse-engineered from the official 0.100 example projects). From QET the whole
set prints to PDF or exports to DXF natively.

A summary prints to **stderr** — read it, it is the tool's self-report:

```
folios     : 10 (one per I/O card with mapped tags)
points     : 106 drawn, 80 skipped
symbols    : 75 matched (...), 31 generic terminal
spare      : 62 reserve terminal(s) (RESERVA) over 10 card(s)
bom        : 178 rows (10 module, 75 device, 31 generic, 62 spare) over 5 summary folio(s)
supply     : 1 'Alimentación' rail folio(s) (order 98)
grounding  : 2 'Puesta a tierra' chassis folio(s) (orders 99..100)
bornero    : 11 terminal-strip (-X1) folio(s) ...
title block: ISO 7200 (exxerpro) — Exxerpro Solutions, 32 folio(s)
```

## 4. Anatomy of the drawing set

The set is emitted in natural drawing order, each sheet carrying the same ISO
7200 title block with a section page number in the cajetín. On the bundled
`WADDING_1` reference project this is **32 folios**.

| Section | Page(s) | Builder | Contents |
|---------|---------|---------|----------|
| **Portada** (cover) | `000` | `build_portada_folio` | project / machine / controller metadata |
| **Simbología** (legend) | `001` | `build_symbology_folio` | the real glyph + localized name of every symbol *actually used*, 2 columns/page |
| **Alimentación** (supply) | `100 − n` | `build_supply_folios` | each supply potential (L1/N, L+/0V, 24V, PE…) as a labelled rail the cards reference |
| **Puesta a tierra** (grounding) | `… 100` | `build_grounding_folios` | one folio **per chassis**: a labelled chassis box with FE + PE studs, gauge-labelled leads to a central ground bus, then to the grounding-electrode system (modeled on AB 1756-IN621) |
| **Card drawings** | `101 … 110` | `build_folio` | the heart of the set — see below |
| **Borneros** (terminal strips) | `200 …` | `build_bornero_folios` | one+ folio per card listing its strip `-X1`: every terminal `-X1:<ch>` (mapped tag *or* `RESERVA`) in channel order, paginated for wide cards |
| **BOM / índice** | `300 …` | `build_summary_folios` | every module, matched device, generic terminal and spare; also the full `_bom.csv` |
| **Historial** (revisions) | `900` | `build_changelog_folios` | ISO 9001 revision history (last sheet) |

Multi-sheet sections (drawings, borneros, BOM) carry `◄ pág. X` / `pág. Y ►`
continuation references on the bottom lane.

### The card drawing folio (`build_folio`)

For each I/O card with mapped tags, one folio carries:

- a **classical card box** sized to the module's point count;
- a **terminal element per point**, wired by a conductor, labelled with the PLC
  **tag**, the **EPLAN-style address** (`I0.3`, `IW256`), the physical RTB **pin**
  (`__` until filled), and a **humanized function text**;
- an **IEC 81346 device designation** per matched device (`-B101.1`, `-K101.3`,
  `-S101.2` …) where the page number is the prefix;
- **RESERVA spare terminals** for every unused channel, so the strip is physically
  complete (counted separately — they never inflate the match count);
- the card's **power terminals** (from the module's `power` block) and a boxed
  `ALIMENTACIÓN` table in the top-right corner referencing the supply rails;
- a **matched field-device symbol** drawn at the end of each digital row and wired
  to the terminal — or a generic terminal when the match is not confident.

## 5. How it works (data flow)

```
 PROJECT.L5X
     │
     ▼  logix_to_eplan_csv.py  (the "l2e" parser + domain model)
 load_l5x ──► controller, modules{}, controller-tags, program-tags
 assign_racks_and_addresses ──► io_mods (rack/slot/catalog/kind/points)
 collect_points ──► IoPoint[]  (tag, address, direction, index, description)
     │
     ▼  logix_to_qet.py  (the renderer)
 per-module grouping, de-dup ─┐
 symbol matching (symbol_db) ─┤
 module enrichment (module_db)┤──► build_*_folio(...) for each section
 designations / wire numbers ─┘        (append <diagram> to the project)
     │
 reorder by section page ──► attach ISO 7200 title blocks ──► embed
 element/title-block definitions ──► serialize
     │
     ▼
 project.qet   +   project_bom.csv
```

Two modules, one clean seam:

- **`logix_to_eplan_csv.py` (l2e)** is the **parser + domain model** — everything
  Rockwell/L5X-specific lives here (`Module`/`IoPoint`, rack assignment, address
  synthesis, catalog classification, the EN/ES abbreviation humanizer). It also
  stands alone as the L5X → EPLAN CSV tool (§2 of the README).
- **`logix_to_qet.py`** is the **renderer** — it consumes that model and knows
  about QET geometry, folios, and the title block, but almost nothing about
  Rockwell. Each section is an independent *folio builder* that consumes derived
  data and appends one self-contained `<diagram>`. Adding a section = adding a
  builder to the list `main()` walks.

This seam is the extension point for everything: a new PLC vendor is a new
front-end feeding the same model; a new diagram type is a new folio builder.

## 6. The data model (extend without touching code)

Everything domain-specific is JSON. None of it requires editing Python.

### Module database — `src/module_db/`
One file per catalog number (vendor, description, RTB, per-point names + physical
pins, optional power-group structure). Pins ship `"TBD"` → render `__`. Unknown
catalogs degrade gracefully (no vendor info, `__` pins). **Full schema:**
[`src/module_db/README.md`](../src/module_db/README.md).

### Symbol database — `src/symbol_db/`
One file per field-device type plus its QET `.elmt` glyph. Carries the IEC 81346
class letter (`dt`), the QET-collection source, `direction` (I/O/any), tag
`suffixes`, and fuzzy-matched `keywords`. Matching is the inverse of the
humanizer: a multi-word phrase the engineer actually wrote beats a 2-letter
suffix code. Includes NO/NC variants for the field switches (level, flow,
pressure, foot, thermostat, limit). **Full schema + how to add a device:**
[`src/symbol_db/README.md`](../src/symbol_db/README.md).

### Project template — `src/project_template.json`
Drives the cajetín and a couple of folios. All keys optional (blank → blank cell,
never garbage):

| Key | Used for |
|-----|----------|
| `company`, `company_logo`, `client`, `client_logo` | title block identity |
| `project`, `machine` | title block; fall back to the controller name |
| `drawn_by`, `revised_by`, `approved_by`, `date`, `drawing_number`, `revision` | title block fields |
| `revisions[]` | the **Historial** changelog (`rev`/`date`/`description`/`drawn`/`approved`); absent → one synthesized "Primera emisión" row |
| `grounding` | grounding-folio wire gauges: `fe_gauge` / `pe_gauge` / `electrode_gauge`; absent → the 1756-IN621 reference defaults |

## 7. Title block (cajetín)

The cajetín is a **native QET ISO 7200 title block** (`assets/exxerpro.titleblock`),
cloned from QET's bundled `ISO7200_A4_V1` with the company SVG logo embedded. The
generator embeds the template verbatim as text and sets, per folio, a
`<property>` for **every** custom token — otherwise QET leaks a raw `%{token}`.
The displayed section page is a custom `%{page}` filled per folio (QET numbers by
document position, so the section scheme is generator-driven, not QET-driven).

> **Gotcha:** QET caches title-block templates at startup — fully **restart** QET
> to see edits to a `.titleblock`.

## 8. Extending it

- **New module catalog** → drop a JSON in `module_db/` (see its README). Fill pins
  from the vendor wiring diagram; never guess.
- **New field device** → add a `.elmt` glyph + JSON in `symbol_db/`; check the
  `symbols :` stderr line on a real project.
- **New language (IT/DE/ZH)** → pure data: add keyword/abbreviation entries; the
  code makes no English assumptions.
- **New section/diagram type** → add a folio builder mirroring an existing one
  (`build_supply_folios` / `build_grounding_folios` are the simplest templates —
  text + shape primitives only, empty `<elements>`/`<conductors>`, inherits the
  title block) and call it from `main()` with its own section page.

## 9. Guardrails (non-negotiable)

1. **Never invent.** Unmatched → generic terminal; uncertain → graceful fallback;
   physical pins stay `"TBD"` → `__`; gauges are documented defaults, not site
   data; chassis/device labels are derived, never made up.
2. **Python 3.10+, standard library only.** Keep the databases language-agnostic.
3. **Public-repo hygiene.** Commit only code, the JSON databases, and docs. Never
   commit anything under `Fixtures/` or any `*.L5X` / `*.qet` / `*_eplan.csv` /
   `*_bom.csv` / `*.pdf` / personal file (plant data).
4. **Don't regress the reference.** The `WADDING_1` gate — **10 drawing folios /
   106 points / 75 matched / 0 false positives** — is the anchor. Validate to a
   **scratch** path, never over a working `.qet`:
   ```bash
   python src/logix_to_qet.py Fixtures/WADDING_1.L5X -o Fixtures/_gen_check.qet
   ```
   then parse the output (unique terminal ids, every conductor resolves, every
   element `type` has an embedded `<definition>`, no raw `%{token}`), run
   `python -m unittest test_logix_to_qet` from `src/`, and delete the scratch
   files. Eyeball in QET for layout.

---

*Companion docs:* [`README.md`](../README.md) ·
[`ProductPlanEnhancement.md`](../ProductPlanEnhancement.md) (vision / backlog) ·
[`docs/TIER3-tracker.md`](TIER3-tracker.md) (delivery tracker).
