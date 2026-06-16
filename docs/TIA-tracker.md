# TIA S7-1200 parser tracker  (Epic 4 — Siemens TIA front-end)

> Durable source of truth for the TIA-1200 batch. Survives a context clear.
> Driven by the bmad-orchestrator loop. Mirror of the in-session Task list.
> Started 2026-06-16. Branch: `feat/e4-tia-1200`.

## Decisions (Abel, 2026-06-16)
- **1500 CAx is being re-exported ("ca02") → build the S7-1200 path FIRST.** (Resolves the
  handoff open question at HANDOFF line 44.)
- **Floor target = the REAL machine:** `Fixtures/Siemens/TiaPortal/IMV1_QRO001_*` 1200 station,
  NOT the synthetic `S71200/Project1`.
- **CLI = a SEPARATE command:** new `src/tia_to_qet.py` for the Siemens path, sharing the
  renderer + the `plc_ir.PlcProject` seam. (`logix_to_qet.py` stays Rockwell/`.L5X`.)

## Ground-truth fixture floor (IMV1 1200 station — verified by orchestrator from IO_Channels.xml)
Station "Q100-Cooling1/UV", Rack_0, **6 modules / 88 channels / 48 tagged / 40 spare**:
| Module    | ch | tagged | spare | addresses        | kind |
|-----------|----|--------|-------|------------------|------|
| F-DI150   | 16 | 16     | 0     | %I150.x / %I151.x | DI (F) |
| F-DI156   | 16 | 4      | 12    | %I156.x / %I157.x | DI (F) |
| DI10_11   | 16 | 12     | 4     | %I10.x / %I11.x   | DI |
| DI12_13   | 16 | 5      | 11    | %I12.x / %I13.x   | DI |
| F-DQ1500  | 8  | 3      | 5     | %Q1500.x + %I1500.x | DQ/DI (F) |
| DQ10_11   | 16 | 8      | 8     | %Q10.x / %Q11.x   | DQ |

## Non-negotiable guardrails (every item)
- **Rockwell WADDING_1 floor must NOT regress: 10 folios / 106 / 75 / 0**, 62 RESERVA, 33 folios.
- Real absolute Siemens addresses used directly — **never synthesize/invent** an address.
- Empty `<Tag>` = spare (RESERVA), mirroring the Rockwell spare semantics.
- stdlib only (zipfile+xml.etree for xlsx); never `git add` under `Fixtures/`.
- `source_vendor="siemens"`; same `PlcProject` shape → renderer needs no vendor branch.

## Backlog (recommended order)
- [ ] **TIA-1** — `build_tia_project()` IR front-end: parse IMV1 `IO_Channels.xml` → modules +
      points + spares; join `PLCTagsS71200.xlsx` for descriptions; classify kind from %I/%Q/%IW;
      set module bases / point index from the REAL absolute address so the rendered address ==
      the real Siemens address. Returns `PlcProject(source_vendor="siemens")`. Tests assert the
      IR floor 6/88/48/40. No CLI/render yet. Rockwell path untouched.
- [ ] **TIA-2** — `src/tia_to_qet.py` entry (separate command) + render the core I/O folios +
      emit a stderr fixture-floor summary; floor test parses it. Structurally verify the `.qet`
      (unique terminal ids; conductors resolve; ISO title block; no `%{token}` leak). Gracefully
      omit any folio that is genuinely Rockwell-specific for now (never invent). Eyeball gate.
- [ ] **TIA-3** — `.aml` hardware map (Story 4.1): module catalog/order# (`TypeIdentifier`
      `OrderNumber:6ES7…`), `TypeName`, kind/points, PROFINET `NetworkAddress` (192.168.10.x).
      `module_db` schema; pins `"TBD"`; masked `?` digits kept; nothing invented.
- [ ] **Adversarial review** at the phase boundary (3-lens + general) vs `docs/planning/*` before
      proposing the merge gate.

## Status log
- 2026-06-16: tracker created; decisions gated & locked; TIA-1 delegated.
