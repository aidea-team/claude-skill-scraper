---
name: scrape
description: "Reverse-engineers a website's API by capturing network traffic, analyzing endpoints, and generating a complete Python scraper with documentation. Use when the user wants to scrape data from a website, extract API endpoints, or build a reusable data collector."
user-invocable: true
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Agent
---

# Web Scraper Generator

You reverse-engineer a website's API and generate a complete, reusable Python scraper.

The user provides a URL and optionally specific instructions. You will:
1. Capture and analyze network traffic to find data APIs
2. Visually inspect the page to understand what data it shows
3. Test and document the discovered APIs
4. Generate a self-contained Python scraper
5. Test the scraper and iterate until it works

## Input

```
/scrape <URL> [instructions]
```

- **URL** (required): the target page to scrape.
- **Instructions** (optional): free-text notes that guide the scraper generation.

Examples:
```
/scrape https://example.com/products
/scrape https://example.com/products scrape only items with price > 0, ignore out-of-stock
/scrape https://portal.example.com/dashboard user: demo@test.com pass: demo123
/scrape https://example.com/api-docs focus on the /v2/listings endpoint, I need name + price + location
```

### Parsing

1. Extract the **URL** — the first token that looks like a URL (starts with `http://`, `https://`, or contains a `.` with no spaces).
2. Everything after the URL is the **user instructions** (`{instructions}`).
3. If no URL is provided, ask for one.

Derive `{sitename}` from the URL's hostname (e.g. `example.com` → `example`, `cowo.21houseofstories.com` → `cowo21house`). Use this as the naming prefix throughout.

### How to use instructions

The `{instructions}` inform your decisions throughout the entire workflow:

- **Step 2 (Network)**: if the user specifies endpoints or data types, prioritize those in your analysis.
- **Step 3 (Visual)**: if the user says what data they want, cross-reference it with what the page shows.
- **Step 4 (API Analysis)**: if credentials are provided, use them for session bootstrap. If the user asks for specific fields or filters, focus your exploration on those.
- **Step 5 (Generate)**: tailor the scraper to the user's needs — filter only the data they asked for, add CLI flags for their specific use case, include only the endpoints they care about.
- **Step 6 (Test)**: validate that the output matches what the user requested.

If the user provides **credentials** (username/password, API key, token), use them in the scraper's session management. Include them as CLI arguments (`--username`, `--password`, `--api-key`) with the provided values as defaults, so the scraper is reusable.

If no instructions are provided, scrape all available data from the discovered APIs.

---

## STEP 1 — Setup & Dependency Check

All Python commands run inside a dedicated venv at `~/.claude/venvs/web-scraper/`.
If the venv doesn't exist, create it and install dependencies automatically.

Define a helper variable for the venv Python and use it for every `python` call
in all subsequent steps:

```bash
VENV=~/.claude/venvs/web-scraper
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet playwright requests
  "$VENV/bin/playwright" install chromium
fi
PY="$VENV/bin/python3"
```

Verify it works:
```bash
$PY -c "import playwright; import requests; print('OK')"
```

Create the output directory:

```bash
mkdir -p {sitename}_scraper
```

**IMPORTANT:** Use `$PY` (i.e. `~/.claude/venvs/web-scraper/bin/python3`) for
every Python invocation in the steps below. Never use the system `python3`.

---

## STEP 2 — Network Reconnaissance

Capture all network traffic from the target page:

```bash
$PY ${CLAUDE_SKILL_DIR}/scripts/netcapture.py "$URL" \
  --wait networkidle \
  --timeout 45 \
  --output-json {sitename}_scraper/network.json \
  --output-har {sitename}_scraper/network.har
```

Read the JSON output and identify relevant API requests:

1. **Focus on fetch/xhr** — the JSON output is already filtered to these by default
2. **Ignore noise** — skip requests to tracking services (Google Analytics, Facebook Pixel, Hotjar, Sentry, etc.), font CDNs, image CDNs
3. **Rank by response size** — larger responses typically contain the actual data
4. **Note the API summary** — netcapture prints a structured summary of unique endpoints, methods, and sizes

For the most promising endpoints, generate curl commands:

```bash
$PY ${CLAUDE_SKILL_DIR}/scripts/netcapture.py "$URL" \
  --wait networkidle \
  --timeout 45 \
  --curl-filter "{api_path_pattern}" \
  --output-json /dev/null \
  --output-har /dev/null
```

---

## STEP 3 — Visual Reconnaissance

Capture what the page actually shows to users:

```bash
$PY ${CLAUDE_SKILL_DIR}/scripts/pagecapture.py "$URL" \
  --screenshot {sitename}_scraper/pagecapture.png \
  --output {sitename}_scraper/pagecapture.json
```

Read the output to understand:
- **What data is displayed** — tables, lists, cards, forms
- **Page structure** — forms (for search/filter UI), interactive elements
- **Framework** — Angular/React/Vue/WordPress hints help predict API patterns
- **Meta tags** — site description, generator info

Use the screenshot to visually confirm what the site shows. This helps connect
API data fields to the visible content (especially when field names are cryptic).

---

## STEP 4 — API Analysis

Now analyze the discovered APIs. This step is your reasoning — no script needed.

For each promising endpoint found in Step 2:

### 4a. Test with curl
Run the curl commands from Step 2 output. For each endpoint:
- Does it work without cookies/session? Try a clean curl first
- Does it need CSRF? (Check if X-XSRF-TOKEN header was present)
- Does it need cookies? (Check if Cookie header was essential)
- What does the response look like? (JSON structure, fields, types)

### 4b. Explore pagination
If the API returns a list:
- Is there a `count`/`total` field?
- Try adding `?page=2` or `?offset=100` or look for cursor params
- Try increasing page size (`?per_page=100`, `?rowsPerPage=500`, etc.)
- What's the maximum page size the API accepts?

### 4c. Explore filters
- What query parameters does the URL accept?
- Try removing optional params — does the API return all data?
- Are there enum values (from lookup endpoints or visible dropdowns)?

### 4d. Check rate limiting
- Do rapid sequential requests get blocked?
- Are there rate limit headers? (`X-RateLimit-*`, `Retry-After`)

### 4e. Document everything

Write `{sitename}_scraper/reverse_engineered_api.md` with:

```markdown
# {Site Name} — Reverse-Engineered API

## Base URL
{base_url}

## Authentication
{none / CSRF / cookie-based / token — explain bootstrap if needed}

## Endpoints

### {METHOD} {path}
- **Purpose**: {what it returns}
- **Auth required**: {yes/no}
- **Parameters**: {query/body params with types and defaults}
- **Pagination**: {type and params, or "not paginated"}
- **Response structure**: {key fields with types}
- **Example response** (truncated): {short JSON sample}

## Rate Limiting
{observed behavior}

## Notes
{anything unusual — CORS, required headers, session bootstrap, etc.}
```

---

## STEP 5 — Generate Scraper

Read the checklist at `${CLAUDE_SKILL_DIR}/templates/scraper_checklist.md` for conventions.

Write two files:

### 5a. `{sitename}_scraper/{sitename}_scraper.py`

The scraper must be:
- **Self-contained**: single Python file, only `requests` as dependency
- **Documented**: module docstring explaining the site, API, and auth mechanism
- **CLI-ready**: argparse with `-o`, `--timeout`, `--dry-run`, `--max-items` at minimum
- **Robust**: proper error handling, progress to stderr, clean JSON output
- **Polite**: configurable delay between paginated requests

Follow the patterns from the checklist. Key points:
- Constants section with BASE_URL and endpoint paths
- `create_session()` if CSRF/cookies needed
- One function per API endpoint
- Pagination loop with progress (if applicable)
- Standard output JSON format with metadata

### 5b. `{sitename}_scraper/{sitename}_scraper_docs.md`

Usage documentation:
- What the scraper does (one paragraph)
- Prerequisites (`pip install requests`)
- Usage examples (basic, with filters, dry-run, max-items)
- Output format (JSON structure with field descriptions)
- API details summary (from Step 4 findings)

---

## STEP 6 — Test, Fix & Run

### 6a. Dry run test
```bash
cd {sitename}_scraper && $PY {sitename}_scraper.py --dry-run
```

If it fails:
- Read the error carefully
- Fix the scraper code
- Re-run (loop until dry-run passes)

### 6b. Limited data test
```bash
$PY {sitename}_scraper.py --max-items 5
```

- Verify the output JSON is valid and matches the documented contract
- Check that fields are populated correctly
- Fix any issues and re-test

### 6c. Propose full run

Once tests pass, tell the user:
- How many total items the API reports
- Estimated time for full scrape (based on page size and delay)
- The exact command to run

**Wait for user confirmation before running the full scrape.**

### 6d. Full run (if user confirms)
```bash
$PY {sitename}_scraper.py
```

- Verify the output file was created
- Report: total items scraped, file size, any errors encountered
- If errors occur: diagnose, fix, and re-run
