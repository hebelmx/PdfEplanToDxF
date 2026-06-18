# Handoff — Phase 2 (Siemens TIA path + output fixes + ALIM MERGED to `main` → next: S7-1500, then S7-300)

> Self-contained handoff so a **fresh agent in a new session** can resume with no prior context.
> Updated 2026-06-17 (supersedes the "feature-complete on a branch, pending eyeball + merge gate"
> version). Phase 1 (Rockwell) shipped & merged long ago; this is **Phase 2** (multi-vendor +
> config-driven diagrams), driven by `docs/planning/{brief,prd,epics}.md`. **Live source of truth for
> the open work = `docs/TIA-tracker.md`** (GitHub issue #2 CLOSED 2026-06-17; surviving low-pri nits → issue #3).

## ⚡ CURRENT STATUS (2026-06-17, branch `feat/e6-s71500-descriptions`) — read `docs/TIA-tracker.md` EPIC E6 for full detail
The **distributed-I/O build is DONE through c1 and Abel-eyeball-APPROVED.** On the branch (NOT merged):
- **E6(a)** `6135125` — `parse_aml` carries per-module address ranges.
- **E6(b)** `f4c1de5` + **(b-fix)** `b8d4afc` — `plc_ir.build_tia_distributed_project(aml)` → ordered
  `list[PlcProject]`, **9 stations / 776 ch / 549 mapped / 227 RESERVA**; Q100 byte-identical to the
  approved single-station floor (88/48/40); ownership by tag-table coverage; all stations carry owning
  CPU. Passed a 3-lens adversarial review (Q100 synthesis = byte-identical to the real IO_Channels).
- **E6(c1)** `d00e647`/`6db8e91` — `src/render_plant.py` + `tia_to_qet --distributed`: **191-folio**
  plant set, per-station numeric bands (front 0–50, bands 100–900, back 1000+), 0 collisions/0 leaks,
  ISO on all. `logix_to_qet.py` UNTOUCHED → Rockwell byte-identical; single-station Siemens unchanged
  (22/48/40). **Suite 454 green.** Abel: "looks good." Functional names = auto-derived + blanks.
- **NEXT = E6(c2)**: the off-module PROFINET I/O section grouped by function (drives/RFID/coordination),
  ~231 non-1:1 tags. Investigation done (no data gap; buckets identified) — see tracker; design gated.
- Merge of the whole branch to `main` is still Abel's call (hold until c2 + a final eyeball).

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
- **State: Epic 4 (Siemens TIA path), the E5 output-fixes cycle, AND ALIM (Siemens power one-line)
  are ALL MERGED to `main` @ `56c6de3` (== `origin/main`, pushed).** No open feature branch; working
  tree clean (only untracked `assets/` logos + `tools/`). Suite **397 green**; Rockwell floor
  11/106/75/0/78/35 byte-equivalent; Siemens render **23 folios** (with `--power-config`).
- **The remaining work is all sequenced and tracked in `docs/TIA-tracker.md`.** ALIM is DONE; the
  next real feature is the **S7-1500 path** — see PENDING below.

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
**1. S7-1500 path** — mostly present already: its I/O is in `IO_Channels.xml` + `PLCTagsS71500.xlsx`
   (which HAS rich English comments — a real description source, unlike the empty 1200 table), and its
   CPU (1512SP F-1) is in the `.aml`. Stand up a 1500 fixture/target and confirm the existing TIA path
   covers it; the descriptions from the 1500 tag table are the new lever (1200 had none).
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
Continue the PLC → mini-EPLAN product, Phase 2. main @ 56c6de3 (== origin) holds the MERGED Siemens TIA path (Epic 4) + E5 output fixes + ALIM
(Siemens power one-line). 397 tests green (1 skip); Rockwell WADDING_1 floor 11/106/75/0, 78 RESERVA,
35 folios, byte-equivalent to pre-TIA main; Siemens render 23 folios (with --power-config) / 35
PROFINET nodes, cover "CONTROLADOR (TIA)". No open feature branch.

READ FIRST: docs/HANDOFF-next-cycle.md (this file), docs/TIA-tracker.md (current), docs/planning/*,
memory tia-import-findings + siemens-import-findings + never-overwrite-working-qet + qet-preview-fidelity.
(GitHub issue #2 is CLOSED; low-pri nits live in issue #3.)

DO NEXT (priority order): (1) S7-1500 path (I/O in IO_Channels + PLCTagsS71500.xlsx rich English
comments; CPU 1512SP in .aml) -> same IR shape. (2) S7-300 path (fixture Fixtures/Siemens/S7300/,
schema in memory siemens-import-findings). (3) Optionally pick up the low-pri nits in issue #3. ALIM is DONE
(config-driven; extend via --power-config JSON, never invent).

HARD RULES: never -o Fixtures/Rockwell/WADDING_1.qet; never invent (read the real datum or blank);
stdlib only; never git add Fixtures/; SANITIZE all GitHub content; re-derive every number from ground
truth (stderr floors + 397 tests + parse the .qet + byte-equiv diff); read review-lens findings yourself;
one commit per item, feature branch → Abel's merge gate. Eyeball = regen _eyeball_*.qet + launch QET
on the local file (matplotlib previewer can't draw box shapes).
```
---
*Overwrite this file at the next milestone (after ALIM lands, or when the S7-1500/300 path ships).*
