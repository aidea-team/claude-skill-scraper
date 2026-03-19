"""Network Capture — emulates Chrome DevTools Network tab via Playwright.

Captures all network requests/responses in headless Chromium, producing
HAR (Chrome DevTools compatible) and simplified JSON output.

Enhanced with --filter-type and --api-summary for automated API discovery.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlparse

from playwright.async_api import (
    Page,
    Request,
    Response,
    async_playwright,
)

# Resource types typically not relevant for API discovery
NOISE_TYPES = frozenset({
    "stylesheet", "image", "media", "font", "texttrack",
    "eventsource", "manifest", "signedexchange", "ping",
    "cspviolationreport", "preflight", "other",
})

# URL patterns for tracking/analytics to filter out
TRACKING_PATTERNS = (
    "google-analytics", "googletagmanager", "gtag", "ga.js",
    "analytics", "hotjar", "segment", "mixpanel", "facebook",
    "fbevents", "doubleclick", "adservice", "pagead",
    "sentry", "newrelic", "datadog", "clarity.ms",
)


@dataclass
class RequestRecord:
    """Container for a single network request."""

    url: str
    method: str
    resource_type: str
    start_time: float  # monotonic
    request_headers: dict[str, str] = field(default_factory=dict)
    post_data: str | None = None
    status: int | None = None
    status_text: str = ""
    response_headers: dict[str, str] = field(default_factory=dict)
    size: int = 0
    encoded_size: int = 0
    end_time: float | None = None
    failure: str | None = None
    is_redirect: bool = False
    redirect_chain: list[str] = field(default_factory=list)
    timing: dict | None = None


class NetworkCapture:
    """Orchestrates headless browser navigation and network capture."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.url = args.url if "://" in args.url else f"https://{args.url}"
        self.pending: dict[int, RequestRecord] = {}
        self.records: list[RequestRecord] = []
        self._request_refs: set[Request] = set()
        self._nav_start: float = 0.0

    def _on_request(self, request: Request) -> None:
        self._request_refs.add(request)
        record = RequestRecord(
            url=request.url,
            method=request.method,
            resource_type=request.resource_type,
            start_time=time.monotonic(),
            post_data=request.post_data,
        )
        self.pending[id(request)] = record

    async def _on_response(self, response: Response) -> None:
        request = response.request
        record = self.pending.get(id(request))
        if record is None:
            return
        record.status = response.status
        record.status_text = response.status_text
        record.request_headers = await request.all_headers()
        record.response_headers = {
            h["name"]: h["value"]
            for h in await response.headers_array()
        }

        if response.status in (301, 302, 303, 307, 308):
            record.is_redirect = True

    async def _on_request_finished(self, request: Request) -> None:
        record = self.pending.get(id(request))
        if record is None:
            return
        record.end_time = time.monotonic()

        try:
            sizes = await request.sizes()
            record.size = sizes.get("responseBodySize", 0)
            record.encoded_size = sizes.get("responseTransferSize", 0)
        except Exception:
            pass

        try:
            record.timing = await request.timing()  # type: ignore[assignment]
        except Exception:
            pass

        # Redirect intermediates stay in pending for chain building
        if record.is_redirect:
            return

        self.pending.pop(id(request), None)
        record.redirect_chain = self._build_redirect_chain(request)
        self.records.append(record)
        self._print_live(record)

    def _on_request_failed(self, request: Request) -> None:
        record = self.pending.pop(id(request), None)
        if record is None:
            return
        record.end_time = time.monotonic()
        record.failure = request.failure
        self.records.append(record)
        self._print_live(record)

    def _build_redirect_chain(self, request: Request) -> list[str]:
        chain: list[str] = []
        prev = request.redirected_from
        while prev is not None:
            rec = self.pending.pop(id(prev), None)
            if rec is not None:
                chain.append(f"{rec.url} → {rec.status}")
                rec.is_redirect = True
                self.records.append(rec)
            else:
                chain.append(prev.url)
            prev = prev.redirected_from
        chain.reverse()
        return chain

    @staticmethod
    def _format_size(num_bytes: int) -> str:
        if num_bytes < 1024:
            return f"{num_bytes} B"
        elif num_bytes < 1024 * 1024:
            return f"{num_bytes / 1024:.1f} KB"
        else:
            return f"{num_bytes / (1024 * 1024):.1f} MB"

    def _print_live(self, record: RequestRecord) -> None:
        elapsed_ms = (
            int((record.end_time - record.start_time) * 1000)
            if record.end_time
            else 0
        )
        size_str = self._format_size(record.size) if record.size else ""

        if record.failure:
            line = (
                f"  [{record.resource_type}] {record.method} {record.url} "
                f"→ FAILED: {record.failure}"
            )
        elif record.redirect_chain:
            chain_str = " → ".join(record.redirect_chain)
            line = (
                f"  [{record.resource_type}] {chain_str} "
                f"→ {record.url} → {record.status} "
                f"({size_str}, {elapsed_ms}ms)"
            )
        else:
            parts = [
                f"  [{record.resource_type}] {record.method} {record.url}",
                f"→ {record.status}",
            ]
            if size_str:
                parts.append(f"({size_str}, {elapsed_ms}ms)")
            else:
                parts.append(f"({elapsed_ms}ms)")
            line = " ".join(parts)

        print(line)

    def _get_terminal_records(self) -> list[RequestRecord]:
        return [r for r in self.records if not r.is_redirect]

    def _filter_records(
        self, records: list[RequestRecord]
    ) -> list[RequestRecord]:
        """Apply --filter-type filtering."""
        if not self.args.filter_type:
            return records
        allowed = {t.strip() for t in self.args.filter_type.split(",")}
        return [r for r in records if r.resource_type in allowed]

    @staticmethod
    def _is_tracking(url: str) -> bool:
        url_lower = url.lower()
        return any(p in url_lower for p in TRACKING_PATTERNS)

    def _print_summary(self) -> None:
        terminal = self._get_terminal_records()
        failed = [r for r in terminal if r.failure]

        # --- By resource type ---
        type_stats: dict[str, dict] = {}
        for r in terminal:
            s = type_stats.setdefault(
                r.resource_type, {"count": 0, "size": 0, "total_ms": 0.0}
            )
            s["count"] += 1
            s["size"] += r.size
            if r.end_time:
                s["total_ms"] += (r.end_time - r.start_time) * 1000

        # --- By domain ---
        domain_stats: dict[str, dict] = {}
        for r in terminal:
            domain = urlparse(r.url).netloc
            d = domain_stats.setdefault(domain, {"count": 0, "size": 0})
            d["count"] += 1
            d["size"] += r.size

        total_size = sum(r.size for r in terminal)
        page_load = (
            (terminal[-1].end_time - self._nav_start)
            if terminal and terminal[-1].end_time
            else 0
        )

        print("\n=== Network Summary ===\n")

        # Type table
        header = f"{'Resource Type':<18} {'Count':>5}  {'Size':>10}  {'Avg Time':>10}"
        print(header)
        print("─" * len(header))
        for rtype, s in sorted(type_stats.items(), key=lambda x: -x[1]["size"]):
            avg_ms = int(s["total_ms"] / s["count"]) if s["count"] else 0
            print(
                f"{rtype:<18} {s['count']:>5}  "
                f"{self._format_size(s['size']):>10}  "
                f"{avg_ms:>7} ms"
            )

        print()

        # Domain table
        header = f"{'Domain':<34} {'Count':>5}  {'Size':>10}"
        print(header)
        print("─" * len(header))
        for domain, d in sorted(domain_stats.items(), key=lambda x: -x[1]["size"]):
            print(
                f"{domain:<34} {d['count']:>5}  "
                f"{self._format_size(d['size']):>10}"
            )

        print()
        fail_str = f" ({len(failed)} failed)" if failed else ""
        print(
            f"Total: {len(terminal)} requests{fail_str} | "
            f"{self._format_size(total_size)} | "
            f"Page load: {page_load:.3f}s"
        )

    def _print_api_summary(self) -> None:
        """Print structured API summary for automated analysis."""
        terminal = self._get_terminal_records()
        # Only fetch/xhr, exclude tracking
        api_records = [
            r for r in terminal
            if r.resource_type in ("fetch", "xhr")
            and not r.failure
            and not self._is_tracking(r.url)
        ]

        if not api_records:
            print("\n=== API Summary ===\n")
            print("No API requests (fetch/xhr) detected.")
            return

        # Group by endpoint (method + path, ignoring query params)
        endpoints: dict[str, list[RequestRecord]] = defaultdict(list)
        for r in api_records:
            parsed = urlparse(r.url)
            key = f"{r.method} {parsed.scheme}://{parsed.netloc}{parsed.path}"
            endpoints[key].append(r)

        print("\n=== API Summary ===\n")
        print(f"Found {len(api_records)} API requests across {len(endpoints)} unique endpoints\n")

        # Sort by total response size (largest first — most likely to be data)
        sorted_endpoints = sorted(
            endpoints.items(),
            key=lambda x: sum(r.size for r in x[1]),
            reverse=True,
        )

        for key, records in sorted_endpoints:
            total_size = sum(r.size for r in records)
            statuses = sorted(set(r.status for r in records if r.status))
            sample = records[0]

            print(f"  {key}")
            print(f"    Calls: {len(records)} | Size: {self._format_size(total_size)} | Status: {statuses}")

            # Show query params from first request
            parsed = urlparse(sample.url)
            if parsed.query:
                print(f"    Query: {parsed.query}")

            # Show post data hint
            if sample.post_data:
                preview = sample.post_data[:200]
                if len(sample.post_data) > 200:
                    preview += "..."
                print(f"    Body: {preview}")

            # Show content-type from response
            ct = sample.response_headers.get("content-type", "")
            if ct:
                print(f"    Content-Type: {ct}")

            print()

    def _print_curls(self, url_filter: str) -> None:
        terminal = self._get_terminal_records()
        matched = [r for r in terminal if url_filter in r.url]
        if not matched:
            print(f"\nNo requests matching '{url_filter}'")
            return
        print(f"\n=== Curl Commands ({len(matched)} matching '{url_filter}') ===\n")

        # Detect if any matched request uses CSRF/session cookies
        needs_session = any(
            r.request_headers.get("x-xsrf-token")
            or r.request_headers.get("cookie")
            for r in matched
        )
        if needs_session:
            origin = self.url.split("#")[0].rstrip("/")
            print("# Session bootstrap (run once, reuse cookies for all calls below)")
            print(f"curl -s -c /tmp/_cookies.txt -o /dev/null {shlex.quote(origin)}")
            print("XSRF=$(grep XSRF-TOKEN /tmp/_cookies.txt | awk '{print $NF}')")
            print()

        for r in matched:
            print(self._to_curl(r, session_mode=needs_session))
            print()

    @staticmethod
    def _to_curl(record: RequestRecord, session_mode: bool = False) -> str:
        """Generate a curl command that reproduces the request."""
        skip_headers = {
            "accept-encoding", "connection", "host", "content-length",
            "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
            "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site",
        }
        if session_mode:
            skip_headers.update({"cookie", "x-xsrf-token"})

        parts = ["curl"]
        if record.method != "GET":
            parts.append(f"-X {record.method}")
        parts.append(shlex.quote(record.url))

        if session_mode:
            parts.append("-b /tmp/_cookies.txt")
            if record.request_headers.get("x-xsrf-token"):
                parts.append("-H 'x-xsrf-token: '\"$XSRF\"")

        for name, value in record.request_headers.items():
            if name.lower() in skip_headers:
                continue
            parts.append(f"-H {shlex.quote(f'{name}: {value}')}")
        if record.post_data:
            parts.append(f"-d {shlex.quote(record.post_data)}")
        return " \\\n  ".join(parts)

    def _export_json(self, path: str) -> None:
        terminal = self._get_terminal_records()
        filtered = self._filter_records(terminal)

        data = []
        for r in filtered:
            entry: dict = {
                "url": r.url,
                "method": r.method,
                "resourceType": r.resource_type,
                "status": r.status,
                "statusText": r.status_text,
                "size": r.size,
                "encodedSize": r.encoded_size,
                "elapsedMs": (
                    int((r.end_time - r.start_time) * 1000) if r.end_time else None
                ),
                "curl": self._to_curl(r),
            }
            if r.request_headers:
                entry["requestHeaders"] = r.request_headers
            if r.post_data:
                entry["postData"] = r.post_data
            if r.failure:
                entry["failure"] = r.failure
            if r.redirect_chain:
                entry["redirectChain"] = r.redirect_chain
            if r.response_headers:
                entry["responseHeaders"] = r.response_headers
            if r.timing:
                entry["timing"] = r.timing
            data.append(entry)

        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"\nJSON saved to {path} ({len(data)} requests)")

    async def run(self) -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                # Context options
                ctx_kwargs: dict = {
                    "record_har_path": self.args.output_har,
                    "record_har_content": "embed",
                }

                # Device emulation
                if self.args.device:
                    try:
                        device = p.devices[self.args.device]
                        ctx_kwargs.update(device)
                    except KeyError:
                        print(
                            f"Error: unknown device '{self.args.device}'. "
                            f"See: playwright.dev/python/docs/emulation#devices",
                            file=sys.stderr,
                        )
                        sys.exit(1)

                context = await browser.new_context(**ctx_kwargs)

                # Extra headers
                if self.args.headers:
                    headers = dict(parse_header(h) for h in self.args.headers)
                    await context.set_extra_http_headers(headers)

                # Cookies
                if self.args.cookie:
                    cookies = [parse_cookie(c, self.url) for c in self.args.cookie]
                    await context.add_cookies(cookies)

                page: Page = await context.new_page()

                # No-cache via CDP
                if self.args.no_cache:
                    cdp = await context.new_cdp_session(page)
                    await cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})

                # Block patterns
                if self.args.block:
                    for pattern in self.args.block:

                        async def _block_route(route, _pattern=pattern):
                            await route.abort()

                        await page.route(pattern, _block_route)

                # Register event listeners
                page.on("request", self._on_request)
                page.on("response", self._on_response)
                page.on("requestfinished", self._on_request_finished)
                page.on("requestfailed", self._on_request_failed)

                print(f"Navigating to {self.url} (wait={self.args.wait}) ...\n")
                self._nav_start = time.monotonic()

                try:
                    await page.goto(
                        self.url,
                        wait_until=self.args.wait,
                        timeout=self.args.timeout * 1000,
                    )
                except Exception as e:
                    print(f"\nNavigation error: {e}", file=sys.stderr)

                # Screenshot
                if self.args.screenshot:
                    await page.screenshot(path=self.args.screenshot, full_page=True)
                    print(f"\nScreenshot saved to {self.args.screenshot}")

                # Close context to flush HAR
                await context.close()
                print(f"\nHAR saved to {self.args.output_har}")

            finally:
                await browser.close()

            self._print_summary()

            if self.args.api_summary:
                self._print_api_summary()

            if self.args.curl_filter:
                self._print_curls(self.args.curl_filter)

            if self.args.output_json:
                self._export_json(self.args.output_json)


def parse_cookie(s: str, url: str) -> dict:
    """Parse 'name=value; domain=...; path=...' into a Playwright cookie dict."""
    parts = [p.strip() for p in s.split(";")]
    if "=" not in parts[0]:
        raise ValueError(f"Invalid cookie format: {s}")
    name, value = parts[0].split("=", 1)
    cookie: dict = {"name": name, "value": value, "url": url}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        key = key.strip().lower()
        if key == "domain":
            cookie["domain"] = val.strip()
            cookie.pop("url", None)
        elif key == "path":
            cookie["path"] = val.strip()
    return cookie


def parse_header(s: str) -> tuple[str, str]:
    """Parse 'Key: Value' into a (key, value) tuple."""
    if ":" not in s:
        raise ValueError(f"Invalid header format (expected 'Key: Value'): {s}")
    key, value = s.split(":", 1)
    return key.strip(), value.strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture network requests like Chrome DevTools Network tab."
    )
    parser.add_argument("url", help="URL to navigate to")
    parser.add_argument(
        "--wait",
        choices=["networkidle", "load", "domcontentloaded"],
        default="networkidle",
        help="Wait strategy (default: networkidle)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Navigation timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--output-har",
        default="network.har",
        help="HAR output path (default: network.har)",
    )
    parser.add_argument(
        "--output-json",
        default="network.json",
        help="JSON output path (default: network.json)",
    )
    parser.add_argument(
        "--filter-type",
        default="fetch,xhr",
        help=(
            "Comma-separated resource types to include in JSON output. "
            "Set to 'all' to include everything. (default: fetch,xhr)"
        ),
    )
    parser.add_argument(
        "--api-summary",
        action="store_true",
        default=True,
        help="Print structured API summary of fetch/xhr endpoints (default: on)",
    )
    parser.add_argument(
        "--no-api-summary",
        action="store_false",
        dest="api_summary",
        help="Disable the API summary output",
    )
    parser.add_argument(
        "--block",
        action="append",
        default=[],
        help="Glob pattern to block (repeatable)",
    )
    parser.add_argument(
        "--headers",
        action="append",
        default=[],
        help="Extra header as 'Key: Value' (repeatable)",
    )
    parser.add_argument(
        "--cookie",
        action="append",
        default=[],
        help="Cookie as 'name=value; domain=...' (repeatable)",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Device to emulate (e.g. 'iPhone 13')",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable browser cache via CDP",
    )
    parser.add_argument(
        "--screenshot",
        default=None,
        help="Save full-page screenshot to path",
    )
    parser.add_argument(
        "--curl-filter",
        default=None,
        help="Print curl commands for requests whose URL contains this substring",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    # Handle --filter-type all
    if args.filter_type == "all":
        args.filter_type = None
    capture = NetworkCapture(args)
    await capture.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        sys.exit(130)
