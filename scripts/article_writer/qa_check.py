#!/usr/bin/env python3
"""Mechanical QA check for article-writer output.

Usage: python qa_check.py <path-to-index.md> [--title "article title"]

Checks the objective, style-guide rules. Grammar and voice still need a
careful human-style read; this script only catches the mechanical stuff.
Exit code 0 = all checks passed, 1 = at least one failure.
"""
import argparse
import re
import sys
from pathlib import Path

FOOTER = "*Written by the aut0ps automated crawler, edited and assisted by the Copilot agent*"

# Words allowed to keep capitals in titles (proper nouns / acronyms are fine;
# this list is only for the checker's "obviously fine" shortlist — anything
# capitalized that is not all-caps gets flagged as a warning for review).
CONTRAST_PATTERNS = [
    r"\bit'?s not\b.{1,60}?\bit'?s\b",
    r"\bnot just\b.{1,60}?\bbut\b",
    r"\bisn'?t\b.{1,40}?\bit'?s\b",
    r"\bnot only\b.{1,60}?\bbut\b",
]


def check(path: str, title: str | None) -> int:
    text = Path(path).read_text(encoding="utf-8")
    lines = text.splitlines()
    failures, warnings = [], []

    # 1. em / en dashes
    for i, line in enumerate(lines, 1):
        if "—" in line:
            failures.append(f"line {i}: em dash (U+2014)")
        if "–" in line:
            failures.append(f"line {i}: en dash (U+2013)")

    # 2. YAML front matter
    if text.lstrip().startswith("---"):
        failures.append("file starts with '---': looks like YAML front matter")

    # 3. H1 in body (title belongs in Strapi, not the file)
    in_code = False
    for i, line in enumerate(lines, 1):
        if line.strip().startswith("```"):
            in_code = not in_code
        elif not in_code and re.match(r"^# \S", line):
            failures.append(f"line {i}: H1 heading in body")

    # 4. code fences without a language tag
    in_code = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_code and stripped == "```":
                failures.append(f"line {i}: code fence without language tag")
            in_code = not in_code

    # 5. attribution footer
    if FOOTER not in text:
        failures.append("missing attribution footer")
    elif not text.rstrip().endswith(FOOTER):
        warnings.append("attribution footer is not the last line")

    # 6. forced contrast framing (heuristic, review each hit)
    in_code = False
    for i, line in enumerate(lines, 1):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        for pat in CONTRAST_PATTERNS:
            if re.search(pat, line, re.IGNORECASE):
                warnings.append(f"line {i}: possible contrast framing: {line.strip()[:80]}")

    # 7. title lowercase (if provided)
    if title:
        words = title.split()
        for w in words:
            if w.isupper() and len(w) > 1:
                continue  # acronym
            if w[:1].isupper():
                warnings.append(
                    f"title word '{w}' is capitalized: fine only if it's a proper noun"
                )

    for f in failures:
        print(f"FAIL  {f}")
    for w in warnings:
        print(f"WARN  {w}")
    if not failures and not warnings:
        print("all checks passed")
    elif not failures:
        print(f"\n0 failures, {len(warnings)} warnings (review each warning manually)")
    else:
        print(f"\n{len(failures)} failures, {len(warnings)} warnings")
    return 1 if failures else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--title", default=None)
    args = ap.parse_args()
    sys.exit(check(args.file, args.title))
