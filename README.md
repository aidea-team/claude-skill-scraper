# aidea-tools

Claude Code plugin marketplace by aidea.

## Installation

```bash
# Add the marketplace
/plugin marketplace add aidea-team/claude-skill-scraper

# Install the scraper plugin
/plugin install scraper@aidea-tools
```

### Prerequisites

- Python 3.9+
- Dependencies are installed automatically at first run (no manual setup needed)

### Permissions

The `/scrape` skill runs bash commands (curl, python scripts), writes files, and installs packages in a venv. On first run Claude Code will ask you to approve each action.

For a smoother experience, you can allow the needed tools upfront by running this in Claude Code:

```
/allowed-tools Bash(pip install *) Bash(playwright install *) Bash(python3 *) Bash(curl *) Bash(mkdir *) Write Edit Read
```

Or, if you prefer to skip all permission prompts for a single session:

```bash
claude --dangerously-skip-permissions
```

## Plugins

### scraper

Reverse-engineer website APIs and generate reusable Python scrapers.

**Skill:** `/scrape <URL> [instructions]`

```
/scrape https://example.com/products
/scrape https://example.com/products only in-stock items, I need name + price
/scrape https://portal.example.com user: demo@test.com pass: demo123
```

Given a URL (and optional instructions), the skill will:
1. Capture network traffic to discover API endpoints
2. Visually inspect the page to understand displayed data
3. Test and document the APIs
4. Generate a self-contained Python scraper with CLI and JSON output
5. Test the scraper and iterate until it works

Each generated scraper depends only on `requests` — no browser needed at runtime.

See [plugins/web-scraper/README.md](plugins/web-scraper/README.md) for details.
