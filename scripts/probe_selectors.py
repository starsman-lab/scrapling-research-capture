#!/usr/bin/env python3
"""Inspect Scrapling CSS selectors against a saved HTML file without networking."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MAX_HTML_BYTES = 20_000_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show match counts and sample values for CSS selectors in local HTML."
    )
    parser.add_argument("html_file", type=Path)
    parser.add_argument(
        "--css", action="append", required=True, metavar="SELECTOR",
        help="CSS selector; repeat to inspect multiple selectors",
    )
    parser.add_argument("--limit", type=int, default=20, help="maximum sample values per selector")
    parser.add_argument("--preview-chars", type=int, default=500, help="maximum characters per sample")
    args = parser.parse_args()
    if args.limit < 1 or args.preview_chars < 1:
        parser.error("--limit and --preview-chars must be positive")
    return args


def main() -> int:
    args = parse_args()
    try:
        raw_html = args.html_file.read_bytes()
    except OSError as exc:
        print(f"Cannot read HTML file: {exc}", file=sys.stderr)
        return 2
    if len(raw_html) > MAX_HTML_BYTES:
        print(f"HTML file exceeds the {MAX_HTML_BYTES}-byte safety limit", file=sys.stderr)
        return 2

    try:
        from scrapling import Selector
    except ImportError:
        print(
            "Scrapling Selector is unavailable. Install this skill's requirements.txt "
            "in the active Python environment.",
            file=sys.stderr,
        )
        return 2

    page = Selector(content=raw_html, url=args.html_file.resolve().as_uri())
    results = []
    failed = False
    for expression in args.css:
        try:
            matches = page.css(expression)
            if "::text" in expression or "::attr(" in expression:
                values = [str(value).strip() for value in matches.getall()]
            else:
                values = [
                    str(node.get_all_text(separator=" ", strip=True)).strip()
                    for node in matches
                ]
            results.append(
                {
                    "css": expression,
                    "match_count": len(matches),
                    "samples": [value[: args.preview_chars] for value in values[: args.limit]],
                    "samples_truncated": len(values) > args.limit,
                }
            )
        except Exception as exc:
            failed = True
            results.append({"css": expression, "error": type(exc).__name__})

    print(
        json.dumps(
            {"html_file": str(args.html_file), "results": results},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

