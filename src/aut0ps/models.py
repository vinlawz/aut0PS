"""Data models shared by the collection, normalization, and storage layers."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class SearchHit:
    """A lightweight result returned by Firecrawl search."""

    url: str
    title: str = ""
    description: str = ""
    markdown: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchedPage:
    """A page returned by Firecrawl scrape or crawl."""

    url: str
    markdown: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PageRecord:
    """The normalized Aut0ps record stored in SQLite."""

    url: str
    canonical_url: str
    title: str
    description: str
    author: str
    published_at: str
    source: str
    query: str
    markdown: str
    technologies: List[str]
    topics: List[str]
    relevance_score: float
    content_hash: str
    fetched_at: str
    status: str = "ok"
    error: str = ""
    raw_json: str = "{}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

