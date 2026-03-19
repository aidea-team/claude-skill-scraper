# Scraper Generation Checklist

Follow these conventions when generating a scraper for a website.

## File structure
- Single file: `{sitename}_scraper.py`
- Companion docs: `reverse_engineered_api.md`, `{sitename}_scraper_docs.md`
- Output data: `{sitename}_data.json`
- All files go in `{sitename}_scraper/` directory

## Required components
1. Module docstring: what site, what data, what tech stack (auth, pagination, etc.)
2. Constants: BASE_URL, endpoint paths, known enums/slugs
3. Session management: `create_session()` if auth/CSRF needed, else direct `requests.get/post`
4. Data fetching: one function per endpoint, with timeout param
5. Pagination: if needed, loop with progress to stderr, `--page-size`/`--delay`/`--max-items` args
6. Data transformation: flatten/enrich as appropriate, as optional flag
7. Output: `{metadata: {source, scraped_at, total_scraped, ...}, data_key: [...]}`
8. CLI: argparse with `-o`, `--timeout`, `--dry-run` minimum; site-specific flags as needed
9. `main()`: numbered steps with progress messages to stderr

## CLI arguments — standard set
- `-o, --output` (path, default: `{sitename}_data.json`)
- `--timeout` (int seconds, default: 30)
- `--dry-run` (flag: verify connectivity and show counts, don't download data)
- `--max-items` (int, optional: limit for testing)

## CLI arguments — conditional (add only if applicable)
- `--page-size` / `--delay` (if paginated API)
- `--flatten` / `--no-enrich` (if nested data structures)
- `--include-{lookup}` (if reference/lookup tables exist)
- `--language` / `--location` / other filters (if site supports them)

## Output JSON contract
```json
{
  "metadata": {
    "source": "<url>",
    "scraped_at": "<ISO8601Z>",
    "total_scraped": 123
  },
  "<data_key>": [ ...items... ]
}
```
- `metadata` always present with at least `source`, `scraped_at`, `total_scraped`
- Add site-specific metadata fields as needed (filters applied, language, etc.)
- `<data_key>` is a descriptive name for the data (e.g. `delegated_acts`, `availability`)

## Code style
- Functional style (no classes unless complexity genuinely warrants it)
- Type hints on function signatures
- Comments only where logic is non-obvious
- Dependencies: only `requests` — no browser/selenium at runtime
- Progress messages to stderr (`print(..., file=sys.stderr)`)
- Data output to file (JSON), never to stdout mixed with progress
