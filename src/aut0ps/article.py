"""Build citation-ready evidence briefs for an article-writing agent."""

import json
from typing import Any, Dict, Iterable, List, Mapping
from urllib.parse import urlsplit


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _markdown_cell(value: Any) -> str:
    return _clean(value).replace("|", "\\|")


def _source_label(row: Mapping[str, Any]) -> str:
    source = _clean(row.get("source"))
    if source not in {"", "crawl", "search", "scrape", "configured"}:
        return source
    raw_json = row.get("raw_json", "")
    if raw_json:
        try:
            raw = json.loads(str(raw_json))
        except (TypeError, ValueError):
            raw = {}
        if isinstance(raw, Mapping) and _clean(raw.get("source_name")):
            return _clean(raw["source_name"])
    hostname = (urlsplit(_clean(row.get("url"))).hostname or "").lower()
    return hostname.lstrip("www.") or source or "unknown"


def select_article_sources(
    rows: Iterable[Mapping[str, Any]], limit: int = 60
) -> List[Dict[str, Any]]:
    """Select high-signal records while retaining source diversity."""

    candidates = [dict(row) for row in rows if _clean(row.get("url"))]
    candidates.sort(
        key=lambda row: (
            float(row.get("relevance_score") or 0.0),
            _clean(row.get("published_at")),
            _clean(row.get("fetched_at")),
        ),
        reverse=True,
    )
    limit = max(1, limit)
    selected: List[Dict[str, Any]] = []
    seen_urls = set()
    source_groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in candidates:
        source_groups.setdefault(_source_label(row), []).append(row)

    # Take one strong record from each source before filling with the best remaining records.
    source_names = sorted(source_groups)
    offset = 0
    max_group_size = max((len(group) for group in source_groups.values()), default=0)
    while len(selected) < limit and offset < max_group_size:
        for source in source_names:
            group = source_groups[source]
            if offset >= len(group):
                continue
            row = group[offset]
            key = _clean(row.get("canonical_url")) or _clean(row.get("url"))
            if key and key not in seen_urls:
                selected.append(row)
                seen_urls.add(key)
                if len(selected) >= limit:
                    break
        offset += 1
    return selected


def build_article_brief(
    rows: Iterable[Mapping[str, Any]],
    topic: str,
    excerpt_chars: int = 1800,
) -> str:
    """Create a markdown evidence packet that GitHub Copilot can turn into an article."""

    rows = list(rows)
    excerpt_chars = max(200, excerpt_chars)
    title = _clean(topic) or "Aut0ps technology trends"
    lines = [
        "# Agent brief: %s" % title,
        "",
        "## Assignment",
        "",
        "Write a detailed technical article about **%s** using only the evidence below." % title,
        "Synthesize developments across sources rather than producing a link-by-link list.",
        "Cite factual claims inline with markdown links to the original source URLs.",
        "Distinguish reported facts from analysis, identify disagreements, and do not invent facts",
        "that are not supported by a cited source. Mention publication dates when recency matters.",
        "",
        "Recommended structure: executive summary, key developments, technical implications,",
        "industry/business implications, regional implications where supported, open questions,",
        "and a source-backed conclusion.",
        "",
        "## Corpus coverage",
        "",
        "- Evidence records: %d" % len(rows),
        "- Sources represented: %d" % len({_source_label(row) for row in rows}),
        "",
        "## Evidence index",
        "",
        "| Source | Published | Relevance | Title | URL |",
        "|---|---|---:|---|---|",
    ]
    for row in rows:
        url = _clean(row.get("url"))
        title_value = _markdown_cell(row.get("title")) or url
        lines.append(
            "| %s | %s | %.3f | %s | [%s](%s) |"
            % (
                _markdown_cell(_source_label(row)),
                _markdown_cell(row.get("published_at")) or "unknown",
                float(row.get("relevance_score") or 0.0),
                title_value,
                _markdown_cell(_source_label(row)),
                url,
            )
        )

    lines.extend(["", "## Source extracts", ""])
    for index, row in enumerate(rows, start=1):
        url = _clean(row.get("url"))
        source = _source_label(row)
        title_value = _clean(row.get("title")) or url
        excerpt = _clean(row.get("markdown"))
        if len(excerpt) > excerpt_chars:
            excerpt = excerpt[:excerpt_chars].rstrip() + "…"
        lines.extend(
            [
                "### %d. %s" % (index, title_value),
                "",
                "- Source: %s" % source,
                "- Published: %s" % (_clean(row.get("published_at")) or "unknown"),
                "- Relevance score: %.3f" % float(row.get("relevance_score") or 0.0),
                "- URL: [%s](%s)" % (url, url),
                "",
                excerpt or "No extract was stored; use the source URL for verification.",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
