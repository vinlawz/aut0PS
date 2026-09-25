"""URL canonicalization and lightweight, deterministic content enrichment."""

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import FetchedPage, PageRecord, SearchHit

TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
}


def canonicalize_url(url: str) -> str:
    """Return a stable URL suitable for deduplication."""

    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower().startswith("utm_") or key.lower() in TRACKING_PARAMETERS:
            continue
        query_items.append((key, value))
    query_items.sort()
    return urlunsplit((scheme, netloc, path, urlencode(query_items), ""))


def _safe_mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    for method_name in ("model_dump", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                result = method()
            except TypeError:
                result = method
            if isinstance(result, Mapping):
                return dict(result)
    return {}


def _first_value(mapping: Mapping[str, Any], keys: Sequence[str]) -> str:
    lowered = {str(key).lower(): value for key, value in mapping.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def extract_title(markdown: str, metadata: Mapping[str, Any]) -> str:
    title = _first_value(metadata, ("title", "og:title", "twitter:title"))
    if title:
        return title
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    for line in markdown.splitlines():
        if line.strip():
            return line.strip()[:240]
    return ""


def _matches(term: str, text: str) -> bool:
    term = term.strip().lower()
    if not term:
        return False
    if " " in term:
        return term in text
    return re.search(r"\b%s\b" % re.escape(term), text) is not None


def extract_terms(text: str, terms: Iterable[str]) -> List[str]:
    normalized = text.lower()
    return sorted({term.strip() for term in terms if _matches(term, normalized)}, key=str.lower)


def relevance_score(title: str, description: str, markdown: str, terms: Iterable[str]) -> float:
    """Give title and description matches more weight than body-only matches."""

    title_text = title.lower()
    description_text = description.lower()
    body_text = markdown[:30000].lower()
    score = 0.0
    for term in terms:
        if _matches(term, title_text):
            score += 0.35
        elif _matches(term, description_text):
            score += 0.2
        elif _matches(term, body_text):
            score += 0.08
    return round(min(score, 1.0), 4)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    if hasattr(value, "dict"):
        return _json_safe(value.dict())
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _record_from_parts(
    url: str,
    markdown: str,
    metadata: Mapping[str, Any],
    raw: Mapping[str, Any],
    source: str,
    query: str,
    terms: Iterable[str],
    include_raw: bool = True,
) -> PageRecord:
    metadata = dict(metadata)
    title = extract_title(markdown, metadata)
    description = _first_value(metadata, ("description", "og:description", "twitter:description"))
    author = _first_value(metadata, ("author", "article:author", "byline"))
    published_at = _first_value(
        metadata,
        (
            "publishedtime",
            "published_time",
            "publishedat",
            "published_at",
            "datepublished",
            "date_published",
            "date",
        ),
    )
    # Both lists come from the same configured terms: "technologies" are terms
    # matched anywhere in the content, "topics" only those in the headline fields.
    matched_terms = extract_terms(" ".join((title, description, markdown)), terms)
    topics = extract_terms(" ".join((title, description)), terms)
    raw_json = "{}"
    if include_raw:
        raw_json = json.dumps(_json_safe(raw), ensure_ascii=False, default=str)
    return PageRecord(
        url=url,
        canonical_url=canonicalize_url(url),
        title=title,
        description=description,
        author=author,
        published_at=published_at,
        source=source,
        query=query,
        markdown=markdown.strip(),
        technologies=matched_terms,
        topics=topics,
        relevance_score=relevance_score(title, description, markdown, terms),
        content_hash=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        fetched_at=datetime.now(timezone.utc).isoformat(),
        raw_json=raw_json,
    )


def normalize_fetched_page(
    page: FetchedPage,
    source: str,
    query: str,
    terms: Iterable[str],
    include_raw: bool = True,
) -> PageRecord:
    return _record_from_parts(
        page.url,
        page.markdown,
        page.metadata,
        page.raw,
        source,
        query,
        terms,
        include_raw=include_raw,
    )


def normalize_search_hit(
    hit: SearchHit,
    source: str,
    query: str,
    terms: Iterable[str],
    include_raw: bool = True,
) -> PageRecord:
    return _record_from_parts(
        hit.url,
        hit.markdown,
        hit.metadata,
        hit.raw,
        source,
        query,
        terms,
        include_raw=include_raw,
    )
