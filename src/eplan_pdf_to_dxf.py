#!/usr/bin/env python3
"""
EPLAN PDF to DXF Converter
===========================
Converts EPLAN-exported electrical diagram PDFs to editable DXF files.

EPLAN exports PDFs with a custom embedded font (Identity-H CID encoding,
no ToUnicode mapping). This makes standard PDF-to-CAD converters fail:
text appears garbled, numbers disappear, and special characters are lost.

This tool solves the problem by:
1. Parsing the raw PDF content stream to extract hex-encoded glyph IDs
2. Decoding glyphs using the discovered +29 shift cipher
3. Tracking the full PDF matrix stack (CTM, text matrix, q/Q state)
4. Extracting vector geometry via PyMuPDF
5. Writing clean, editable DXF files with correct text and positions

Requirements:
    pip install PyMuPDF ezdxf

Usage:
    python eplan_pdf_to_dxf.py input.pdf [output_dir]
    python eplan_pdf_to_dxf.py input.pdf [output_dir] --pages 1,5,10-15
    python eplan_pdf_to_dxf.py input.pdf [output_dir] --shift 29

Author: Abel Briones / Exxerpro Solutions
License: MIT
"""

import argparse
import math
import os
import re
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("Error: PyMuPDF required. Install with: pip install PyMuPDF")

try:
    import ezdxf
except ImportError:
    sys.exit("Error: ezdxf required. Install with: pip install ezdxf")


# ============================================================
# GLYPH DECODING
# ============================================================

DEFAULT_SHIFT = 29

def decode_glyph_ids(hex_string: str, shift: int = DEFAULT_SHIFT) -> str:
    """
    Decode hex-encoded CID glyph IDs from EPLAN's custom font.

    EPLAN uses Identity-H encoding with no ToUnicode map. The glyph IDs
    are offset from ASCII by a fixed shift value (default: +29).

    Each glyph ID is 2 bytes (4 hex chars) in big-endian order.

    Args:
        hex_string: Hex-encoded glyph IDs (e.g., "003500280029003200300030")
        shift: Character shift value (default 29, discovered by analysis)

    Returns:
        Decoded text string
    """
    result = ""
    for i in range(0, len(hex_string), 4):
        if i + 4 <= len(hex_string):
            gid = int(hex_string[i:i+4], 16)
            char_code = gid + shift
            if 32 <= char_code <= 255:
                result += chr(char_code)
    return result


# ============================================================
# PDF MATRIX OPERATIONS
# ============================================================

def mat_multiply(a: list, b: list) -> list:
    """
    Multiply two PDF transformation matrices.

    PDF matrices are 3x3 stored as [a, b, c, d, e, f]:
        | a  b  0 |
        | c  d  0 |
        | e  f  1 |

    Args:
        a: First matrix [a, b, c, d, e, f]
        b: Second matrix [a, b, c, d, e, f]

    Returns:
        Result matrix [a, b, c, d, e, f]
    """
    return [
        a[0]*b[0] + a[1]*b[2],
        a[0]*b[1] + a[1]*b[3],
        a[2]*b[0] + a[3]*b[2],
        a[2]*b[1] + a[3]*b[3],
        a[4]*b[0] + a[5]*b[2] + b[4],
        a[4]*b[1] + a[5]*b[3] + b[5],
    ]


# ============================================================
# CONTENT STREAM TOKENIZER
# ============================================================

def tokenize_content(content: str) -> list:
    """
    Tokenize a PDF content stream into typed tokens.

    Returns list of (type, value) tuples:
        'n' = number, 'o' = operator, 'h' = hex string, 'a' = array
    """
    tokens = []
    i = 0
    n = len(content)
    while i < n:
        c = content[i]
        if c in ' \t\r\n':
            i += 1
            continue
        if c == '<':
            j = content.index('>', i)
            tokens.append(('h', content[i+1:j]))
            i = j + 1
        elif c == '[':
            j = content.index(']', i)
            tokens.append(('a', content[i+1:j]))
            i = j + 1
        elif c == '%':
            j = content.find('\n', i)
            i = (j + 1 if j >= 0 else n)
        elif c in '0123456789.-+':
            j = i + 1
            while j < n and content[j] in '0123456789.eE+-':
                j += 1
            tokens.append(('n', content[i:j]))
            i = j
        elif c.isalpha() or c in "/*'\"":
            j = i + 1
            while j < n and (content[j].isalnum() or content[j] in "_*'\""):
                j += 1
            tokens.append(('o', content[i:j]))
            i = j
        else:
            i += 1
    return tokens


# ============================================================
# PAGE CONVERTER
# ============================================================

def convert_page(doc, page_num: int, output_path: str, shift: int = DEFAULT_SHIFT) -> dict:
    """
    Convert a single PDF page to DXF.

    Extracts vector geometry via PyMuPDF and text via raw content
    stream parsing with full matrix stack tracking.

    Args:
        doc: PyMuPDF document
        page_num: Page index (0-based)
        output_path: Output DXF file path
        shift: Glyph ID shift value

    Returns:
        Dict with conversion statistics
    """
    page = doc[page_num]
    pw, ph = page.rect.width, page.rect.height

    # Create DXF
    dxf = ezdxf.new('R2010')
    msp = dxf.modelspace()
    dxf.layers.new('BORDER', dxfattribs={'color': 7})
    dxf.layers.new('LINES', dxfattribs={'color': 7})
    dxf.layers.new('TEXT', dxfattribs={'color': 3})

    stats = {'lines': 0, 'texts': 0, 'curves': 0}

    # ---- VECTOR GEOMETRY (via PyMuPDF) ----
    for path in page.get_drawings():
        for item in path.get("items", []):
            kind = item[0]
            if kind == "l":
                p1, p2 = item[1], item[2]
                msp.add_line(
                    (p1.x * 0.3528, (ph - p1.y) * 0.3528),
                    (p2.x * 0.3528, (ph - p2.y) * 0.3528),
                    dxfattribs={'layer': 'LINES'}
                )
                stats['lines'] += 1
            elif kind == "re":
                r = item[1]
                x, y = r.x0 * 0.3528, (ph - r.y1) * 0.3528
                w, h = r.width * 0.3528, r.height * 0.3528
                pts = [(x, y), (x+w, y), (x+w, y+h), (x, y+h), (x, y)]
                for i in range(4):
                    msp.add_line(pts[i], pts[i+1], dxfattribs={'layer': 'BORDER'})
                    stats['lines'] += 1
            elif kind == "c":
                p0, c1, c2, p3 = item[1], item[2], item[3], item[4]
                prev = None
                for ti in range(9):
                    t = ti / 8
                    mt = 1 - t
                    x = mt**3*p0.x + 3*mt**2*t*c1.x + 3*mt*t**2*c2.x + t**3*p3.x
                    y = mt**3*p0.y + 3*mt**2*t*c1.y + 3*mt*t**2*c2.y + t**3*p3.y
                    pt = (x * 0.3528, (ph - y) * 0.3528)
                    if prev:
                        msp.add_line(prev, pt, dxfattribs={'layer': 'LINES'})
                        stats['lines'] += 1
                    prev = pt
                stats['curves'] += 1

    # ---- TEXT (via raw content stream parsing) ----
    raw_bytes = b''
    for xr in page.get_contents():
        raw_bytes += doc.xref_stream(xr)
    content = raw_bytes.decode('latin-1')
    tokens = tokenize_content(content)

    # PDF graphics state machine
    ctm_stack = []
    ctm = [1, 0, 0, 1, 0, 0]
    tm = [1, 0, 0, 1, 0, 0]
    lm = [1, 0, 0, 1, 0, 0]
    font_size = 1.0
    leading = 0.0
    in_text = False
    num_stack = []

    def emit_text(txt):
        """Add decoded text to DXF at current matrix position."""
        if not txt.strip():
            return
        combined = mat_multiply(tm, ctm)
        tx, ty = combined[4], combined[5]
        sy = math.sqrt(combined[2]**2 + combined[3]**2)
        rot = math.degrees(math.atan2(combined[1], combined[0]))
        x_mm = tx * 0.3528
        y_mm = ty * 0.3528  # Direct: CTM already in PDF coordinate space
        h_mm = font_size * sy * 0.3528
        try:
            msp.add_text(txt, dxfattribs={
                'layer': 'TEXT',
                'height': max(h_mm, 0.5),
                'rotation': -rot,
                'insert': (x_mm, y_mm),
            })
            stats['texts'] += 1
        except Exception:
            pass

    for ttype, tval in tokens:
        if ttype == 'n':
            try:
                num_stack.append(float(tval))
            except ValueError:
                pass
            continue

        if ttype == 'h' and in_text:
            emit_text(decode_glyph_ids(tval, shift))
            num_stack.clear()
            continue

        if ttype == 'a' and in_text:
            hex_parts = re.findall(r'<([0-9A-Fa-f]+)>', tval)
            full = "".join(decode_glyph_ids(hp, shift) for hp in hex_parts)
            emit_text(full)
            num_stack.clear()
            continue

        if ttype == 'o':
            op = tval
            if op == 'q':
                ctm_stack.append(ctm[:])
            elif op == 'Q' and ctm_stack:
                ctm = ctm_stack.pop()
            elif op == 'cm' and len(num_stack) >= 6:
                ctm = mat_multiply(num_stack[-6:], ctm)
                num_stack.clear()
            elif op == 'BT':
                in_text = True
                tm = [1, 0, 0, 1, 0, 0]
                lm = [1, 0, 0, 1, 0, 0]
            elif op == 'ET':
                in_text = False
            elif op == 'Tf' and in_text and num_stack:
                font_size = abs(num_stack[-1])
                num_stack.clear()
            elif op == 'TL' and in_text and num_stack:
                leading = num_stack[-1]
                num_stack.clear()
            elif op == 'Tm' and in_text and len(num_stack) >= 6:
                tm = num_stack[-6:][:]
                lm = tm[:]
                num_stack.clear()
            elif op == 'Td' and in_text and len(num_stack) >= 2:
                lm = mat_multiply([1, 0, 0, 1, num_stack[-2], num_stack[-1]], lm)
                tm = lm[:]
                num_stack.clear()
            elif op == 'TD' and in_text and len(num_stack) >= 2:
                leading = -num_stack[-1]
                lm = mat_multiply([1, 0, 0, 1, num_stack[-2], num_stack[-1]], lm)
                tm = lm[:]
                num_stack.clear()
            elif op == "T*" and in_text:
                lm = mat_multiply([1, 0, 0, 1, 0, -leading], lm)
                tm = lm[:]
            elif op in ('Tj', 'TJ'):
                num_stack.clear()
            elif not in_text:
                num_stack.clear()

    dxf.saveas(output_path)
    return stats


# ============================================================
# CLI ENTRY POINT
# ============================================================

def parse_page_range(page_str: str, total_pages: int) -> list:
    """
    Parse page range string like '1,3,5-10' into a list of 0-based indices.
    """
    pages = set()
    for part in page_str.split(','):
        part = part.strip()
        if '-' in part:
            start, end = part.split('-', 1)
            start = max(1, int(start))
            end = min(total_pages, int(end))
            pages.update(range(start - 1, end))
        else:
            pg = int(part)
            if 1 <= pg <= total_pages:
                pages.add(pg - 1)
    return sorted(pages)


def main():
    parser = argparse.ArgumentParser(
        description='Convert EPLAN PDF electrical diagrams to editable DXF files.',
        epilog='Example: python eplan_pdf_to_dxf.py schematic.pdf output/ --pages 1-10'
    )
    parser.add_argument('input', help='Input PDF file path')
    parser.add_argument('output', nargs='?', default=None,
                        help='Output directory (default: <input>_DXF/)')
    parser.add_argument('--pages', '-p', default=None,
                        help='Page range to convert (e.g., "1,3,5-10"). Default: all pages')
    parser.add_argument('--shift', '-s', type=int, default=DEFAULT_SHIFT,
                        help=f'Glyph ID shift value (default: {DEFAULT_SHIFT})')
    parser.add_argument('--prefix', default=None,
                        help='Output filename prefix (default: derived from input)')
    parser.add_argument('--quiet', '-q', action='store_true',
                        help='Suppress per-page output')

    args = parser.parse_args()

    if not os.path.isfile(args.input):
        sys.exit(f"Error: File not found: {args.input}")

    # Output directory
    if args.output is None:
        base = os.path.splitext(args.input)[0]
        args.output = base + '_DXF'
    os.makedirs(args.output, exist_ok=True)

    # Filename prefix
    if args.prefix is None:
        args.prefix = os.path.splitext(os.path.basename(args.input))[0]

    # Open PDF
    doc = fitz.open(args.input)
    total_pages = len(doc)

    # Parse page range
    if args.pages:
        page_indices = parse_page_range(args.pages, total_pages)
    else:
        page_indices = list(range(total_pages))

    print(f"EPLAN PDF to DXF Converter")
    print(f"Input:  {args.input} ({total_pages} pages)")
    print(f"Output: {args.output}/")
    print(f"Pages:  {len(page_indices)} | Shift: +{args.shift}")
    print()

    total_stats = {'lines': 0, 'texts': 0, 'curves': 0}

    for pg in page_indices:
        filename = f"{args.prefix}_Page{pg+1:02d}.dxf"
        filepath = os.path.join(args.output, filename)

        stats = convert_page(doc, pg, filepath, args.shift)

        for k in total_stats:
            total_stats[k] += stats[k]

        if not args.quiet:
            size_kb = os.path.getsize(filepath) / 1024
            print(f"  Page {pg+1:2d}/{total_pages}: "
                  f"{stats['lines']:5d} lines, "
                  f"{stats['texts']:4d} texts, "
                  f"{size_kb:7.1f} KB -> {filename}")

    doc.close()

    total_size = sum(
        os.path.getsize(os.path.join(args.output, f))
        for f in os.listdir(args.output) if f.endswith('.dxf')
    )

    print(f"\nConversion complete!")
    print(f"  Pages:  {len(page_indices)}")
    print(f"  Lines:  {total_stats['lines']}")
    print(f"  Texts:  {total_stats['texts']}")
    print(f"  Curves: {total_stats['curves']}")
    print(f"  Size:   {total_size/1024:.0f} KB ({total_size/1024/1024:.1f} MB)")


if __name__ == '__main__':
    main()
