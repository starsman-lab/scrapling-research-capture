#!/usr/bin/env python3
"""Extract structured records from saved HTML with Scrapling CSS selectors."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from capture_public_pages import redact_url

MAX_HTML_BYTES = 20_000_000


def parse_bindings(items: list[str], option: str) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"{option} expects NAME=CSS_SELECTOR")
        name, selector = item.split("=", 1)
        name, selector = name.strip(), selector.strip()
        if not name or not selector or name in bindings:
            raise ValueError(f"{option} contains an empty or duplicate field")
        bindings[name] = selector
    return bindings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract structured records from a local HTML snapshot; no network access."
    )
    parser.add_argument("html_file", type=Path)
    parser.add_argument("--source-url", required=True, help="page URL to preserve as provenance")
    parser.add_argument("--record-selector", required=True, help="CSS selector for each record")
    parser.add_argument(
        "--field", action="append", default=[], metavar="NAME=SELECTOR",
        help="single-value field; repeat for more fields",
    )
    parser.add_argument(
        "--multi", action="append", default=[], metavar="NAME=SELECTOR",
        help="multi-value field; repeat for list-valued fields",
    )
    parser.add_argument("--require-field", action="append", default=[], metavar="NAME")
    parser.add_argument("--output", required=True, type=Path, help="JSONL output path")
    parser.add_argument("--max-value-chars", type=int, default=5000)
    parser.add_argument("--append", action="store_true")
    args = parser.parse_args()

    try:
        args.fields = parse_bindings(args.field, "--field")
        args.multi_fields = parse_bindings(args.multi, "--multi")
    except ValueError as exc:
        parser.error(str(exc))
    overlap = set(args.fields) & set(args.multi_fields)
    if overlap:
        parser.error("field names must be unique across --field and --multi")
    known = set(args.fields) | set(args.multi_fields)
    if not known:
        parser.error("provide at least one --field or --multi")
    if set(args.require_field) - known:
        parser.error("--require-field must name a configured --field or --multi")
    if args.max_value_chars < 1:
        parser.error("--max-value-chars must be positive")
    if args.output.resolve() == args.html_file.resolve():
        parser.error("--output must not overwrite the HTML input")
    if args.output.exists() and not args.append:
        parser.error("output already exists; choose another path or pass --append")
    return args


def css_values(node, expression: str, max_chars: int) -> list[str]:
    selected = node.css(expression)
    if "::text" in expression or "::attr(" in expression:
        raw = selected.getall()
    else:
        raw = [element.get_all_text(separator=" ", strip=True) for element in selected]
    return [str(value).strip()[:max_chars] for value in raw if str(value).strip()]


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

    page = Selector(content=raw_html, url=args.source_url)
    records = page.css(args.record_selector)
    if not records:
        print("Record selector matched no elements.", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if args.append else "x"
    extracted_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    invalid_records = 0
    with args.output.open(mode, encoding="utf-8", newline="\n") as output:
        for index, record in enumerate(records, 1):
            fields: dict[str, object] = {}
            for name, expression in args.fields.items():
                values = css_values(record, expression, args.max_value_chars)
                fields[name] = values[0] if values else None
            for name, expression in args.multi_fields.items():
                fields[name] = css_values(record, expression, args.max_value_chars)

            missing = [
                name for name in args.require_field
                if not fields.get(name) or fields.get(name) == []
            ]
            valid = not missing
            invalid_records += not valid
            row = {
                "source_url": redact_url(args.source_url),
                "extracted_at_utc": extracted_at,
                "record_index": index,
                "valid": valid,
                "missing_required_fields": missing,
                "fields": fields,
            }
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
        output.flush()

    print(f"Extracted {len(records)} records; {invalid_records} failed required-field checks.")
    return 1 if invalid_records else 0


if __name__ == "__main__":
    raise SystemExit(main())

