#!/usr/bin/env python3
"""Capture a user-supplied list of public pages with Scrapling's HTTP session."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

USER_AGENT = "scrapling-research-capture/0.1"
MAX_PAGES_LIMIT = 50
STOP_STATUSES = {401, 403, 429, 503}
CHALLENGE_MARKERS = (
    "verify you are human",
    "verify that you are human",
    "checking your browser",
    "unusual traffic from your computer network",
    "automated queries",
    "please complete the security check",
    "attention required! | cloudflare",
)
SENSITIVE_QUERY_KEY = re.compile(
    r"(?:token|api[_-]?key|secret|auth|password|passwd|signature|session|cookie)",
    re.IGNORECASE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_public_url(value: str) -> tuple[str, str]:
    """Accept explicit HTTP(S) hostnames; reject credentials and local/IP targets."""
    value = value.strip()
    try:
        parsed = urlsplit(value)
        _ = parsed.port  # Force validation of malformed ports.
    except ValueError as exc:
        raise ValueError("malformed URL") from exc

    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("URL scheme must be http or https")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("URL must have a hostname and must not contain credentials")
    if any(SENSITIVE_QUERY_KEY.search(key) for key, _ in parse_qsl(parsed.query)):
        raise ValueError("URL query appears to contain a credential or token")

    host = parsed.hostname.rstrip(".").lower()
    if re.fullmatch(r"[0-9.]+", host):
        raise ValueError("numeric or IP-like hostnames are not accepted")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("literal IP URLs are not accepted; use a public hostname")

    if (
        "." not in host
        or host in {"localhost", "localhost.localdomain"}
        or host.endswith((".localhost", ".local", ".internal", ".lan", ".test", ".invalid"))
    ):
        raise ValueError("local or single-label hostnames are not accepted")

    return value, host


def normalize_domain(value: str) -> str:
    _, host = validate_public_url("https://" + value.strip())
    return host


def redact_url(value: str) -> str:
    parsed = urlsplit(value)
    query = [
        (key, "[REDACTED]" if SENSITIVE_QUERY_KEY.search(key) else item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def read_urls(path: Path, max_pages: int) -> list[tuple[str, str]]:
    urls: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        try:
            request_url, host = validate_public_url(value)
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
        if request_url not in seen:
            urls.append((request_url, host))
            seen.add(request_url)
        if len(urls) > max_pages:
            raise ValueError(f"URL list exceeds --max-pages={max_pages}")
    if not urls:
        raise ValueError("URL list contains no HTTP(S) URLs")
    return urls


def allowed_by_domain(host: str, allowed_domains: list[str]) -> bool:
    return not allowed_domains or any(
        host == domain or host.endswith("." + domain) for domain in allowed_domains
    )


def paced_get(session, url: str, host: str, delay: float, last_started: dict[str, float]):
    now = time.monotonic()
    wait = delay - (now - last_started.get(host, now))
    if wait > 0:
        time.sleep(wait)
    last_started[host] = time.monotonic()
    return session.get(url)


def get_robots_policy(session, page_url: str, host: str, delay: float,
                      last_started: dict[str, float]):
    parsed = urlsplit(page_url)
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    robots_url = origin + "/robots.txt"
    response = paced_get(session, robots_url, host, delay, last_started)
    status = int(response.status)

    if status in {404, 410}:
        return None, status, None
    if status != 200:
        return None, status, "robots_unavailable"

    body = bytes(response.body).decode("utf-8", errors="replace")
    if any(marker in body[:10000].lower() for marker in CHALLENGE_MARKERS):
        return None, status, "robots_challenge_page"

    policy = RobotFileParser()
    policy.parse(body.splitlines())
    return policy, status, None


def text_value(page, selector: str) -> str | None:
    value = page.css(selector).get()
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def page_text(page) -> str:
    containers = page.css("main") or page.css("article")
    root = containers[0] if containers else page
    return str(
        root.get_all_text(
            separator="\n",
            strip=True,
            ignore_tags=("script", "style", "noscript", "svg"),
        )
    ).strip()


def write_record(output, record: dict) -> None:
    output.write(json.dumps(record, ensure_ascii=False) + "\n")
    output.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture only the public URLs listed in a local text file."
    )
    parser.add_argument("--urls", required=True, type=Path, help="UTF-8 file: one URL per line")
    parser.add_argument("--output", required=True, type=Path, help="JSONL output path")
    parser.add_argument(
        "--allow-domain", action="append", default=[], metavar="DOMAIN",
        help="optional host allowlist; repeat for multiple domains, including subdomains",
    )
    parser.add_argument("--max-pages", type=int, default=10, help="target page cap (1-50)")
    parser.add_argument(
        "--delay-seconds", type=float, default=2.0,
        help="minimum delay between requests to the same host (at least 1 second)",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="per-request timeout in seconds")
    parser.add_argument(
        "--max-text-chars", type=int, default=40000,
        help="maximum extracted text characters stored per page",
    )
    parser.add_argument("--save-html-dir", type=Path, help="optional directory for successful HTML responses")
    parser.add_argument("--append", action="store_true", help="append to an existing JSONL output")
    args = parser.parse_args()

    if not 1 <= args.max_pages <= MAX_PAGES_LIMIT:
        parser.error(f"--max-pages must be between 1 and {MAX_PAGES_LIMIT}")
    if args.delay_seconds < 1.0:
        parser.error("--delay-seconds must be at least 1")
    if args.timeout <= 0 or args.max_text_chars <= 0:
        parser.error("--timeout and --max-text-chars must be positive")
    if args.output.resolve() == args.urls.resolve():
        parser.error("--output must not overwrite the URL list")
    if args.output.exists() and not args.append:
        parser.error("output already exists; choose another path or pass --append")

    try:
        args.allow_domain = [normalize_domain(item) for item in args.allow_domain]
        args.urls_to_fetch = read_urls(args.urls, args.max_pages)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return args


def main() -> int:
    args = parse_args()
    try:
        from scrapling.fetchers import FetcherSession
    except ImportError:
        print(
            "Scrapling Fetcher is unavailable. Install this skill's requirements.txt "
            "in the active Python environment.",
            file=sys.stderr,
        )
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.save_html_dir:
        args.save_html_dir.mkdir(parents=True, exist_ok=True)
    mode = "a" if args.append else "x"
    last_started: dict[str, float] = {}
    robots_cache: dict[str, tuple[RobotFileParser | None, int, str | None]] = {}
    stopped = False

    # Use plain HTTP headers, no browser impersonation, and one attempt per request.
    with args.output.open(mode, encoding="utf-8", newline="\n") as output:
        with FetcherSession(
            timeout=args.timeout,
            retries=1,
            follow_redirects="safe",
            max_redirects=5,
            impersonate=None,
            stealthy_headers=False,
            headers={"User-Agent": USER_AGENT + " (+https://github.com/starsman-lab/scrapling-research-capture)"},
        ) as session:
            for request_url, host in args.urls_to_fetch:
                safe_url = redact_url(request_url)
                record = {
                    "source_url": safe_url,
                    "retrieved_at_utc": utc_now(),
                    "status": None,
                    "title": None,
                    "author": None,
                    "published_at": None,
                    "site_name": None,
                    "canonical_url": None,
                    "text": None,
                    "text_truncated": False,
                    "html_file": None,
                    "error": None,
                }

                if not allowed_by_domain(host, args.allow_domain):
                    record["error"] = "outside_domain_allowlist"
                    write_record(output, record)
                    print(f"Skipped outside allowlist: {safe_url}", file=sys.stderr)
                    continue

                parsed = urlsplit(request_url)
                origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
                if origin not in robots_cache:
                    try:
                        robots_cache[origin] = get_robots_policy(
                            session, request_url, host, args.delay_seconds, last_started
                        )
                    except Exception as exc:  # Keep the output useful without echoing query strings.
                        robots_cache[origin] = (None, 0, f"robots_error:{type(exc).__name__}")
                policy, robots_status, robots_error = robots_cache[origin]
                record["robots_status"] = robots_status

                if robots_error:
                    record["error"] = robots_error
                    write_record(output, record)
                    print(f"Skipped; robots policy unavailable for {host}", file=sys.stderr)
                    continue
                if policy is not None and not policy.can_fetch(USER_AGENT, request_url):
                    record["error"] = "disallowed_by_robots_txt"
                    write_record(output, record)
                    print(f"Skipped by robots.txt: {safe_url}", file=sys.stderr)
                    continue

                site_delay = args.delay_seconds
                if policy is not None:
                    crawl_delay = policy.crawl_delay(USER_AGENT)
                    request_rate = policy.request_rate(USER_AGENT)
                    if crawl_delay:
                        site_delay = max(site_delay, float(crawl_delay))
                    if request_rate and request_rate.requests:
                        site_delay = max(site_delay, request_rate.seconds / request_rate.requests)

                try:
                    page = paced_get(session, request_url, host, site_delay, last_started)
                except Exception as exc:
                    record["error"] = f"fetch_error:{type(exc).__name__}"
                    write_record(output, record)
                    print(f"Fetch failed ({type(exc).__name__}): {safe_url}", file=sys.stderr)
                    continue

                status = int(page.status)
                record["status"] = status
                if status in STOP_STATUSES:
                    record["error"] = "access_denied_or_rate_limited; stopped_without_retry"
                    write_record(output, record)
                    print(f"Stopped after HTTP {status}: {safe_url}", file=sys.stderr)
                    stopped = True
                    break
                if not 200 <= status < 300:
                    record["error"] = "non_success_http_status"
                    write_record(output, record)
                    print(f"HTTP {status}: {safe_url}", file=sys.stderr)
                    continue

                try:
                    extracted = page_text(page)
                    title = text_value(page, "title::text")
                    metadata = {
                        "author": text_value(page, 'meta[name="author"]::attr(content)'),
                        "published_at": text_value(
                            page, 'meta[property="article:published_time"]::attr(content)'
                        ) or text_value(page, 'meta[name="date"]::attr(content)'),
                        "site_name": text_value(page, 'meta[property="og:site_name"]::attr(content)'),
                        "canonical_url": text_value(page, 'link[rel="canonical"]::attr(href)'),
                    }
                except Exception as exc:
                    record["error"] = f"parse_error:{type(exc).__name__}"
                    write_record(output, record)
                    print(f"Parse failed ({type(exc).__name__}): {safe_url}", file=sys.stderr)
                    continue
                combined = ((title or "") + "\n" + extracted[:10000]).lower()
                if any(marker in combined for marker in CHALLENGE_MARKERS):
                    record["error"] = "challenge_page; stopped_without_bypass"
                    write_record(output, record)
                    print(f"Stopped on a challenge page: {safe_url}", file=sys.stderr)
                    stopped = True
                    break

                record.update(
                    {
                        "title": title,
                        **metadata,
                        "text": extracted[: args.max_text_chars],
                        "text_truncated": len(extracted) > args.max_text_chars,
                    }
                )
                if args.save_html_dir:
                    digest = hashlib.sha256((request_url + record["retrieved_at_utc"]).encode()).hexdigest()[:16]
                    html_path = args.save_html_dir / f"{digest}.html"
                    html_path.write_bytes(bytes(page.body))
                    record["html_file"] = html_path.as_posix()

                write_record(output, record)
                print(f"Captured HTTP {status}: {safe_url}")

    if stopped:
        print("Capture stopped at the first access denial, rate limit, or challenge page.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

