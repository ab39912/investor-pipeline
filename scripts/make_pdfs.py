#!/usr/bin/env python3
"""
Convert Methodology.md and Final_Summary.md to PDFs for submission.

Two strategies, tried in order:
  1. pandoc (best output, requires `pandoc` + LaTeX installed)
  2. markdown-pdf via npm (works without LaTeX, requires Node)
  3. Fallback: print instructions

Usage:
    python scripts/make_pdfs.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OUT = ROOT / "output"

CANDIDATE_NAME = "Ameya_Bhalerao"

JOBS = [
    (DOCS / "Methodology.md", OUT / f"Methodology_{CANDIDATE_NAME}.pdf"),
    (DOCS / "Final_Summary.md", OUT / f"Final_Summary_{CANDIDATE_NAME}.pdf"),
]


def try_pandoc(src: Path, dst: Path) -> bool:
    if not shutil.which("pandoc"):
        return False
    cmd = [
        "pandoc", str(src), "-o", str(dst),
        "--pdf-engine=xelatex",
        "-V", "geometry:margin=1in",
        "-V", "mainfont=Helvetica",
        "-V", "fontsize=11pt",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError:
        # Try without xelatex (use default engine)
        try:
            subprocess.run(
                ["pandoc", str(src), "-o", str(dst), "-V", "geometry:margin=1in"],
                check=True, capture_output=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False


def try_md_to_pdf_node(src: Path, dst: Path) -> bool:
    if not shutil.which("md-to-pdf") and not shutil.which("markdown-pdf"):
        return False
    cmd = "md-to-pdf" if shutil.which("md-to-pdf") else "markdown-pdf"
    try:
        subprocess.run([cmd, str(src), "-o", str(dst)], check=True, capture_output=True)
        return True
    except Exception:
        return False


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    for src, dst in JOBS:
        if not src.exists():
            print(f"SKIP: source missing: {src}")
            continue

        print(f"Converting {src.name} -> {dst.name}...")
        if try_pandoc(src, dst):
            print(f"  OK (pandoc): {dst}")
            continue
        if try_md_to_pdf_node(src, dst):
            print(f"  OK (markdown-pdf): {dst}")
            continue

        print(f"  FAILED: no PDF tool found")
        print(f"  Manual options:")
        print(f"    1. Install pandoc + LaTeX: brew install pandoc basictex")
        print(f"    2. Install md-to-pdf: npm install -g md-to-pdf")
        print(f"    3. Open {src} in VS Code, install 'Markdown PDF' extension, "
              "right-click -> Export PDF")
        print(f"    4. Paste markdown into any online md->pdf converter")
        return 1

    print("\nAll PDFs generated in output/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
