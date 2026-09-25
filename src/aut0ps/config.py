"""Configuration loading without requiring a dotenv dependency."""

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

DEFAULT_TERMS = [
    "technology",
    "software",
    "engineering",
    "developer",
    "programming",
    "artificial intelligence",
    "machine learning",
    "cloud",
    "cybersecurity",
    "data",
    "startup",
    "open source",
]


def render_search_query(query: str, start_date: Optional[str] = None) -> str:
    """Expand date placeholders used by platform search seeds."""

    today = date.today()
    resolved_start = start_date or os.getenv("AUT0PS_START_DATE")
    if resolved_start is None and "{start_date}" in query:
        # In unattended runs a missing start date silently narrows results to today.
        print(
            "Warning: {start_date} used but AUT0PS_START_DATE is not set; "
            "defaulting to today (%s)" % today.isoformat(),
            file=sys.stderr,
        )
    values = {
        "today": today.isoformat(),
        "year": str(today.year),
        "start_date": resolved_start or today.isoformat(),
    }
    rendered = query
    for key, value in values.items():
        rendered = rendered.replace("{%s}" % key, value)
    return rendered


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE entries, without overriding real environment values."""

    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        # Only remove one matched pair of surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def load_json_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("Config file must contain a JSON object")
    return value


@dataclass
class SearchSeed:
    """A named query intended for a specific discovery platform."""

    name: str
    platform: str
    query: str
    include_domains: List[str] = field(default_factory=list)


@dataclass
class CrawlSite:
    """A named website or section that can be crawled from the CLI."""

    name: str
    url: str
    enabled: bool = True
    mode: str = "crawl"
    provider: str = "fallback"
    limit: Optional[int] = None
    min_relevance_score: Optional[float] = None
    include_paths: List[str] = field(default_factory=list)
    exclude_paths: List[str] = field(default_factory=list)
    rss_feeds: List[str] = field(default_factory=list)
    max_depth: Optional[int] = None
    notes: str = ""


@dataclass
class Settings:
    """Runtime settings resolved from environment and the seed config."""

    api_key: Optional[str]
    database_path: Path
    data_dir: Path
    queries: List[str] = field(default_factory=list)
    search_seeds: List[SearchSeed] = field(default_factory=list)
    crawl_sites: List[CrawlSite] = field(default_factory=list)
    terms: List[str] = field(default_factory=lambda: list(DEFAULT_TERMS))
    include_domains: List[str] = field(default_factory=list)
    exclude_domains: List[str] = field(default_factory=list)
    max_search_results: int = 10
    max_crawl_pages: int = 25
    min_relevance_score: float = 0.0
    max_retries: int = 2
    backoff_seconds: float = 1.0
    request_interval_seconds: float = 0.25
    operation_timeout_seconds: float = 300.0
    max_run_manifests: int = 200
    store_raw_json: bool = True
    fallback_enabled: bool = True
    fallback_timeout_seconds: float = 20.0
    searxng_url: Optional[str] = None
    force_fallback: bool = False


def load_settings(config_path: Path, db_override: Optional[Path] = None) -> Settings:
    """Resolve settings and create no files until a command actually needs them."""

    load_dotenv(Path(".env"))
    config = load_json_config(config_path)
    configured_db = Path(
        str(config.get("database_path", os.getenv("AUT0PS_DB", "data/aut0ps.db")))
    )
    database_path = db_override or configured_db
    data_dir = Path(str(config.get("data_dir", "data")))

    def list_value(key: str, default: List[str]) -> List[str]:
        value = config.get(key, default)
        if not isinstance(value, list):
            raise ValueError("Config value '%s' must be a list" % key)
        return [str(item).strip() for item in value if str(item).strip()]

    def domain_list(value: Any, key: str) -> List[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("Config value '%s' must be a list" % key)
        return [str(item).strip() for item in value if str(item).strip()]

    def bool_value(value: Any, key: str) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}:
            return True
        if isinstance(value, str) and value.strip().lower() in {"0", "false", "no", "off"}:
            return False
        raise ValueError("Config value '%s' must be a boolean" % key)

    search_seeds: List[SearchSeed] = []
    configured_searches = config.get("search_terms", [])
    if not isinstance(configured_searches, list):
        raise ValueError("Config value 'search_terms' must be a list")
    platform_domains = {
        "x": ["x.com", "twitter.com"],
        "twitter": ["x.com", "twitter.com"],
    }
    for index, entry in enumerate(configured_searches):
        if not isinstance(entry, Mapping):
            raise ValueError("search_terms[%d] must be an object" % index)
        query = str(entry.get("query", "")).strip()
        if not query:
            raise ValueError("search_terms[%d] is missing query" % index)
        platform = str(entry.get("platform", "web")).strip().lower() or "web"
        if platform == "twitter":
            # Canonicalize the alias so platform filters behave consistently.
            platform = "x"
        include_domains = domain_list(entry.get("include_domains"), "search_terms.include_domains")
        if not include_domains:
            include_domains = list(platform_domains.get(platform, []))
        search_seeds.append(
            SearchSeed(
                name=str(entry.get("name", query)).strip() or query,
                platform=platform,
                query=query,
                include_domains=include_domains,
            )
        )

    queries = list_value("queries", [])
    if not search_seeds:
        search_seeds = [
            SearchSeed(
                name=query,
                platform="web",
                query=query,
                include_domains=[],
            )
            for query in queries
        ]

    crawl_sites: List[CrawlSite] = []
    configured_sites = config.get("crawl_sites", [])
    if not isinstance(configured_sites, list):
        raise ValueError("Config value 'crawl_sites' must be a list")
    for index, entry in enumerate(configured_sites):
        if isinstance(entry, str):
            entry = {"url": entry}
        if not isinstance(entry, Mapping):
            raise ValueError("crawl_sites[%d] must be an object or URL string" % index)
        url = str(entry.get("url", "")).strip()
        if not url:
            raise ValueError("crawl_sites[%d] is missing url" % index)
        mode = str(entry.get("mode", "crawl")).strip().lower() or "crawl"
        if mode not in {"crawl", "scrape"}:
            raise ValueError("crawl_sites[%d].mode must be 'crawl' or 'scrape'" % index)
        provider = str(entry.get("provider", "fallback")).strip().lower() or "fallback"
        if provider not in {"fallback", "firecrawl"}:
            raise ValueError(
                "crawl_sites[%d].provider must be 'fallback' or 'firecrawl'" % index
            )
        crawl_sites.append(
            CrawlSite(
                name=str(entry.get("name", url)).strip() or url,
                url=url,
                enabled=bool(entry.get("enabled", True)),
                mode=mode,
                provider=provider,
                limit=(int(entry["limit"]) if entry.get("limit") is not None else None),
                min_relevance_score=(
                    float(entry["min_relevance_score"])
                    if entry.get("min_relevance_score") is not None
                    else None
                ),
                include_paths=domain_list(
                    entry.get("include_paths"), "crawl_sites.include_paths"
                ),
                exclude_paths=domain_list(
                    entry.get("exclude_paths"), "crawl_sites.exclude_paths"
                ),
                rss_feeds=domain_list(entry.get("rss_feeds"), "crawl_sites.rss_feeds"),
                max_depth=(
                    int(entry["max_depth"]) if entry.get("max_depth") is not None else None
                ),
                notes=str(entry.get("notes", "")).strip(),
            )
        )

    return Settings(
        api_key=os.getenv("FIRECRAWL_API_KEY") or None,
        database_path=database_path,
        data_dir=data_dir,
        queries=queries,
        search_seeds=search_seeds,
        crawl_sites=crawl_sites,
        terms=list_value("terms", DEFAULT_TERMS),
        include_domains=list_value("include_domains", []),
        exclude_domains=list_value("exclude_domains", []),
        max_search_results=int(config.get("max_search_results", 10)),
        max_crawl_pages=int(config.get("max_crawl_pages", 25)),
        min_relevance_score=float(config.get("min_relevance_score", 0.0)),
        max_retries=int(config.get("max_retries", 2)),
        backoff_seconds=float(config.get("backoff_seconds", 1.0)),
        request_interval_seconds=float(config.get("request_interval_seconds", 0.25)),
        operation_timeout_seconds=float(config.get("operation_timeout_seconds", 300.0)),
        max_run_manifests=int(config.get("max_run_manifests", 200)),
        store_raw_json=bool(config.get("store_raw_json", True)),
        fallback_enabled=bool_value(
            os.getenv("AUT0PS_FALLBACK_ENABLED", config.get("fallback_enabled", True)),
            "fallback_enabled",
        ),
        fallback_timeout_seconds=float(
            os.getenv(
                "AUT0PS_FALLBACK_TIMEOUT_SECONDS",
                config.get("fallback_timeout_seconds", 20),
            )
        ),
        searxng_url=(
            str(os.getenv("AUT0PS_SEARXNG_URL", config.get("searxng_url", ""))).strip()
            or None
        ),
        force_fallback=bool_value(
            os.getenv("AUT0PS_FORCE_FALLBACK", config.get("force_fallback", False)),
            "force_fallback",
        ),
    )
