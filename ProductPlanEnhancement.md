# Product Plan — PLC → mini-EPLAN

## Vision

Turn a Rockwell ControlLogix **L5X** export into a QElectroTech project that is
as close to a *finished* I/O drawing set as possible, so the engineer doing the
final drafting does the **least manual work possible**. Every feature must
remove a manual finishing step — that is the single measure of whether it
belongs here.

This started as a one-file script and is growing into a small but real product.
Keep it honest: an MVP that reliably removes drudgery beats a feature-rich tool
that produces drawings nobody trusts.

## Where we are today

| Capability | Status |
|------------|--------|
| L5X → EPLAN PLC-import CSV | ✅ `src/logix_to_eplan_csv.py` |
| L5X → QElectroTech project, one folio per I/O card | ✅ `src/logix_to_qet.py` |
| Card box, terminal per point, PLC tag, EPLAN address, humanized function text | ✅ |
| Module enrichment (vendor, description, RTB, pins) | ✅ `src/module_db/` (pins `"TBD"`) |
| Semantic field-device symbols (limit switch, push button, valve…) wired to terminals | ✅ `src/symbol_db/` |
| Multilingual matching (EN/ES today; IT/DE/ZH = pure data later) | ✅ structure ready |

**WADDING_1 baseline:** 10 folios, 106 points, 75 symbols matched, 0 false
positives. Don't regress this.

## What the drawing engineer still does by hand

The biggest remaining manual chunks are *lettering devices*, *numbering wires*,
and *building the terminal strip* — none of which need new domain data; they
just need us to emit what we already know. That's why Tier 1 below is cheap and
high-impact.

---

## Backlog (ranked by gain-per-effort)

### Tier 1 — quick, pure data we already have

1. **Device designations (`-S1`, `-B1`, `-K1`, `-H1`, `-Y1`).**
   Auto-assign an IEC 81346 class letter per symbol and number sequentially.
   `symbol_db` already knows the device type — add one field (`"dt": "S"`) plus
   a per-project counter. Use the designation as the symbol's label; keep the
   PLC tag in the function/description text. *Single biggest "looks like EPLAN"
   win; otherwise the engineer letters every device manually.*
   Suggested classes: limit/proximity/pressure/level/flow sensor → `B`;
   push button/selector/e-stop/foot switch → `S`; relay/contactor coil → `K`;
   pilot light/horn → `H`; solenoid valve → `Y`; aux contact → parent's letter.

2. **Wire numbers.** We already emit `<conductor num="">` empty — populate it.
   Default scheme = the EPLAN-style I/O address of the point (configurable to
   sequential-per-folio). Verify QET renders it.

3. **Device-index / BOM folio (or CSV).** One summary sheet listing every I/O
   module (catalog + vendor + description from `module_db`) and every matched
   field device (designation, type, tag, address, folio). Pure data we have.

### Tier 2 — medium effort, high value

4. **Cajetín (title block).** Replace hardcoded header with a JSON-config-driven
   template (`src/project_template.json`): company, logo path, author, project
   title, date, folio `x/total`. Sensible defaults if absent.

5. **Power / supply.**
   (a) Draw each card's own power/common terminals — extend `module_db` with the
   group-common structure (e.g. `1756-OA16` = 2 groups of 8, separate L1
   commons; DC input cards share a common); pins stay `"TBD"` if unfilled.
   (b) A supply-rail folio (L+/L‑/24 V/PE) the cards reference.

6. **Terminal strip (bornero).** Insert a numbered terminal block inline on each
   field conductor between the card terminal and the device, or a dedicated
   strip folio per card. Classic EPLAN output, big manual task.

### Tier 3 — polish

- NO/NC correctness on symbols, spare-point rendering, column pagination when a
  card overflows, PE/ground potentials.
- Additional languages in the keyword/abbreviation databases (Italian, German,
  Chinese) — drop in as pure data when a project demands it.

**Recommended order:** 1 → 2 → 3 (about a day, no new domain data, immediately
visible) → cajetín → power → borneros.

---

## Non-negotiable guardrails

- **Public repo.** NEVER `git add` anything under `Fixtures/` (plant PLC data)
  or any `*.L5X` / `*.qet` / `*_eplan.csv` / personal files. `Fixtures/.gitignore`
  already blocks them — keep it that way. Commit only code, the JSON databases,
  and docs.
- **Never guess physical pin numbers.** `module_db` pins stay `"TBD"` (rendered
  `__`) until filled from Rockwell manuals. Wrong pins are worse than blanks.
- **Never force an uncertain symbol/designation.** Low-confidence matches keep
  the generic terminal. A wrong symbol in a drawing is worse than a plain one.
- **Python 3.10+, standard library only.** Keep the multilingual databases
  language-agnostic — never hardcode English assumptions in code.

## Validation (run every time before committing)

```bash
python src/logix_to_qet.py Fixtures/WADDING_1.L5X -o Fixtures/WADDING_1.qet
```

Then read the stderr summary (folios / points / symbols), and parse the output
`.qet` to assert:

- terminal ids are unique per diagram,
- every conductor `terminal1`/`terminal2` references an existing terminal id,
- every element `type` has a matching embedded `<definition>` in `<collection>`.

(A reusable validation snippet lives in the git history of commit `78e2d73`.)
Then open the project in QElectroTech to eyeball layout/overlap. Don't regress
the match count or introduce false matches.

## Working style

One focused commit per backlog item; the message should name the manual step it
removes. Stop and show the WADDING_1 result after each item before moving on.
`Co-Authored-By: Claude`.
