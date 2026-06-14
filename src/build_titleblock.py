#!/usr/bin/env python3
"""build_titleblock.py — Exxerpro QElectroTech title block, cloned from ISO 7200.

Exxerpro is ISO 9001 certified, so the drawing title block follows the ISO 7200
layout. This clones QET's bundled ISO7200_A4_V1 template verbatim (its standard
grid, fields and multilingual — incl. Spanish — labels) and only:

  * replaces the embedded logo with the Exxerpro SVG, *aspect-fitted* to the
    template's logo cell so QET's stretch-to-fill cannot distort it (we pad the
    SVG viewBox to the cell's exact aspect ratio, centering the artwork), and
  * blanks QElectroTech's own e-mail / website default literals.

The FECHA cell stays bound to the project's `%{date}` additional field; the
generator fills it with the static creation/release date (config-driven) so the
record keeps the same date across regenerations — traceability, not "today".

Stdlib only.
  python build_titleblock.py            # -> assets/exxerpro.titleblock
  python build_titleblock.py --install  # also copy into the user QET dir
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ISO7200 = Path(r"C:\Program Files\QElectroTech\titleblocks\ISO7200_A4_V1.titleblock")
DEFAULT_SVG = REPO / "assets" / "logotipo_exxerpro_transparente-01.svg"
DEFAULT_OUT = REPO / "assets" / "exxerpro.titleblock"
LOGO_RESOURCE = "exxerpro.svg"


def svg_root(svg_text: str) -> str:
    """Just the <svg>…</svg> root: drop the XML declaration and any leading
    comments/PIs so it can be inlined under <logo>."""
    start = svg_text.find("<svg")
    if start < 0:
        raise ValueError("no <svg> root element found")
    return svg_text[start:].rstrip()


def _root_attr(svg: str, name: str) -> float | None:
    m = re.search(rf'<svg\b[^>]*?\b{name}="([\d.]+)', svg)
    return float(m.group(1)) if m else None


def fit_svg_to_cell(svg_text: str, cell_w: float, cell_h: float) -> str:
    """Pad the SVG viewBox to the cell's aspect ratio, centering the artwork, so
    that when QElectroTech stretches the logo to fill the (cell_w x cell_h) cell
    the scaling is uniform — no width/height squish. Returns the <svg> root with
    its width/height/viewBox rewritten; all artwork is untouched."""
    svg = svg_root(svg_text)
    ow = _root_attr(svg, "width")
    oh = _root_attr(svg, "height")
    if not ow or not oh:
        vb = re.search(r'viewBox="([\d.\s-]+)"', svg)
        if vb:
            _, _, ow, oh = (float(x) for x in vb.group(1).split())
    if not ow or not oh:
        raise ValueError("cannot determine SVG intrinsic size")

    orig_aspect = ow / oh
    cell_aspect = cell_w / cell_h
    if cell_aspect >= orig_aspect:           # cell relatively wider -> pad width
        new_h, new_w = oh, oh * cell_aspect
        minx, miny = -(new_w - ow) / 2, 0.0
    else:                                    # cell relatively taller -> pad height
        new_w, new_h = ow, ow / cell_aspect
        minx, miny = 0.0, -(new_h - oh) / 2

    open_m = re.match(r"<svg\b([^>]*)>", svg, re.S)
    attrs, rest = open_m.group(1), svg[open_m.end():]
    attrs = re.sub(r'\s+(?:width|height|viewBox)="[^"]*"', "", attrs)
    new_open = (f'<svg{attrs} width="{new_w:.3f}" height="{new_h:.3f}" '
                f'viewBox="{minx:.3f} {miny:.3f} {new_w:.3f} {new_h:.3f}">')
    return new_open + rest


def _set_attrs(tag: str, **attrs) -> str:
    """Set/replace attributes on a single XML start tag string."""
    out = tag
    for k, v in attrs.items():
        if re.search(rf'\b{k}="', out):
            out = re.sub(rf'\b{k}="[^"]*"', f'{k}="{v}"', out)
        elif out.rstrip().endswith("/>"):        # self-closing tag
            out = re.sub(r"\s*/>\s*$", f' {k}="{v}"/>', out)
        else:                                    # <tag ...>
            out = re.sub(r"\s*>\s*$", f' {k}="{v}">', out)
    return out


def _col_widths(cols_spec: str):
    return [p for p in cols_spec.split(";") if p.strip()]


def build_from_iso7200(iso_text: str, svg_text: str) -> str:
    # Put the logo in the big empty LEFT block (grid column 0, blank across every
    # row in stock ISO7200). We turn that flexible r100% column into a fixed-
    # width logo cell whose width matches the logo aspect exactly (height = full
    # title-block height), so the logo is prominent AND undistorted; the title
    # column then takes the r100% so the block still fills the page width. The
    # company-name (owner) text keeps its original cell.
    g = re.search(r'<grid cols="([^"]*)" rows="([^"]*)"', iso_text)
    cols_spec, rows_spec = g.group(1), g.group(2)
    cols = _col_widths(cols_spec)
    row_sizes = [float(re.match(r"[lrc]?([\d.]+)", p).group(1))
                 for p in rows_spec.split(";") if p.strip()]
    n_rows = len(row_sizes)
    block_h = sum(row_sizes)

    svg = svg_root(svg_text)
    ow, oh = _root_attr(svg, "width"), _root_attr(svg, "height")
    logo_w = round(block_h * (ow / oh))          # match aspect -> no distortion
    fitted = fit_svg_to_cell(svg_text, logo_w, block_h)

    out = iso_text
    # 1. rename the template
    out = re.sub(r'(<titleblocktemplate\b[^>]*\bname=")[^"]*(")',
                 r"\1exxerpro\2", out, count=1)
    # 2. replace ALL embedded logos with our single fitted Exxerpro logo
    new_logos = (f'<logos>\n        '
                 f'<logo storage="xml" type="svg" name="{LOGO_RESOURCE}">'
                 f'{fitted}</logo>\n    </logos>')
    out = re.sub(r"<logos>.*?</logos>", lambda _m: new_logos, out,
                 count=1, flags=re.S)
    # 3. resize the grid: column 0 becomes the fixed-width logo cell; the title
    #    column (4) takes the r100% so the block still spans the full page width.
    cols[0] = f"{logo_w}px"
    cols[4] = "r100%"
    out = out.replace(f'cols="{cols_spec}"', f'cols="{";".join(cols)};"', 1)
    # 4. move the logo cell into column 0, spanning the full title-block height
    logo_tag = re.search(r'<logo\b[^>]*\bname="logo"[^>]*?/?>', out).group(0)
    new_logo = _set_attrs(logo_tag, row="0", col="0", rowspan=str(n_rows),
                          colspan="1", resource=LOGO_RESOURCE)
    out = out.replace(logo_tag, new_logo, 1)
    # 5. de-brand QET's own default literals (keep cells; blank the values)
    out = out.replace("qet@lists.tuxfamily.org", "")
    out = out.replace("Qelectrotech.org", "")
    return out, (logo_w, block_h)


def user_titleblocks_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "QElectroTech" / "QElectroTech" / "titleblocks" if appdata else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iso", type=Path, default=ISO7200)
    ap.add_argument("--svg", type=Path, default=DEFAULT_SVG)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--install", action="store_true")
    args = ap.parse_args(argv)

    xml, (cw, ch) = build_from_iso7200(
        args.iso.read_text(encoding="utf-8"),
        args.svg.read_text(encoding="utf-8"))
    args.out.write_text(xml, encoding="utf-8")
    print(f"logo cell: {cw:.0f}x{ch:.0f}px (aspect {cw/ch:.2f}:1)", file=sys.stderr)
    print(f"wrote {args.out} ({len(xml)} bytes)", file=sys.stderr)

    if args.install:
        dest_dir = user_titleblocks_dir()
        if not dest_dir:
            print("warning: %APPDATA% unset; cannot install", file=sys.stderr)
            return 1
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "exxerpro.titleblock"
        dest.write_text(xml, encoding="utf-8")
        print(f"installed {dest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
