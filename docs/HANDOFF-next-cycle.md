# Handoff — Phase 2 (EPIC E6 full-plant distributed I/O ✅ MERGED to `main` @ `f3a3fc5`; next: S7-300 / new vendor work)

> Self-contained handoff so a **fresh agent in a new session** can resume with no prior context.
> Updated 2026-06-17 (supersedes the "feature-complete on a branch, pending eyeball + merge gate"
> version). Phase 1 (Rockwell) shipped & merged long ago; this is **Phase 2** (multi-vendor +
> config-driven diagrams), driven by `docs/planning/{brief,prd,epics}.md`. **Live source of truth for
> the open work = `docs/TIA-tracker.md`** (GitHub issue #2 CLOSED 2026-06-17; surviving low-pri nits → issue #3).

## ⚡ CURRENT STATUS (2026-06-17) — EPIC E6 ✅ DONE & MERGED to `main` @ `f3a3fc5` (read `docs/TIA-tracker.md` EPIC E6 for full detail)
The full-plant distributed-I/O build shipped & Abel-eyeball-approved. **Suite 397→471 green; Rockwell
byte-identical; single-station Siemens unchanged (22 folios).** What landed:
- **E6(a)** `parse_aml` carries per-module I/O address ranges.
- **E6(b)+(b-fix)** `plc_ir.build_tia_distributed_project(aml)` → ordered `list[PlcProject]`,
  **9 stations / 768 ch / 547 mapped / 221 RESERVA** (range-join; ownership by tag-table coverage;
  F-DI value+status / F-DQ split; **1214C onboard clamped to the datasheet's physical 14 DI/10 DO/2 AI**
  — `_CPU_ONBOARD_PHYSICAL_IO` from the S7-1200 catalog in `docs/`). Q100 reproduces the approved
  single-station floor 88/48/40. Passed a 3-lens adversarial review.
- **E6(c1)** `src/render_plant.py` + `tia_to_qet --distributed` → per-station numeric bands
  (front 0–50, bands 100–900, back 1000+). 191 folios.
- **E6(c2)** off-module PROFINET I/O section (drives/RFID/coordination, by function→per element,
  summary tables + per-element placeholder boxes). Plant = **206 folios**; off-module 233 non-1:1 tags.
- **NEXT candidates** (none gating): S7-300 path (fixture in hand, schema in memory
  `siemens-import-findings`); issue-#3 low-pri nits; a curated Siemens símbología dictionary. The
  CLI `tia_to_qet --distributed` is the whole-plant entry; single-station `tia_to_qet …` still works.

## TL;DR — read this first
- Product: turn a PLC program export into a near-finished **QElectroTech** drawing set.
  - Rockwell: `src/logix_to_qet.py` (renderer + Rockwell folio builders) + `src/logix_to_eplan_csv.py`
    (L5X parser). CLI: `logix_to_qet.py PROJECT.L5X -o out.qet`.
  - Vendor-neutral IR: `src/plc_ir.py` — `PlcProject` (`build_rockwell_project` / `build_tia_project`
    return the same shape). The renderer `logix_to_qet.render_project(ir, out, *, …)` is shared.
  - **Siemens TIA: `src/tia_to_qet.py` (separate CLI) + `src/tia_front_end.py` + `src/tia_aml.py`.**
    CLI: `tia_to_qet.py …_IO_Channels.xml --aml …_V15.aml -o out.qet` (the `--aml` is auto-discovered
    from a sibling `*.aml` if omitted).
  - Tests: `src/test_logix_to_qet.py` + `src/test_tia_front_end.py` + `src/test_tia_to_qet.py` +
    `src/test_tia_aml.py` = **397 tests** (stdlib unittest, 1 pre-existing skip).
- **State of `main` @ `5b7ebdb`** (== `origin/main`): Epic 4 (Siemens TIA path) + E5 output fixes +
  ALIM (Siemens power one-line) + the issue-#2 close are ALL merged. Suite **397 green**; Rockwell
  floor 11/106/75/0/78/35 byte-equivalent; Siemens render **23 folios** (with `--power-config`).
- **⚠️ OPEN BRANCH `feat/e6-s71500-descriptions` @ `137b5b1`** (pushed, **NOT merged**): the S7-1500
  FOUNDATION (`bc0e5b0`, suite 397→**409**) + the distributed-I/O epic plan (3 doc commits). Holds:
  per-station tag-table coverage selection (→ real descriptions, 47/48 on Q100), symbol match +
  non-device suppression (VS_/'Vsupply'/'Permission to' → generic; 19 confident matches),
  `PlcProject.controller_cpu` seam. **Merge deferred** — see the reframe.
- **⚠️ THE BIG REFRAME (Abel, 2026-06-17) — read memory `siemens-distributed-io-reframe` FIRST.**
  The TIA path was drawing only ONE station (`Q100`, ~14% of the I/O). A modern S7-1500 has local I/O
  at the CPU + many DISTRIBUTED drops. The `.aml` actually holds the WHOLE plant: **9 stations / 75
  I/O modules / 636 channels** (Q100 1512SP-local @ .10 + Q200–Q800 IM155-6 ET200SP drops @ .20–.80 +
  the 1214C @ .95). **All data is in hand** (`.aml` modules+addresses + per-PLC tag tables); the full
  set is reconstructable — the next build draws it all. See PENDING + `docs/TIA-tracker.md` (EPIC E6).

## How we got to `56c6de3` (three merges landed 2026-06-17)
- **`56c6de3` — Merge `feat/e5-alim`** (3 commits): config-driven Siemens power one-line
  ('Alimentación') folio. `src/power_config.py` (stdlib json loader) + `build_power_folio`
  (`logix_to_qet.py`, `SECTION_ALIM=5`, visual-only, gated `source_vendor=="siemens" AND
  power_config` → Rockwell byte-equiv) + `tia_to_qet --power-config PATH` + a synthetic example
  `docs/examples/power_config.example.json`. Test values (Abel's assumptions, memory
  `alim-test-power-config`): 120 VAC / CB 2A / PS 10A / CB 10A, no xfmr/UPS. One-line:
  `120 VAC → [CB 2A] → [PS 10A] → [CB 10A] → loads`; absent optionals omitted. Siemens 22→23 folios.
  Label vertical placement iterated on the eyeball (**QET anchors text at the BASELINE / drawn
  upward — a SMALLER y sits HIGHER**; the in-box tags went to `top_y+12`/`+28` per Abel).
- **`586555e` — Merge `feat/e4-tia-1200`** (17 commits): the Siemens TIA drawing-set path.
  TIA-1 (`build_tia_project` IR front-end) → TIA-2 (`tia_to_qet.py` + shared `render_project`) →
  TIA-3 (`tia_aml.py` CAx parse) → CHAN (all channels drawn as box I/O, both vendors; **re-baselined
  the WADDING floor**) → NET (35-node PROFINET folio) → RACK+IDX (Siemens rack + drawing index) →
  TIA-FIX-1 (review fixes: real SubnetMask, DeviceItemType=CPU controller, no cross-station bleed) →
  4 desktop-eyeball fixes EYE-1..4 (NET node-box/rows, I/O lane widening, split-card side-by-side).
  Desktop-confirmed by Abel; **NET decision: tag ALL real CPUs** (whole-plant view, no code change).
- **`93519b6` — Merge `feat/e5-output-fixes`** (3 commits): post-merge output fixes.
  - **TIA-FIX-2** (`d52163e`): `build_portada_folio` gained keyword `source_format` (default `"L5X"`);
    `render_project` passes `"TIA"` for Siemens. Siemens cover now `CONTROLADOR (TIA)` (was the
    Rockwell `(L5X)` leak). Rockwell **byte-equivalent**. Cover change desktop-confirmed by Abel.
  - **RW-CLASSIFY** (`b401555`), byte-equivalent on WADDING: `classify_node` HMI drops the bare
    2-letter `"PV"` substring (false-positived on e.g. `1492-SPV-*` AND missed real `2711P-*`) → now
    literal `PANELVIEW` or AB family prefix `2711`/`2715`. Added comms-bridge families `AENT`→
    EtherNet/IP, `DNB`→DeviceNet, `DHRIO`→DH+/RIO.
  - **Finding #1 (24V rail) = WON'T-FIX** (Abel decision): keep the standard rail template
    `L1/N/L+/24V/0V/PE` as an intentional panel skeleton, even though WADDING cards reference only
    `L1/N/L+/0V` (the DC card names its rails `L+`/`0V`; `L+` IS the 24V positive). No code change;
    the existing supply-rail test correctly locks in the intended template.

## Floors (RE-DERIVED from ground truth on `main` @ `56c6de3` — do not trust a summary)
- **Rockwell WADDING_1: 11 drawing folios / 106 drawn / 75 matched / 0 false positives / 78 RESERVA /
  35 folios total.** **matched=75 and FP=0 are the never-move invariants**; drawn=106 (mapped only)
  also holds. Rockwell output is **byte-equivalent** to pre-TIA `main` (every Phase-2 commit verified
  via the UUID+filename-normalized diff — the only output that changed across all of Phase 2 is the
  Siemens cover `(TIA)` tag; the Rockwell set never moved).
- **Siemens IMV1 1200 station: 22 folios** (portada, símbología, Red PROFINET, índice, rack, 6 I/O
  cards incl. the merged split-card folio, 7 bornero, 3 BOM, changelog). IR floor 88 ch / 48 drawn /
  40 RESERVA / 35 PROFINET nodes. ISO 7200 title block on all, 0 token leaks, 0 unresolved conductors.
  (With `--power-config`, the Alimentación one-line is order 5 → **23 folios**.)
- **Suite: 397 tests green** (1 pre-existing skip).

## Key ground-truth facts (memory `tia-import-findings` is current)
- The `.aml` is the **FULL plant (91 station/module entries)**, NOT "1214C + 8×ET200SP". It contains
  **TWO CPUs**: `CPU 1512SP F-1 PN` (`6ES7 512-1SK01-0AB0`, the Q100 floor station, an **S7-1500-class
  F-CPU on ET200SP**) AND `CPU 1214C` (`6ES7 214-1BG40-0XB0`, host .95). The plant PROFINET subnet has
  **35 nodes** on 192.168.10.x (CPUs, IM 155-6 heads per station, SK TU3-PNT drives, EX260 valve
  terminals, BIS M-4008 RFID, printer). Both real CPUs are tagged `(CONTROLADOR)` (Abel's locked call).

## ⚠️ OPEN / PENDING — the prioritized backlog (most critical first)
**0. ALIM — ✅ DONE & MERGED 2026-06-17 @ `56c6de3`** (config-driven Siemens power one-line). Built
   against Abel's ASSUMED TEST values (test project, not real plant — memory `alim-test-power-config`).
   To extend later: it's config-driven, so real ratings / a transformer / a UPS are just JSON fields in
   the per-project `--power-config` (or `docs/examples/power_config.example.json`) — `build_power_folio`
   already renders optional transformer/ups rows when present; **never invent** values not supplied.
**1. ⭐ DISTRIBUTED-I/O BUILD (EPIC E6) — the next real work. Branch `feat/e6-s71500-descriptions`.**
   Draw ALL 9 stations (local + distributed), each its OWN section, sourced from the full `.aml` +
   per-PLC tag tables. **Design is LOCKED + data-validated** (memory `siemens-distributed-io-reframe`,
   tracker EPIC E6):
   - **Section block per station**; **heaviest PLC first** (1512SP's 8 stations: Q100-local then
     Q200–Q800 drops; THEN the 1214C's station). **Ownership is DATA-DRIVEN** (a station's `%`addresses
     resolve in exactly one PLC's tag table — Q100–Q800 → S71500, .95 → S71200; zero overlap, verified)
     — never guess; ask the user if a station ever splits across both tables. General + extendable.
   - **Build chunks:** (a) extend `tia_aml.parse_aml` to carry per-module address ranges
     (`StartAddress`/`Length`/`IoType` — already in the `.aml`). (b) Build the multi-station IR (all 9
     stations; ownership via tag-table coverage; **join by ADDRESS RANGE** — take the tag-table
     `%`addresses that fall inside each module's `.aml` range → real mapped channels, the rest of the
     capacity = RESERVA; this **sidesteps F-module PROFIsafe per-channel addressing**, which is NOT a
     clean byte.bit). data only, fully tested, no rendering. (c) Renderer: section-block-per-station
     folios — the section/numbering scheme + a **desktop eyeball gate** (Abel iterates visually).
   - The E6 foundation (tag-coverage selection + descriptions + symbol suppression + `controller_cpu`)
     is the reusable per-station join this builds on.
**2. S7-300 path** — spike was GO; fixture `Fixtures/Siemens/S7300/` in hand. Schema (`.asc` symbol
   table + `.cfg` HW config, join on byte address; masked `?` order-# digits are wildcards — keep) is
   in memory `siemens-import-findings`. Build a `build_s7300_project()` front-end → same IR shape.
**3. ✅ GitHub issue #2 CLOSED 2026-06-17** (all substantive items done or decided + merged). The 3
   surviving low-pri nits were split into **issue #3** (non-blocking polish) — same as the list below.
**4. Low-priority nits (not bugs, no floor risk):**
   - Símbología Siemens vocabulary — only `push_button` matches the Siemens tag vocabulary today
     (correct never-invent). Could add a CONFIDENT Siemens symbol dictionary (fcuv/VS_/etc.). Abel
     accepted the 1-type legend as-is for now.
   - Split-card bornero — kept per-half (two `-X1` strips for the F-DQ1500 [DO]+[DI] folio); Abel
     accepted as-is. Merge into one strip is a possible future polish.
   - `EPLAN A/KF` device-class letters; magic `256` analog-word base; TIA-DEFERRED nits (>32-ch
     column overflow assert+test, NET inter-row spine, synthetic 32-ch two-column positional test).
   - Epic-2 scope: rack/index shipped Siemens-only vs the plan's "vendor-independent" — Abel leaned
     "check if the errors exist on Rockwell" rather than force it; revisit only if it surfaces.

## Fixtures (GITIGNORED plant data — NEVER `git add` under `Fixtures/`)
- `Fixtures/Rockwell/WADDING_1.L5X` — Rockwell hard-gate fixture. ⚠️ **`Fixtures/Rockwell/WADDING_1.qet`
  is Abel's hand-edited WORKING file — NEVER `-o` over it** (memory `never-overwrite-working-qet`).
- `Fixtures/Siemens/TiaPortal/` — real machine IMV1_QRO001 (the 1200/Q100 target):
  `IMV1_QRO001_08AGO21_V15.aml` (CAx hardware, full plant) + `…_IO_Channels.xml` (THE point source) +
  `PLCTagsS71200.xlsx` (empty comments) + `PLCTagsS71500.xlsx` (rich English comments) + a 63MB PDF.
  Schema in memory `tia-import-findings`. Scratch eyeball files `_eyeball_tia.qet` / `_eyeball_wadding.qet`
  live here (gitignored — regenerate fresh per the gate commands).
- `Fixtures/Siemens/S7300/` — `*.asc` (symbol table) + `*.cfg` (HW config) for the S7-300 path.

## Gate commands (run from `src/`, re-derive every number; don't trust a summary)
```
# Full suite (397 tests, ~15-150s; VM can be slow):
cd src && python -m unittest discover -p "test_*.py"

# Rockwell WADDING_1 hard gate (floor must hold 11/106/75/0, 78 RESERVA, 35 folios):
cd src && python logix_to_qet.py ../Fixtures/Rockwell/WADDING_1.L5X -o ../Fixtures/Rockwell/_gen_check.qet
#   then delete ../Fixtures/Rockwell/_gen_check.qet + _gen_check_bom.csv

# Rockwell BYTE-EQUIVALENCE (when refactoring shared code) — diff must be EMPTY:
cd src && git show main:src/logix_to_qet.py > _old_lq.py
python _old_lq.py ../Fixtures/Rockwell/WADDING_1.L5X -o ../Fixtures/Rockwell/_b.qet
python logix_to_qet.py ../Fixtures/Rockwell/WADDING_1.L5X -o ../Fixtures/Rockwell/_a.qet
norm(){ sed -E 's/uuid="\{[0-9a-fA-F-]+\}"/uuid="{X}"/g; s/filename="[^"]*"/filename="{F}"/g' "$1"; }
diff <(norm ../Fixtures/Rockwell/_a.qet) <(norm ../Fixtures/Rockwell/_b.qet)   # empty = byte-equivalent
rm -f _old_lq.py ../Fixtures/Rockwell/_a.qet ../Fixtures/Rockwell/_b.qet ../Fixtures/Rockwell/_*_bom.csv

# Siemens render + structural check (23 folios with --power-config; render to scratch, then delete):
cd src && python tia_to_qet.py ../Fixtures/Siemens/TiaPortal/IMV1_QRO001_08AGO21_V15_IO_Channels.xml \
  --aml ../Fixtures/Siemens/TiaPortal/IMV1_QRO001_08AGO21_V15.aml -o ../Fixtures/Siemens/TiaPortal/_chk.qet
#   assert: 23 folios (with --power-config; 22 without); ISO title block on all; subnet from REAL SubnetMask; 2 CPUs flagged controller;
#   cover row "CONTROLADOR (TIA)" (no "(L5X)"); 0 token leaks; without --aml (isolated dir) NET+RACK omitted.
```

## Eyeball workflow (Abel reviews on his own Windows machine)
QET has **no CLI/headless PDF export** (confirmed; GUI-only single binary), and the dev previewer
`tools/qet_preview.py` (matplotlib — UNTRACKED, non-stdlib, do NOT `git add`) **skips the diagram
`<shape>` rectangles**, so it is lossy for the box-heavy NET / card-box / rack folios — **QET-desktop
is the true eyeball** (memory `qet-preview-fidelity`). Abel runs on THIS machine, so the real workflow
is: regenerate fresh eyeball `.qet` files to the gitignored `Fixtures/.../_eyeball_*.qet` paths, then
**launch QET on them directly**:
`& "C:\Program Files\QElectroTech\bin\qelectrotech.exe" "<abs path to _eyeball_*.qet>"`.
Fully restart QET to pick up `.titleblock` edits (it caches templates at startup). (Note: the older
handoff mentioned a `SendUserFile` tool for phone review — that tool is NOT available in this harness;
the renders are plant data and must never be pushed to the public repo regardless.)
Folio order: portada 0, símbología 1, Red PROFINET 2, índice 3, rack 4, I/O 101+, bornero 200+, BOM 300+, changelog 900.

## ⚠️ HARD RULES (carry forward — these bit us)
1. **NEVER `-o` over `Fixtures/Rockwell/WADDING_1.qet`** (Abel's working file).
2. **Don't trust a subagent/workflow summary or `shipReady`.** Re-derive every number from ground truth
   (generator stderr; the 397 tests; parse the `.qet`; byte-equiv diff). Read individual review-lens
   findings yourself — every review pass this product has run found REAL issues behind clean-looking work.
3. **Never invent.** Real addresses used directly; empty `<Tag>` = spare/RESERVA; missing description →
   ""; missing catalog/PROFINET/slot/mask → blank/None; masked `?` kept; pins `TBD`→`__`. Past reviews
   caught synthesized values (subnet /24, `24V` rail, `.10` controller) — prefer the real source datum
   or leave blank. stdlib only; multilingual DBs language-agnostic.
4. **Public-repo hygiene:** never `git add` under `Fixtures/` or any `*.L5X`/`*.qet`/`*.xlsx`/`*.xml`/
   `*.aml`/`*.pdf`/`*_bom.csv`/`*.asc`/`*.cfg`/personal file. **Issue #2 and any GitHub content must be
   SANITIZED** (no project name, IPs, station/device names). `assets/*` logos + `tools/` are untracked.
5. **One focused commit per item; feature branch → human merge gate** (merges to `main` are Abel's
   call — confirm before merging/pushing `main`). Footer:
   `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## Architecture seam (so the next agent gets it fast)
`plc_ir.PlcProject` is the vendor-neutral IR; `build_rockwell_project(l5x)` and
`build_tia_project(io_channels_xml, tags_xlsx=None, aml_path=None)` both return the same shape. The
renderer `logix_to_qet.render_project(project_ir, out, *, include_hmi, no_symbols, wire_scheme,
emit_vendor_folios)` is shared. Siemens-specific folios are gated by **IR content**, not just
`emit_vendor_folios`: the NET folio renders when `PlcProject.network_nodes` is non-empty (Rockwell IR
leaves it empty); rack/index render when `source_vendor=="siemens"`. `network_nodes` tuples are
`(ip, name, type, subnet_mask, is_controller)` (tolerant accessor handles legacy 3-tuples).
`tia_aml.py` provides `parse_aml` (catalog/slot/PROFINET per module, joined by physical name; split
`[DI]/[DO]` halves share the physical module) + `profinet_nodes` (the 35-node subnet list). The
Rockwell-specific topology/grounding/supply folios stay OFF for Siemens. `build_portada_folio` takes a
keyword `source_format` (`"L5X"` Rockwell / `"TIA"` Siemens) for the cover controller tag.

## Kickoff prompt — paste into the new session
```
Continue the PLC → mini-EPLAN product, Phase 2. main @ 5b7ebdb (== origin) holds the MERGED Siemens
TIA path (Epic 4) + E5 output fixes + ALIM + issue-#2 close. 397 tests green (1 skip); Rockwell
WADDING_1 floor 11/106/75/0, 78 RESERVA, 35 folios, byte-equivalent; Siemens render 23 folios (with
--power-config). OPEN BRANCH feat/e6-s71500-descriptions @ 137b5b1 (pushed, NOT merged): the S7-1500
FOUNDATION (suite 409) + the distributed-I/O epic plan.

⚠️ BIG REFRAME (Abel) — the TIA path drew only ONE station (~14%). The next work is the DISTRIBUTED-I/O
build: draw all 9 stations / 75 modules / 636 channels from the FULL .aml, each its own section.

READ FIRST: memory siemens-distributed-io-reframe (THE reframe + locked design), then
docs/HANDOFF-next-cycle.md (this file) + docs/TIA-tracker.md EPIC E6 (current), docs/planning/*,
memory tia-import-findings + alim-test-power-config + never-overwrite-working-qet + qet-preview-fidelity.
(GitHub issue #2 CLOSED; low-pri nits in issue #3.)

DO NEXT (on branch feat/e6-s71500-descriptions): the DISTRIBUTED-I/O build — (a) extend
tia_aml.parse_aml for per-module address ranges; (b) build the multi-station IR (all 9 stations;
ownership by tag-table coverage [Q100-Q800→S71500, .95→S71200, never guess/ask if ambiguous]; join by
ADDRESS RANGE — tag-table %addresses inside each module's .aml range = mapped, rest = RESERVA; this
avoids F-module PROFIsafe per-channel addressing; order heaviest-PLC-first); data-only + tested. THEN
(c) render section-block-per-station with a desktop eyeball gate. THEN S7-300; issue-#3 nits.

HARD RULES: never -o Fixtures/Rockwell/WADDING_1.qet; never invent (read the real datum or blank);
stdlib only; never git add Fixtures/; SANITIZE all GitHub content; re-derive every number from ground
truth (stderr floors + the full suite + parse the .qet + byte-equiv diff); don't trust a subagent's
"it's real" — re-verify (the S7-1500 symbol-match false positives were caught this way); one commit per
item, feature branch → Abel's merge gate. Eyeball = regen _eyeball_*.qet + launch QET via
"C:\Program Files\QElectroTech\bin\qelectrotech.exe" (matplotlib previewer can't draw box shapes).
```
---
*Overwrite this file at the next milestone (after ALIM lands, or when the S7-1500/300 path ships).*
