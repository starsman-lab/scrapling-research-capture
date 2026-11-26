#!/usr/bin/env python3
"""Learn or recover a Scrapling adaptive CSS selector from local HTML files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

MAX_HTML_BYTES = 20_000_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use Scrapling adaptive parsing with local HTML snapshots; no network access."
    )
    parser.add_argument("mode", choices=("learn", "recover"))
    parser.add_argument("html_file", type=Path)
    parser.add_argument("--url", required=True, help="stable public page URL used as the adaptive domain key")
    parser.add_argument("--css", required=True, help="element CSS selector; omit ::text/::attr")
    parser.add_argument("--database", required=True, type=Path, help="SQLite file for adaptive state")
    parser.add_argument("--identifier", help="stable key; defaults to the CSS selector")
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        parser.error("--url must be an http(s) URL with a hostname")
    if "::text" in args.css or "::attr(" in args.css:
        parser.error("--css must select an element, not a text or attribute pseudo-selector")
    if args.database.resolve() == args.html_file.resolve():
        parser.error("--database must not overwrite the HTML input")
    args.identifier = args.identifier or args.css
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
        print("Scrapling Selector is unavailable; install requirements.txt in the active environment.", file=sys.stderr)
        return 2

    args.database.parent.mkdir(parents=True, exist_ok=True)
    page = Selector(
        content=raw_html,
        url=args.url,
        adaptive=True,
        storage_args={"storage_file": str(args.database.resolve()), "url": args.url},
    )
    exact = page.css(args.css)
    if args.mode == "learn":
        if len(exact) != 1:
            print(f"Learning requires exactly one matching element; found {len(exact)}.", file=sys.stderr)
            return 1
        saved = page.css(args.css, auto_save=True, identifier=args.identifier)
        result = {
            "mode": "learn",
            "identifier": args.identifier,
            "saved_count": len(saved),
            "sample": str(saved[0].get_all_text(separator=" ", strip=True))[:500],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    matches = list(page.css(args.css, adaptive=True, identifier=args.identifier))
    result = {
        "mode": "recover",
        "identifier": args.identifier,
        "exact_match_count": len(exact),
        "adaptive_match_count": len(matches),
        "samples": [str(item.get_all_text(separator=" ", strip=True))[:500] for item in matches[:10]],
        "manual_validation_required": True,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())

