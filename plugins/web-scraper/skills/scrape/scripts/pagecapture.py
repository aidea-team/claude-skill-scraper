"""Page Capture — visual and DOM inspection of a web page via Playwright.

Captures screenshot, page title, visible text, DOM structure summary,
and meta tags. Useful for understanding what a site displays to users,
complementing network-level analysis from netcapture.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from playwright.async_api import Page, async_playwright


async def extract_page_data(page: Page) -> dict:
    """Extract structured data from the loaded page via JavaScript evaluation."""
    return await page.evaluate("""() => {
        // --- Visible text (trimmed, deduplicated lines) ---
        const bodyText = document.body ? document.body.innerText : '';
        const visibleText = bodyText
            .split('\\n')
            .map(l => l.trim())
            .filter(l => l.length > 0)
            .slice(0, 500)
            .join('\\n');

        // --- Forms ---
        const forms = Array.from(document.querySelectorAll('form')).map(f => ({
            action: f.action || null,
            method: (f.method || 'GET').toUpperCase(),
            id: f.id || null,
            fields: Array.from(f.querySelectorAll('input, select, textarea')).map(el => ({
                tag: el.tagName.toLowerCase(),
                type: el.type || null,
                name: el.name || null,
                id: el.id || null,
                placeholder: el.placeholder || null,
            })),
        }));

        // --- Tables ---
        const tables = Array.from(document.querySelectorAll('table')).map(t => {
            const headers = Array.from(t.querySelectorAll('th')).map(th => th.innerText.trim());
            const rowCount = t.querySelectorAll('tr').length;
            return {
                id: t.id || null,
                headers: headers.length > 0 ? headers : null,
                rows: rowCount,
            };
        });

        // --- Interactive elements ---
        const interactive = [];
        document.querySelectorAll('button, [role="button"], a[href*="api"], a[href*="download"]').forEach(el => {
            const text = (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 80);
            if (text) {
                interactive.push({
                    tag: el.tagName.toLowerCase(),
                    text: text,
                    href: el.href || null,
                });
            }
        });

        // --- Meta tags ---
        const getMeta = (name) => {
            const el = document.querySelector(`meta[name="${name}"], meta[property="${name}"]`);
            return el ? el.content : null;
        };

        // --- Framework detection ---
        const frameworkHints = [];
        if (document.querySelector('[ng-version], [_nghost], [_ngcontent]')) frameworkHints.push('angular');
        if (document.querySelector('[data-reactroot], [data-reactid]') || window.__REACT_DEVTOOLS_GLOBAL_HOOK__) frameworkHints.push('react');
        if (document.querySelector('[data-v-], [data-vue]') || window.__VUE__) frameworkHints.push('vue');
        if (document.querySelector('[data-svelte-h]') || document.querySelector('style[data-svelte]')) frameworkHints.push('svelte');
        if (window.__NEXT_DATA__) frameworkHints.push('nextjs');
        if (window.__NUXT__) frameworkHints.push('nuxtjs');
        if (document.querySelector('meta[name="generator"][content*="WordPress"]')) frameworkHints.push('wordpress');
        if (document.querySelector('script[src*="jquery"]') || window.jQuery) frameworkHints.push('jquery');

        return {
            visibleText: visibleText,
            domSummary: {
                forms: forms,
                tables: tables,
                linksCount: document.querySelectorAll('a[href]').length,
                interactiveElements: interactive.slice(0, 30),
            },
            meta: {
                description: getMeta('description'),
                ogTitle: getMeta('og:title'),
                ogDescription: getMeta('og:description'),
                ogImage: getMeta('og:image'),
                generator: getMeta('generator'),
                frameworkHints: frameworkHints,
            },
        };
    }""")


async def capture(args: argparse.Namespace) -> dict:
    """Run page capture and return structured result."""
    url = args.url if "://" in args.url else f"https://{args.url}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            ctx_kwargs: dict = {}
            if args.device:
                try:
                    device = p.devices[args.device]
                    ctx_kwargs.update(device)
                except KeyError:
                    print(
                        f"Error: unknown device '{args.device}'.",
                        file=sys.stderr,
                    )
                    sys.exit(1)

            context = await browser.new_context(**ctx_kwargs)
            page = await context.new_page()

            print(f"Loading {url} ...", file=sys.stderr)

            try:
                await page.goto(
                    url,
                    wait_until="networkidle",
                    timeout=args.timeout * 1000,
                )
            except Exception as e:
                print(f"Navigation warning: {e}", file=sys.stderr)

            # Get page title
            title = await page.title()

            # Take screenshot
            screenshot_path = args.screenshot
            await page.screenshot(path=screenshot_path, full_page=True)
            print(f"Screenshot saved to {screenshot_path}", file=sys.stderr)

            # Extract page data
            page_data = await extract_page_data(page)

            await context.close()
        finally:
            await browser.close()

    result = {
        "url": url,
        "title": title,
        "screenshot": screenshot_path,
        "visible_text": page_data["visibleText"],
        "dom_summary": page_data["domSummary"],
        "meta": page_data["meta"],
    }

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture visual and DOM information from a web page."
    )
    parser.add_argument("url", help="URL to inspect")
    parser.add_argument(
        "--screenshot",
        default="pagecapture.png",
        help="Screenshot output path (default: pagecapture.png)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Navigation timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Device to emulate (e.g. 'iPhone 13')",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Save JSON output to file (default: print to stdout)",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    result = await capture(args)

    output = json.dumps(result, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"JSON saved to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        sys.exit(130)
