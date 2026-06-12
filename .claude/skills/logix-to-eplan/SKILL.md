---
name: logix-to-eplan
description: Converts Rockwell ControlLogix / CompactLogix programs (L5X exports) into EPLAN Electric P8 PLC import CSV files for schematic generation. This skill should be used when a user wants to generate an EPLAN I/O list, PLC card overview, or schematic import data from a Studio 5000 / RSLogix 5000 project (ACD, L5X, L5K, AML, or RDF files), or asks to map PLC tags/addresses to EPLAN device tags, racks, slots, and connection points.
---

# Logix to EPLAN PLC Import CSV

## Purpose

Generate an EPLAN P8 "PLC bulk data" import CSV directly from a ControlLogix
project file. The CSV has one row per physical I/O connection point with the
fixed header:

```
DeviceTag,Rack,Slot,ConnectionPoint,Address,DataType,SymbolicName,FunctionText
```

Digital and analog I/O are supported, both in the local chassis and in remote
drops (ControlNet/EtherNet adapters). HMI-mapped points (PanelView) are
excluded by default because they are not hardwired.

## Input format selection

Always work from an **.L5X** export. If the user only has another format:

| Format | Action |
|--------|--------|
| .ACD   | Ask the user to export L5X from Studio 5000: File > Save As > L5X |
| .L5K   | Ask for L5X instead (L5K is a custom text grammar; L5X carries identical data as XML) |
| .RDF / .AML | Not sufficient — these contain the hardware tree but **no tag database** |

## Running the converter

Use the bundled script (a copy also lives at `src/logix_to_eplan_csv.py` in
this repository). Python 3.10+, stdlib only:

```bash
python scripts/logix_to_eplan_csv.py PROJECT.L5X -o output_eplan.csv
```

Options:

- `--spares` — also emit unused points of every referenced card with empty
  SymbolicName and FunctionText "Spare" (use when EPLAN should draw full cards).
- `--include-hmi` — include PanelView/HMI-mapped alias points.
- `--logix-address` — keep raw Logix addresses (`Local:2:I.Data.3`) in the
  Address column instead of generated EPLAN-style `I0.3` / `QW256` addresses.
- `--keep-duplicates` — when several tags alias one physical point, emit all
  of them (default: first tag wins, the rest are folded into FunctionText).

The script prints a mapping summary to stderr (modules per rack/slot, mapped /
skipped / duplicate counts). Always relay this summary to the user and call
out any `skipped ... [unresolvable alias]` lines — those tags need manual review.

## How the mapping works

- **Modules** come from `Controller/Modules/Module`; the slot is the
  `Ports/Port[@Type='ICP']/@Address`. Catalog numbers are classified
  (DI/DO/AI/AO + point count) via a built-in 1756/1769/5069 catalog table with
  a regex heuristic fallback for unknown catalogs.
- **Racks**: local chassis = rack 1; each remote drop (distinct comm-adapter
  parent of I/O modules) = rack 2, 3, ... in name order.
- **Tags**: controller- and program-scoped alias tags are parsed from
  `AliasFor` (forms: `Local:2:I.Data.3`, `RIO_RCP:5:I.Ch0Data`,
  `Module:I.Data[2].9`); alias-of-alias chains are resolved recursively.
- **DeviceTag**: `=<Controller>+A<rack>-KF<slot>`.
- **ConnectionPoint**: bit/channel index + 1.
- **DataType**: BOOL for digital; analog is REAL when the tag radix is Float,
  else INT.
- **FunctionText**: tag Description when present, otherwise the tag name
  humanized through an English/Spanish abbreviation dictionary
  (`ABBREVIATIONS` in the script — extend it for plant-specific shorthand).

## Importing into EPLAN

Advise the user to import via *Project data > PLC > Navigator*, using the
bulk-data import (Data exchange > PLC data) with a field mapping matching the
CSV header, or paste-map the columns in EPLAN's import dialog. The DeviceTag
column is a full EPLAN DT (`=Plant+Location-Device`), so structure identifiers
must be enabled in the target project.

## Customization requests

- Different DeviceTag scheme (e.g. `-KF1` per byte-pair instead of per slot):
  edit `device_tag()` inside `build_rows()`.
- Additional module types: add entries to `CATALOG`.
- Different analog word base (default IW256/QW256): change
  `assign_racks_and_addresses()`.
