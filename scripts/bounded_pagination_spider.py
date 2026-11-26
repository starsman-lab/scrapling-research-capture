#!/usr/bin/env python3
"""Follow a site's explicit next-page link with strict Scrapling Spider bounds."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from capture_public_pages import redact_url, validate_public_url

MAX_PAGES_LIMIT = 50
USER_AGENT = "scrapling-research-capture/0.1"
CHALLENGE_MARKERS = (
    "verify you are human",
    "verify that you are human",
    "checking your browser",
    "unusual traffic from your computer network",
    "automated queries",
    "please complete the security check",
    "attention required! | cloudflare",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def path_in_scope(path: str, prefix: str) -> bool:
    if prefix == "/":
        return True
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Follow only a configured pagination link on one public host and path scope."
    )
    parser.add_argument("--start-url", required=True)
    parser.add_argument("--path-prefix", help="allowed URL path prefix; defaults to the start URL path")
    parser.add_argument("--next-selector", default='a[rel="next"]::attr(href)')
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--max-text-chars", type=int, default=30000)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not 1 <= args.max_pages <= MAX_PAGES_LIMIT:
        parser.error(f"--max-pages must be between 1 and {MAX_PAGES_LIMIT}")
    if args.delay_seconds < 1 or args.timeout <= 0 or args.max_text_chars <= 0:
        parser.error("delay must be at least 1 second; timeout and max text must be positive")
    try:
        args.start_url, args.host = validate_public_url(args.start_url)
    except ValueError as exc:
        parser.error(str(exc))
    start = urlsplit(args.start_url)
    args.origin = (start.scheme.lower(), start.hostname.lower(), start.port)
    prefix = args.path_prefix if args.path_prefix is not None else (start.path or "/")
    if not prefix.startswith("/"):
        parser.error("--path-prefix must start with '/'")
    args.path_prefix = prefix.rstrip("/") or "/"
    if not path_in_scope(start.path or "/", args.path_prefix):
        parser.error("start URL is outside --path-prefix")
    if args.output.resolve() == Path(__file__).resolve() or args.output.exists():
        parser.error("--output must be a new file path")
    return args


def main() -> int:
    args = parse_args()
    try:
        from scrapling.fetchers import FetcherSession
        from scrapling.spiders import Response, Spider
    except ImportError:
        print("Scrapling Spider is unavailable; install requirements.txt in the active environment.", file=sys.stderr)
        return 2

    class BoundedPaginationSpider(Spider):
        name = "bounded-pagination"
        start_urls = [args.start_url]
        allowed_domains = {args.host}
        robots_txt_obey = True
        concurrent_requests = 1
        concurrent_requests_per_domain = 1
        download_delay = args.delay_seconds

        def __init__(self):
            super().__init__()
            self._pages_seen = 0

        def configure_sessions(self, manager):
            manager.add(
                "default",
                FetcherSession(
                    timeout=args.timeout,
                    retries=1,
                    follow_redirects=False,
                    impersonate=None,
                    stealthy_headers=False,
                    headers={
                        "User-Agent": USER_AGENT
                        + " (+https://github.com/starsman-lab/scrapling-research-capture)"
                    },
                ),
            )

        async def parse(self, response: Response):
            self._pages_seen += 1
            status = int(response.status)
            source_url = redact_url(response.url)
            base = {"source_url": source_url, "retrieved_at_utc": utc_now(), "status": status}
            if status in {401, 403, 429, 503}:
                yield {**base, "error": "access_denied_or_rate_limited; stopped_without_retry"}
                return
            if not 200 <= status < 300:
                yield {**base, "error": "non_success_http_status"}
                return

            title = str(response.css("title::text").get("")).strip()
            text = str(
                response.get_all_text(
                    separator="\n", strip=True,
                    ignore_tags=("script", "style", "noscript", "svg"),
                )
            ).strip()
            combined = (title + "\n" + text[:10000]).lower()
            if any(marker in combined for marker in CHALLENGE_MARKERS):
                yield {**base, "error": "challenge_page; stopped_without_bypass"}
                return

            yield {
                **base,
                "title": title or None,
                "text": text[: args.max_text_chars],
                "text_truncated": len(text) > args.max_text_chars,
            }
            if self._pages_seen >= args.max_pages:
                return

            next_href = response.css(args.next_selector).get()
            if not next_href:
                return
            target = urljoin(response.url, str(next_href).strip())
            try:
                target, _ = validate_public_url(target)
            except ValueError:
                return
            parsed = urlsplit(target)
            target_origin = (parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port)
            if target_origin != args.origin or not path_in_scope(parsed.path or "/", args.path_prefix):
                return
            yield response.follow(target, callback=self.parse)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = BoundedPaginationSpider().start()
    items = list(result.items)
    with args.output.open("x", encoding="utf-8", newline="\n") as output:
        for item in items:
            output.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(
        f"Saved {len(items)} page records; requests={result.stats.requests_count}; "
        f"completed={result.completed}."
    )
    return 1 if any(item.get("error") for item in items) else 0


if __name__ == "__main__":
    raise SystemExit(main())

