# aut0ps

aut0ps is a focused, searchable web corpus builder. It uses no-credit RSS/HTTP collection by
default and reserves the official Firecrawl Python SDK for sources that need browser-grade
crawling, then normalizes pages into a local SQLite database with full-text search.

## Quick start

Requirements: Python 3.9+ and `uv`. A Firecrawl API key is recommended, but collection commands
can use the no-credit fallbacks when the key is missing or Firecrawl is unavailable.

```bash
cp .env.example .env
# Add your FIRECRAWL_API_KEY to .env
uv sync
uv run python -m unittest discover -s tests -v
```

## Commands

```bash
uv run aut0ps run-all
uv run aut0ps discover --platform google --limit 10
uv run aut0ps scrape https://example.com
uv run aut0ps crawl-sites --limit 3
uv run aut0ps search "artificial intelligence"
uv run aut0ps stats
```

Edit [`config/seeds.json`](config/seeds.json) to configure search terms and crawl sites.
