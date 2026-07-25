#!/usr/bin/env python3
"""Render a Quarto literature review to PDF without Quarto.

Quarto is unavailable in this container, so this reproduces the parts of its
pipeline the manuscript actually uses:

  1. resolve ``{{< include ... >}}`` shortcodes (Quarto-specific, pandoc has
     no idea about them) into a single markdown document
  2. pandoc --citeproc, resolving [@key] against references.bib with the same
     AMA CSL the .qmd names in its ``csl:`` field
  3. pandoc's typst writer, then compile via the typst Python module

Usage: render_pdf.py <literature_review.qmd> [-o out.pdf]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

INCLUDE_RE = re.compile(r"\{\{<\s*include\s+([^>]+?)\s*>\}\}")
MAX_INCLUDE_DEPTH = 10


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return (yaml_frontmatter, body). Frontmatter is '' when absent."""
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end == -1:
        return "", text
    return text[3:end].strip("\n"), text[end + 4 :].lstrip("\n")


def resolve_includes(text: str, base: Path, depth: int = 0) -> str:
    """Recursively inline Quarto include shortcodes relative to *base*."""
    if depth > MAX_INCLUDE_DEPTH:
        raise RecursionError(f"include depth exceeded {MAX_INCLUDE_DEPTH} in {base}")

    def repl(m: re.Match) -> str:
        target = base / m.group(1).strip()
        if not target.exists():
            print(f"  warning: missing include {target}", file=sys.stderr)
            return ""
        # Included files carry body text only, but strip frontmatter defensively.
        _, body = split_frontmatter(target.read_text(encoding="utf-8"))
        return resolve_includes(body, target.parent, depth + 1).strip() + "\n"

    return INCLUDE_RE.sub(repl, text)


def yaml_scalar(value: str) -> str:
    """Quote a YAML scalar so colons in titles cannot break the header."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_document(qmd: Path) -> str:
    """Produce a self-contained pandoc markdown document from *qmd*."""
    raw = qmd.read_text(encoding="utf-8")
    front, body = split_frontmatter(raw)
    base = qmd.parent

    # Pull the metadata pandoc needs; the .qmd's format/csl blocks are Quarto
    # config that the typst writer would not understand.
    meta: dict[str, str] = {}
    for key in ("title", "subtitle", "date", "author"):
        m = re.search(rf"^{key}:\s*(.+)$", front, re.M)
        if m:
            meta[key] = m.group(1).strip().strip('"')

    # The abstract is itself an include nested inside the YAML block.
    abstract = ""
    m = re.search(r"^abstract:\s*\|\s*\n((?:[ \t]+.*\n?)*)", front, re.M)
    if m:
        dedented = "\n".join(line.strip() for line in m.group(1).splitlines())
        abstract = resolve_includes(dedented, base).strip()

    header = ["---"]
    for key, value in meta.items():
        header.append(f"{key}: {yaml_scalar(value)}")
    if abstract:
        header.append("abstract: |")
        header.extend(f"  {line}" if line else "" for line in abstract.splitlines())
    header.append("---")

    return "\n".join(header) + "\n\n" + resolve_includes(body, base).strip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("qmd", type=Path)
    ap.add_argument("-o", "--output", type=Path)
    ap.add_argument("--csl", type=Path, default=Path("/tmp/ama.csl"))
    ap.add_argument("--keep-intermediates", action="store_true")
    args = ap.parse_args()

    qmd = args.qmd.resolve()
    if not qmd.exists():
        print(f"error: {qmd} not found", file=sys.stderr)
        return 1

    out_pdf = (args.output or qmd.with_suffix(".pdf")).resolve()
    workdir = qmd.parent
    md_path = workdir / (qmd.stem + ".flat.md")
    typ_path = workdir / (qmd.stem + ".typ")

    print(f"[1/3] resolving includes from {qmd.name}")
    md_path.write_text(build_document(qmd), encoding="utf-8")
    print(f"      -> {md_path.name} ({len(md_path.read_text(encoding='utf-8')):,} chars)")

    import pypandoc

    bib = workdir / "references.bib"
    extra = [
        "--citeproc",
        "--standalone",
        f"--resource-path={workdir}",
        "--wrap=preserve",
        "-V", "paper:a4",
        "-V", "margin-x:1in",
        "-V", "margin-y:1in",
        "-V", "fontsize:11pt",
    ]
    if bib.exists():
        extra += [f"--bibliography={bib}"]
        print(f"[2/3] pandoc -> typst (citeproc against {bib.name})")
    else:
        print("[2/3] pandoc -> typst (no bibliography found)")
    if args.csl.exists():
        extra += [f"--csl={args.csl}"]

    pypandoc.convert_file(
        str(md_path),
        to="typst",
        format="markdown",
        outputfile=str(typ_path),
        extra_args=extra,
    )
    print(f"      -> {typ_path.name} ({typ_path.stat().st_size:,} bytes)")

    print("[3/3] typst compile -> pdf")
    import typst

    typst.compile(str(typ_path), output=str(out_pdf), root=str(workdir))
    print(f"      -> {out_pdf} ({out_pdf.stat().st_size:,} bytes)")

    if not args.keep_intermediates:
        md_path.unlink(missing_ok=True)
        typ_path.unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
