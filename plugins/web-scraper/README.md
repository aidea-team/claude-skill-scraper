# web-scraper plugin

Claude Code skill that reverse-engineers website APIs and generates reusable Python scrapers.

## What it does

The `/scrape` skill automates a 6-step workflow:

1. **Network reconnaissance** — captures all HTTP traffic from the target page using a headless browser, identifies data-carrying API endpoints
2. **Visual reconnaissance** — screenshots the page and extracts DOM structure to understand what data the site displays
3. **API analysis** — tests discovered endpoints with curl, maps pagination, filters, auth requirements
4. **API documentation** — writes `reverse_engineered_api.md` with full endpoint specs
5. **Scraper generation** — produces a self-contained Python script (only `requests` needed at runtime) with CLI, pagination, and standardized JSON output
6. **Test & iterate** — runs the scraper with `--dry-run` and `--max-items`, fixes issues, proposes full run

## Usage

```
/scrape <URL> [instructions]
```

Examples:
```
/scrape https://example.com/products
/scrape https://example.com/products only items in stock, I need name + price + SKU
/scrape https://portal.example.com/data user: demo@test.com pass: demo123
/scrape https://example.com/api focus on /v2/listings endpoint
```

The skill creates a `{sitename}_scraper/` directory containing:
- `{sitename}_scraper.py` — the scraper script
- `{sitename}_scraper_docs.md` — usage documentation
- `reverse_engineered_api.md` — API documentation
- `{sitename}_data.json` — scraped data (after full run)

## Prerequisites

- Python 3.9+
- Dependencies (playwright, requests) are installed automatically in a dedicated venv at first run

## Bundled tools

### netcapture.py
Network traffic capture tool. Emulates Chrome DevTools Network tab via Playwright.

```bash
python netcapture.py URL [--wait networkidle] [--timeout 45] [--curl-filter pattern]
                         [--filter-type fetch,xhr] [--api-summary] [--output-json path]
```

### pagecapture.py
Visual and DOM inspection tool. Captures screenshots, visible text, forms, tables, meta tags, and framework detection.

```bash
python pagecapture.py URL [--screenshot path] [--timeout 30] [--device "iPhone 13"]
```
