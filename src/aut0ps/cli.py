"""Command-line interface for the Aut0ps corpus."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from .article import build_article_brief, select_article_sources
from .config import SearchSeed, Settings, load_settings, render_search_query
from .fallback_client import FallbackClient, ResilientClient
from .firecrawl_client import FirecrawlClient, FirecrawlConfigurationError
from .pipeline import RunSummary, Aut0psPipeline
from .storage import Storage


def _summary_dict(summary: Any) -> dict:
    return {
        "discovered": summary.discovered,
        "fetched": summary.fetched,
        "stored": summary.stored,
        "skipped": summary.skipped,
        "failed": summary.failed,
        "errors": summary.errors,
    }


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/seeds.json"),
        help="JSON seed/config file (default: config/seeds.json)",
    )
    parser.add_argument("--db", type=Path, default=None, help="SQLite path override")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and query the Aut0ps web corpus")
    _add_common_arguments(parser)
    commands = parser.add_subparsers(dest="command", required=True)

    discover = commands.add_parser("discover", help="Search, scrape, and store relevant pages")
    discover.add_argument("queries", nargs="*", help="Search queries; defaults to config queries")
    discover.add_argument("--limit", type=int, default=None, help="Results per query")
    discover.add_argument("--min-score", type=float, default=None, help="Minimum relevance score")
    discover.add_argument(
        "--platform",
        choices=("all", "x", "twitter", "google", "web"),
        default="all",
        help="Configured platform filter; X terms are restricted to x.com/twitter.com",
    )
    discover.add_argument(
        "--provider",
        choices=("fallback", "firecrawl"),
        default="fallback",
        help="Search provider (default: no-credit fallback providers)",
    )

    scrape = commands.add_parser("scrape", help="Scrape and store one URL")
    scrape.add_argument("url")
    scrape.add_argument("--query", default="", help="Optional query associated with the page")
    scrape.add_argument("--min-score", type=float, default=None, help="Minimum relevance score")
    scrape.add_argument(
        "--provider", choices=("fallback", "firecrawl"), default="firecrawl"
    )

    crawl = commands.add_parser("crawl", help="Crawl and store pages from one site")
    crawl.add_argument("url")
    crawl.add_argument("--limit", type=int, default=None, help="Maximum pages")
    crawl.add_argument("--min-score", type=float, default=None, help="Minimum relevance score")
    crawl.add_argument(
        "--provider", choices=("fallback", "firecrawl"), default="firecrawl"
    )
    crawl.add_argument(
        "--include-path", action="append", default=[], help="Path pattern to include; repeatable"
    )
    crawl.add_argument(
        "--exclude-path", action="append", default=[], help="Path pattern to exclude; repeatable"
    )
    crawl.add_argument("--max-depth", type=int, default=None, help="Maximum link depth")

    map_command = commands.add_parser("map", help="List URLs discovered on a site")
    map_command.add_argument("url")
    map_command.add_argument("--search", default=None, help="Optional site-map search term")
    map_command.add_argument(
        "--provider", choices=("fallback", "firecrawl"), default="fallback"
    )

    search = commands.add_parser("search", help="Search the local SQLite FTS index")
    search.add_argument("query", nargs="*", help="Search terms")
    search.add_argument("--limit", type=int, default=20)

    export = commands.add_parser("export", help="Export indexed pages")
    export.add_argument("--query", default="", help="Optional FTS query")
    export.add_argument("--limit", type=int, default=1000)
    export.add_argument("--format", choices=("json", "csv"), default="json")
    export.add_argument("--output", type=Path, default=None, help="Output path; defaults to stdout")

    commands.add_parser("stats", help="Show local corpus statistics")

    article = commands.add_parser(
        "prepare-article",
        help="Create a citation-ready evidence brief for an article-writing agent",
    )
    article.add_argument("--topic", default="Aut0ps technology trends")
    article.add_argument(
        "--query",
        default="",
        help="Optional local FTS query used to focus the evidence",
    )
    article.add_argument("--limit", type=int, default=60, help="Evidence records to include")
    article.add_argument(
        "--excerpt-chars", type=int, default=1800, help="Maximum excerpt length per source"
    )
    article.add_argument("--output", type=Path, default=None, help="Markdown output path")

    queries = commands.add_parser(
        "queries", help="Print configured search terms for X, Google, or the open web"
    )
    queries.add_argument(
        "--platform",
        choices=("all", "x", "twitter", "google", "web"),
        default="all",
    )
    queries.add_argument("--format", choices=("text", "json"), default="text")

    crawl_sites = commands.add_parser(
        "crawl-sites", help="Crawl every enabled site in config/seeds.json"
    )
    crawl_sites.add_argument("--limit", type=int, default=None, help="Maximum pages per site")
    crawl_sites.add_argument(
        "--min-score", type=float, default=None, help="Minimum relevance score"
    )

    run_all = commands.add_parser(
        "run-all",
        help="Run all configured searches and enabled site crawls",
    )
    run_all.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Override results per search and maximum pages per site",
    )
    run_all.add_argument(
        "--min-score", type=float, default=None, help="Minimum relevance score"
    )

    smoke = commands.add_parser("smoke-test", help="Make one real Firecrawl scrape request")
    smoke.add_argument("--url", default="https://firecrawl.dev")

    return parser


def _settings(args: argparse.Namespace):
    return load_settings(args.config, db_override=args.db)


def _primary_client(settings: Settings) -> FirecrawlClient:
    return FirecrawlClient(
        api_key=settings.api_key,
        max_retries=settings.max_retries,
        backoff_seconds=settings.backoff_seconds,
        request_interval_seconds=settings.request_interval_seconds,
        operation_timeout_seconds=settings.operation_timeout_seconds,
    )


def _collection_client(settings: Settings) -> ResilientClient:
    primary = (
        _primary_client(settings)
        if settings.api_key and not settings.force_fallback
        else None
    )
    if not settings.fallback_enabled and primary is None:
        raise FirecrawlConfigurationError(
            "FIRECRAWL_API_KEY is not set and fallback collection is disabled."
        )
    feed_overrides = {
        site.url: site.rss_feeds
        for site in settings.crawl_sites
        if site.rss_feeds
    }
    fallback = FallbackClient(
        searxng_url=settings.searxng_url,
        timeout_seconds=settings.fallback_timeout_seconds,
        feed_overrides=feed_overrides,
    ) if settings.fallback_enabled else None
    return ResilientClient(primary=primary, fallback=fallback)


def _pipeline(args: argparse.Namespace):
    settings = _settings(args)
    client = _collection_client(settings)
    storage = Storage(settings.database_path)
    pipeline = Aut0psPipeline(
        client=client,
        storage=storage,
        terms=settings.terms,
        exclude_domains=settings.exclude_domains,
        include_domains=settings.include_domains,
        store_raw=settings.store_raw_json,
    )
    return settings, storage, pipeline


def _print_summary(summary: Any) -> None:
    print(json.dumps(_summary_dict(summary), indent=2))


def _write_run_manifest(
    settings: Settings, command: str, args: argparse.Namespace, summary: Any
) -> Path:
    """Persist an audit-friendly summary without including secrets."""

    runs_dir = settings.data_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    manifest_path = runs_dir / ("%s-%s.json" % (timestamp, command))
    safe_args = {
        key: str(value)
        for key, value in vars(args).items()
        if key not in {"api_key", "password", "token"}
    }
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "args": safe_args,
        "summary": _summary_dict(summary),
    }
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _prune_run_manifests(runs_dir, settings.max_run_manifests)
    return manifest_path


def _prune_run_manifests(runs_dir: Path, keep: int) -> None:
    """Keep the newest manifests so daily runs don't grow the directory forever."""

    if keep <= 0:
        return
    manifests = sorted(runs_dir.glob("*.json"), key=lambda path: path.name, reverse=True)
    for stale in manifests[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass


def _write_article_brief(
    settings: Settings,
    storage: Storage,
    topic: str,
    query: str = "",
    limit: int = 60,
    excerpt_chars: int = 1800,
    output: Optional[Path] = None,
) -> tuple[Path, int]:
    evidence_limit = max(1, limit)
    candidate_limit = max(evidence_limit * 3, evidence_limit)
    candidates = storage.search(query, limit=candidate_limit) if query else storage.list_pages(
        candidate_limit
    )
    rows = select_article_sources(candidates, limit=evidence_limit)
    if output is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        output = settings.data_dir / "runs" / (timestamp + "-article-brief.md")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        build_article_brief(rows, topic=topic, excerpt_chars=excerpt_chars),
        encoding="utf-8",
    )
    return output, len(rows)


def _selected_search_seeds(
    settings: Settings, queries: List[str], platform: str
) -> List[SearchSeed]:
    # "twitter" is an alias of "x"; seeds are stored canonically as "x".
    if platform == "twitter":
        platform = "x"
    if queries:
        include_domains = ["x.com", "twitter.com"] if platform == "x" else []
        return [
            SearchSeed(
                name=query,
                platform=("web" if platform == "all" else platform),
                query=render_search_query(query),
                include_domains=include_domains,
            )
            for query in queries
        ]
    if platform == "all":
        return list(settings.search_seeds)
    return [seed for seed in settings.search_seeds if seed.platform == platform]


def _configured_site_min_score(
    settings: Settings, site: Any, override: Optional[float]
) -> float:
    if override is not None:
        return override
    if site.min_relevance_score is not None:
        return site.min_relevance_score
    return settings.min_relevance_score


def _client_for_provider(client: ResilientClient, provider: str) -> Any:
    if provider == "firecrawl":
        return client
    fallback = getattr(client, "fallback", None)
    if fallback is None:
        raise FirecrawlConfigurationError(
            "Fallback collection is disabled; enable it to use provider='fallback'."
        )
    return fallback


def _pipeline_for_provider(
    pipeline: Aut0psPipeline, provider: str
) -> Aut0psPipeline:
    return pipeline.with_client(_client_for_provider(pipeline.client, provider))


def _crawl_configured_sites(
    settings: Settings,
    pipeline: Aut0psPipeline,
    limit: Optional[int] = None,
    min_score: Optional[float] = None,
    require_sites: bool = True,
) -> RunSummary:
    sites = [site for site in settings.crawl_sites if site.enabled]
    if not sites:
        if require_sites:
            raise ValueError(
                "No enabled crawl_sites configured. Add named sites to config/seeds.json."
            )
        return RunSummary()

    summary = RunSummary()
    for index, site in enumerate(sites, start=1):
        print(
            "[%d/%d] Crawling %s (%s, %s)"
            % (index, len(sites), site.name, site.mode, site.provider)
        )
        site_min_score = _configured_site_min_score(settings, site, min_score)
        site_pipeline = _pipeline_for_provider(pipeline, site.provider)
        if site.mode == "scrape":
            site_summary = site_pipeline.scrape(
                site.url,
                source=site.name,
                min_score=site_min_score,
            )
        else:
            site_summary = site_pipeline.crawl(
                site.url,
                limit=limit or site.limit or settings.max_crawl_pages,
                min_score=site_min_score,
                include_paths=site.include_paths,
                exclude_paths=site.exclude_paths,
                max_depth=site.max_depth,
                source=site.name,
            )
        summary.absorb(site_summary)
    return summary


def _run(args: argparse.Namespace) -> int:
    if args.command == "search":
        settings = _settings(args)
        with Storage(settings.database_path) as storage:
            rows = storage.search(" ".join(args.query), limit=args.limit)
            storage.export_json(rows)
        return 0

    if args.command == "export":
        settings = _settings(args)
        with Storage(settings.database_path) as storage:
            rows = (
                storage.search(args.query, limit=args.limit)
                if args.query
                else storage.list_pages(args.limit)
            )
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                with args.output.open("w", encoding="utf-8", newline="") as handle:
                    if args.format == "json":
                        storage.export_json(rows, handle)
                    else:
                        storage.export_csv(rows, handle)
                print("Exported %d records to %s" % (len(rows), args.output))
            elif args.format == "json":
                storage.export_json(rows)
            else:
                storage.export_csv(rows)
        return 0

    if args.command == "stats":
        settings = _settings(args)
        with Storage(settings.database_path) as storage:
            print(
                json.dumps(
                    {"database": str(settings.database_path), "pages": storage.count()},
                    indent=2,
                )
            )
        return 0

    if args.command == "prepare-article":
        settings = _settings(args)
        with Storage(settings.database_path) as storage:
            output, count = _write_article_brief(
                settings,
                storage,
                topic=args.topic,
                query=args.query,
                limit=args.limit,
                excerpt_chars=args.excerpt_chars,
                output=args.output,
            )
        print("Article brief: %s (%d evidence records)" % (output, count))
        return 0

    if args.command == "queries":
        settings = _settings(args)
        seeds = _selected_search_seeds(settings, [], args.platform)
        if args.format == "json":
            print(
                json.dumps(
                    [
                        {
                            "name": seed.name,
                            "platform": seed.platform,
                            "query": render_search_query(seed.query),
                            "include_domains": seed.include_domains,
                        }
                        for seed in seeds
                    ],
                    indent=2,
                )
            )
        else:
            for seed in seeds:
                print("[%s] %s" % (seed.platform, seed.name))
                print(render_search_query(seed.query))
                print()
        return 0

    if args.command == "map":
        settings = _settings(args)
        collection_client = _collection_client(settings)
        client = _client_for_provider(collection_client, args.provider)
        print(json.dumps(client.map(args.url, search=args.search), indent=2))
        return 0

    if args.command == "smoke-test":
        settings = _settings(args)
        client = _primary_client(settings)
        page = client.scrape(args.url)
        print(
            json.dumps(
                {
                    "url": page.url,
                    "title": page.metadata.get("title", ""),
                    "characters": len(page.markdown),
                }
            )
        )
        return 0

    settings, storage, pipeline = _pipeline(args)
    try:
        if args.command == "discover":
            queries = _selected_search_seeds(settings, args.queries, args.platform)
            if not queries:
                raise ValueError("No search terms match the selected platform")
            search_pipeline = _pipeline_for_provider(pipeline, args.provider)
            summary = search_pipeline.discover(
                queries,
                limit=args.limit or settings.max_search_results,
                min_score=(
                    settings.min_relevance_score
                    if args.min_score is None
                    else args.min_score
                ),
            )
        elif args.command == "crawl-sites":
            summary = _crawl_configured_sites(
                settings,
                pipeline,
                limit=args.limit,
                min_score=args.min_score,
            )
        elif args.command == "run-all":
            search_seeds = _selected_search_seeds(settings, [], "all")
            enabled_sites = [site for site in settings.crawl_sites if site.enabled]
            if not search_seeds and not enabled_sites:
                raise ValueError(
                    "No configured search_terms or enabled crawl_sites to run."
                )

            summary = RunSummary()
            if search_seeds:
                print("Discovering %d configured search terms" % len(search_seeds))
                search_pipeline = _pipeline_for_provider(pipeline, "fallback")
                search_summary = search_pipeline.discover(
                    search_seeds,
                    limit=args.limit or settings.max_search_results,
                    min_score=(
                        settings.min_relevance_score
                        if args.min_score is None
                        else args.min_score
                    ),
                )
                summary.absorb(search_summary)
            else:
                print("No configured search terms; skipping discovery")

            if enabled_sites:
                summary.absorb(
                    _crawl_configured_sites(
                        settings,
                        pipeline,
                        limit=args.limit,
                        min_score=args.min_score,
                        require_sites=False,
                    )
                )
            else:
                print("No enabled crawl sites; skipping site crawls")
        elif args.command == "scrape":
            operation_pipeline = _pipeline_for_provider(pipeline, args.provider)
            summary = operation_pipeline.scrape(
                args.url,
                query=args.query,
                min_score=(
                    settings.min_relevance_score
                    if args.min_score is None
                    else args.min_score
                ),
            )
        elif args.command == "crawl":
            operation_pipeline = _pipeline_for_provider(pipeline, args.provider)
            summary = operation_pipeline.crawl(
                args.url,
                limit=args.limit or settings.max_crawl_pages,
                min_score=(
                    settings.min_relevance_score
                    if args.min_score is None
                    else args.min_score
                ),
                include_paths=args.include_path,
                exclude_paths=args.exclude_path,
                max_depth=args.max_depth,
            )
        else:
            raise ValueError("Unsupported command: %s" % args.command)
        _print_summary(summary)
        manifest_path = _write_run_manifest(settings, args.command, args, summary)
        print("Run manifest: %s" % manifest_path)
        if args.command == "run-all":
            try:
                article_path, article_count = _write_article_brief(
                    settings,
                    storage,
                    topic="Aut0ps technology trends",
                )
                print(
                    "Article brief: %s (%d evidence records)"
                    % (article_path, article_count)
                )
            except Exception as exc:
                print("Warning: could not write article brief: %s" % exc, file=sys.stderr)
        fallback_uses = getattr(pipeline.client, "fallback_uses", {})
        if fallback_uses:
            providers = ", ".join(
                "%s=%d" % (operation, count)
                for operation, count in sorted(fallback_uses.items())
            )
            print("Fallback providers used: %s" % providers, file=sys.stderr)
        return _exit_code(summary)
    finally:
        storage.close()


def _exit_code(summary: RunSummary) -> int:
    """0 = clean, 3 = partial failures with stored results, 1 = nothing succeeded."""

    if not summary.failed:
        return 0
    if summary.stored:
        return 3
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except FirecrawlConfigurationError as exc:
        print("Configuration error: %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 1
