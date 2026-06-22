#!/usr/bin/env python3
"""ocr_scanned_pdf.py — OCR a scanned (image-only) PDF page by page.

Renders each PDF page to a high-DPI PNG with PyMuPDF, then runs Tesseract
(spa+eng by default) over each image, writing:
  <out>/page_XX.png         the rendered raster (kept for visual cross-check)
  <out>/page_XX.txt         per-page OCR text
  <out>/_all_pages.txt      combined text, page-delimited

Stdlib + PyMuPDF + a Tesseract binary. Language data and the exe are taken from
explicit paths so the (x86) PATH install (no tessdata) is bypassed.
"""
from __future__ import annotations
import os, sys, subprocess, argparse
import fitz  # PyMuPDF

TESS = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESSDATA = r"C:\Program Files\Tesseract-OCR\tessdata"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", required=True, help="output folder")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--lang", default="spa+eng")
    ap.add_argument("--tess", default=TESS)
    ap.add_argument("--tessdata", default=TESSDATA)
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    env = dict(os.environ, TESSDATA_PREFIX=args.tessdata)
    doc = fitz.open(args.pdf)
    zoom = args.dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)

    combined = []
    for i, page in enumerate(doc):
        n = i + 1
        png = os.path.join(args.out, f"page_{n:02d}.png")
        txt = os.path.join(args.out, f"page_{n:02d}.txt")
        pix = page.get_pixmap(matrix=mat)
        pix.save(png)
        # tesseract writes <stem>.txt; pass stem (no extension)
        stem = txt[:-4]
        r = subprocess.run(
            [args.tess, png, stem, "-l", args.lang, "--psm", "6"],
            env=env, capture_output=True, text=True,
        )
        if r.returncode != 0:
            sys.stderr.write(f"p{n}: tesseract error: {r.stderr.strip()}\n")
            continue
        with open(txt, encoding="utf-8", errors="replace") as f:
            page_text = f.read()
        combined.append(f"\n===== PAGE {n:02d} =====\n{page_text}")
        sys.stderr.write(f"p{n}: {len(page_text.strip())} chars\n")

    with open(os.path.join(args.out, "_all_pages.txt"), "w",
              encoding="utf-8") as f:
        f.write("".join(combined))
    sys.stderr.write(f"done: {len(doc)} pages -> {args.out}\n")


if __name__ == "__main__":
    main()
