"""Collection orchestration for Aut0ps."""

from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Set, Union
from urllib.parse import urlsplit

from .config import SearchSeed, render_search_query
from .firecrawl_client import FirecrawlClient
from .models import FetchedPage, PageRecord
from .normalize import canonicalize_url, normalize_fetched_page
from .storage import Storage


def _strip_www(hostname: str) -> str:
    return hostname[4:] if hostname.startswith("www.") else hostname


def domain_matches(url: str, excluded_domains: Iterable[str]) -> bool:
    hostname = _strip_www((urlsplit(url).hostname or "").lower())
    for excluded in excluded_domains:
        excluded = _strip_www(excluded.lower().strip())
        if excluded and (hostname == excluded or hostname.endswith("." + excluded)):
            return True
    return False


@dataclass
class RunSummary:
    discovered: int = 0
    fetched: int = 0
    stored: int = 0
    skipped: int = 0
    failed: int = 0
    errors: List[str] = field(default_factory=list)

    def absorb(self, other: "RunSummary") -> None:
        self.discovered += other.discovered
        self.fetched += other.fetched
        self.stored += other.stored
        self.skipped += other.skipped
        self.failed += other.failed
        self.errors.extend(other.errors)


class Aut0psPipeline:
    def __init__(
        self,
        client: FirecrawlClient,
        storage: Storage,
        terms: Iterable[str],
        exclude_domains: Iterable[str] = (),
        include_domains: Iterable[str] = (),
        store_raw: bool = True,
    ) -> None:
        self.client = client
        self.storage = storage
        self.terms = list(terms)
        self.exclude_domains = list(exclude_domains)
        self.include_domains = list(include_domains)
        self.store_raw = store_raw

    def with_client(self, client: Any) -> "Aut0psPipeline":
        """Return a pipeline sharing storage and rules but using another provider."""

        return Aut0psPipeline(
            client=client,
            storage=self.storage,
            terms=self.terms,
            exclude_domains=self.exclude_domains,
            include_domains=self.include_domains,
            store_raw=self.store_raw,
        )

    def _accept(self, url: str) -> bool:
        return bool(url) and not domain_matches(url, self.exclude_domains)

    def ingest_page(
        self,
        page: FetchedPage,
        source: str,
        query: str = "",
        min_score: float = 0.0,
    ) -> Optional[PageRecord]:
        if not self._accept(page.url):
            return None
        record = normalize_fetched_page(
            page,
            source=source,
            query=query,
            terms=self.terms,
            include_raw=self.store_raw,
        )
        if not record.canonical_url or record.relevance_score < min_score:
            return None
        self.storage.upsert(record)
        return record

    def discover(
        self,
        queries: Iterable[Union[str, SearchSeed]],
        limit: int = 10,
        min_score: float = 0.0,
    ) -> RunSummary:
        summary = RunSummary()
        seen: Set[str] = set()
        for entry in queries:
            include_domains = self.include_domains
            if isinstance(entry, SearchSeed):
                query = render_search_query(entry.query)
                include_domains = entry.include_domains or self.include_domains
            else:
                query = render_search_query(str(entry))
            query = query.strip()
            if not query:
                continue
            try:
                hits = self.client.search(query, limit=limit, include_domains=include_domains)
            except Exception as exc:
                # One failing seed must not abort the remaining seeds.
                summary.failed += 1
                summary.errors.append("search '%s': %s" % (query, exc))
                continue
            summary.discovered += len(hits)
            with self.storage.bulk():
                for hit in hits:
                    key = canonicalize_url(hit.url)
                    if not key or key in seen or not self._accept(hit.url):
                        summary.skipped += 1
                        continue
                    seen.add(key)
                    try:
                        if hit.markdown:
                            page = FetchedPage(
                                url=hit.url,
                                markdown=hit.markdown,
                                metadata=hit.metadata,
                                raw=hit.raw,
                            )
                        else:
                            page = self.client.scrape(hit.url)
                        summary.fetched += 1
                        if self.ingest_page(
                            page, source="search", query=query, min_score=min_score
                        ):
                            summary.stored += 1
                        else:
                            summary.skipped += 1
                    except Exception as exc:
                        summary.failed += 1
                        summary.errors.append("%s: %s" % (hit.url, exc))
        return summary

    def scrape(
        self,
        url: str,
        source: str = "scrape",
        query: str = "",
        min_score: float = 0.0,
    ) -> RunSummary:
        summary = RunSummary()
        try:
            page = self.client.scrape(url)
            summary.fetched = 1
            if self.ingest_page(page, source=source, query=query, min_score=min_score):
                summary.stored = 1
            else:
                summary.skipped = 1
        except Exception as exc:
            summary.failed = 1
            summary.errors.append("%s: %s" % (url, exc))
        return summary

    def crawl(
        self,
        url: str,
        limit: int = 25,
        min_score: float = 0.0,
        include_paths: Iterable[str] = (),
        exclude_paths: Iterable[str] = (),
        max_depth: Optional[int] = None,
        source: str = "crawl",
    ) -> RunSummary:
        summary = RunSummary()
        try:
            pages = self.client.crawl(
                url,
                limit=limit,
                include_paths=include_paths,
                exclude_paths=exclude_paths,
                max_depth=max_depth,
            )
            summary.discovered = len(pages)
            with self.storage.bulk():
                for page in pages:
                    try:
                        summary.fetched += 1
                        if self.ingest_page(page, source=source, min_score=min_score):
                            summary.stored += 1
                        else:
                            summary.skipped += 1
                    except Exception as exc:
                        summary.failed += 1
                        summary.errors.append("%s: %s" % (page.url, exc))
        except Exception as exc:
            summary.failed = 1
            summary.errors.append("%s: %s" % (url, exc))
        return summary
