# EPLAN Engineering Tools

A small toolbox for moving data between Rockwell ControlLogix, EPLAN Electric P8, and CAD:

| Tool | Purpose |
|------|---------|
| [`src/eplan_pdf_to_dxf.py`](src/eplan_pdf_to_dxf.py) | Convert EPLAN-exported PDFs into editable DXF files |
| [`src/logix_to_eplan_csv.py`](src/logix_to_eplan_csv.py) | Convert ControlLogix L5X exports into EPLAN PLC import CSVs |
| [`src/CsvToEplan.py`](src/CsvToEplan.py) | Convert an enriched I/O CSV into a simple EPLAN XML structure |

---

# 1. EPLAN PDF to DXF Converter

Convert EPLAN-exported electrical diagram PDFs into editable DXF files that can be opened in AutoCAD, DraftSight, LibreCAD, or any CAD tool.

## The Problem

EPLAN exports PDFs with a **custom embedded font** using Identity-H CID encoding and no ToUnicode mapping table. This means:

- Standard PDF-to-CAD converters produce **garbled text**
- Numbers, dots, and special characters **disappear** completely
- LibreOffice Draw loses data, Inkscape crashes, Word 2016 misinterprets characters
- Commercial tools ($180+) still struggle with the custom encoding

The PDF *looks* perfect in any viewer (because the font glyphs render correctly), but every tool that tries to extract or convert the text gets garbage.

## The Solution

This tool **cracks the EPLAN font cipher** (a +29 shift on glyph IDs) and parses the raw PDF content stream directly, bypassing the broken text extraction layer.

It handles:
- Full vector geometry extraction (lines, rectangles, bezier curves)
- Complete text decoding including numbers, dots, dashes, and special characters
- PDF transformation matrix stack (CTM, text matrix, q/Q graphics state)
- Proper coordinate mapping to DXF

## Installation

```bash
pip install PyMuPDF ezdxf
```

## Usage

### Convert all pages
```bash
python src/eplan_pdf_to_dxf.py schematic.pdf
```

### Specify output directory
```bash
python src/eplan_pdf_to_dxf.py schematic.pdf output/
```

### Convert specific pages
```bash
python src/eplan_pdf_to_dxf.py schematic.pdf output/ --pages 1,5,10-20
```

### Custom shift value
If your EPLAN version uses a different encoding shift (rare):
```bash
python src/eplan_pdf_to_dxf.py schematic.pdf --shift 29
```

### Quiet mode
```bash
python src/eplan_pdf_to_dxf.py schematic.pdf -q
```

## Output

Each page becomes an individual DXF file (AutoCAD R2010 format) with three layers:
- **LINES** - Electrical schematic lines, wires, and curves
- **BORDER** - Title block borders and frames
- **TEXT** - All decoded text (component references, labels, descriptions)

## How It Works

### The EPLAN Font Cipher

EPLAN's PDF export uses a subset-embedded TrueType font (`JVNBVD+Eplan`) with Identity-H encoding. The glyph IDs are offset from standard ASCII by **+29 positions**:

| Glyph ID | + 29 | Character |
|----------|------|-----------|
| 3        | 32   | (space)   |
| 17       | 46   | `.`       |
| 19       | 48   | `0`       |
| 20       | 49   | `1`       |
| 36       | 65   | `A`       |
| 68       | 97   | `a`       |

Standard text extraction tools (PyMuPDF, pdfminer, etc.) interpret the low glyph IDs (< 32) as control characters and strip them, which is why **all numbers and punctuation disappear**.

### Matrix Stack Parser

The converter implements a full PDF graphics state machine tracking:
- `cm` - Current Transformation Matrix concatenation
- `q`/`Q` - Graphics state save/restore stack
- `BT`/`ET` - Text object boundaries
- `Tm` - Absolute text matrix
- `Td`/`TD` - Relative text position
- `T*` - New line with leading
- `Tf` - Font size
- `Tj`/`TJ` - Text show operators (hex-encoded glyph IDs)

## Known Limitations

- **German umlauts**: Characters like "u" in "Netzschutz" may not decode perfectly (the umlaut glyph uses a non-standard mapping). Manual correction of ~5 words per page may be needed.
- **Y-axis positioning**: Some text elements inside clipped regions may have slight Y-offset drift. X positions are consistently accurate.
- **Single font**: The tool assumes one EPLAN font per document. Multi-font PDFs would need additional mapping tables.
- **EPLAN version**: Tested with EPLAN exports via PDF-XChange 4.0 (2013-2015 era). Other versions may use different shift values - use the `--shift` parameter to adjust.

## Determining the Shift Value

If your EPLAN PDF uses a different shift, you can discover it by:

1. Find a page with known text (e.g., "REFORM" or your company name)
2. Extract the raw hex glyph IDs from the content stream
3. Compare: `expected_ASCII - glyph_ID = shift`

For example, if 'R' (ASCII 82) is encoded as glyph ID 53: shift = 82 - 53 = 29.

# 2. ControlLogix to EPLAN PLC Import CSV

Generate an EPLAN Electric P8 "PLC bulk data" import CSV directly from a
Rockwell Studio 5000 / RSLogix 5000 project, so the PLC schematic generation
tool can draw I/O cards with real tags, addresses, and function texts.

## Input format

Work from an **.L5X** export (Studio 5000: *File > Save As > L5X*). Among the
formats Rockwell can produce, L5X is the only one that is both standard XML
and carries the complete picture:

| Format | Verdict |
|--------|---------|
| .ACD   | Closed binary — not parseable |
| .L5K   | Custom text grammar — needs a bespoke parser |
| .RDF / .AML | Hardware tree only, **no tag database** |
| **.L5X** | XML with module tree (catalogs, slots, chassis) **and** all tags |

## Usage

Python 3.10+, standard library only:

```bash
python src/logix_to_eplan_csv.py PROJECT.L5X -o project_eplan.csv
```

Options:
- `--spares` — also emit unused points of referenced cards (FunctionText "Spare")
- `--include-hmi` — include PanelView/HMI-mapped points (excluded by default; not hardwired)
- `--logix-address` — keep raw Logix addresses (`Local:2:I.Data.3`) instead of EPLAN-style `I0.3` / `IW256`
- `--keep-duplicates` — emit every tag even when several alias one physical point

A mapping summary (modules per rack/slot, mapped/skipped/duplicate counts) is
printed to stderr; the CSV goes to the output file with the fixed header:

```
DeviceTag,Rack,Slot,ConnectionPoint,Address,DataType,SymbolicName,FunctionText
```

## What it handles

- Digital and analog I/O, local chassis and remote drops (ControlNet/EtherNet adapters); local rack = 1, each remote drop = 2, 3, ...
- Controller- and program-scoped alias tags, including alias-of-alias chains
- 1756 / 1769 / 5069 catalog classification (DI/DO/AI/AO + point count) with a heuristic fallback for unknown catalog numbers
- Several tags aliasing the same physical point are de-duplicated (EPLAN rejects duplicate connection points); extras are folded into the function text
- FunctionText is humanized from the tag name through an English/Spanish abbreviation dictionary (e.g. `HU_OIL_PRESSURE_PT1` → "Hydraulic Unit Oil Pressure PT1"); tag descriptions are used when present

## Claude Code skill

The converter is also packaged as a project skill in
[`.claude/skills/logix-to-eplan/`](.claude/skills/logix-to-eplan/SKILL.md) —
in a Claude Code session on this repo, asking for an "EPLAN I/O list from this
L5X" triggers it automatically.

---

## Contributing

Contributions welcome! Common improvements needed:
- Extended character mapping tables for different EPLAN versions
- Better handling of clipped region coordinate transforms
- Support for multiple fonts in the same document
- Batch processing scripts

## License

MIT License - See [LICENSE](LICENSE) file.

## Credits

Developed by **Abel Briones** at [Exxerpro Solutions](https://www.exxerpro.com) while converting REFORM Maschinenfabrik electrical documentation for industrial automation projects.

Built with:
- [PyMuPDF](https://pymupdf.readthedocs.io/) - PDF parsing
- [ezdxf](https://ezdxf.readthedocs.io/) - DXF file creation
