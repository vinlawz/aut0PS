"""No-credit collection providers used when Firecrawl is unavailable.

The fallback client intentionally uses only Python's standard library. It can
read RSS/Atom feeds, query a self-hosted SearXNG instance, and extract a small
amount of readable text and links from ordinary HTML pages.
"""

import gzip
import html
import json
import re
from html.parser import HTMLParser
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urljoin, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .models import FetchedPage, SearchHit

HttpResponse = Tuple[bytes, str, str]
HttpFetcher = Callable[[str], HttpResponse]


def _local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1].lower()


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


class _TextParser(HTMLParser):
    """Small HTML-to-text parser suitable for feeds and article fallbacks."""

    BLOCK_TAGS = {
        "address",
        "article",
        "blockquote",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "p",
        "pre",
        "section",
        "tr",
    }
    IGNORED_TAGS = {"aside", "form", "footer", "nav", "script", "style", "svg", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.links: List[str] = []
        self.feed_links: List[str] = []
        self.metadata: Dict[str, str] = {}
        self._ignored_depth = 0
        self._title_depth = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        href = attributes.get("href", "").strip()
        if tag == "a" and href:
            self.links.append(href)
        if tag == "link" and href:
            rel = attributes.get("rel", "").lower()
            content_type = attributes.get("type", "").lower()
            if "alternate" in rel and (
                "rss" in content_type or "atom" in content_type or "xml" in content_type
            ):
                self.feed_links.append(href)
        if tag == "meta":
            key = attributes.get("property") or attributes.get("name")
            value = attributes.get("content", "").strip()
            if key and value:
                self.metadata[key.lower()] = value
        if tag == "time":
            datetime_value = attributes.get("datetime", "").strip()
            if datetime_value:
                self.metadata.setdefault("published_at", datetime_value)
        if tag == "title":
            self._title_depth += 1
        if tag in self.IGNORED_TAGS:
            self._ignored_depth += 1
        if self._ignored_depth == 0 and tag in self.BLOCK_TAGS:
            self.parts.append("\n")
        if self._ignored_depth == 0 and tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag in self.IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1
        if self._ignored_depth == 0 and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        text = _clean_text(data)
        if text:
            self.parts.append(text)

    @property
    def title(self) -> str:
        return self.metadata.get("title", "")

    @property
    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", " ".join(self.parts)).strip()


def _html_to_text(value: str) -> str:
    parser = _TextParser()
    parser.feed(value or "")
    return parser.text


def _first_child_text(element: ElementTree.Element, names: Sequence[str]) -> str:
    wanted = {name.lower() for name in names}
    for child in list(element):
        if _local_name(child.tag) in wanted:
            return _clean_text("".join(child.itertext()))
    return ""


def _feed_item_url(element: ElementTree.Element) -> str:
    for child in list(element):
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href", "").strip()
        rel = child.attrib.get("rel", "alternate").lower()
        if href and rel in {"alternate", ""}:
            return href
        if href and not rel:
            return href
        text = _clean_text("".join(child.itertext()))
        if text:
            return text
    return _first_child_text(element, ("guid", "id"))


def _parse_feed(body: bytes, feed_url: str, limit: int) -> List[SearchHit]:
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return []
    items = [element for element in root.iter() if _local_name(element.tag) in {"item", "entry"}]
    hits: List[SearchHit] = []
    for item in items[: max(0, limit)]:
        url = _feed_item_url(item)
        title = _first_child_text(item, ("title",))
        description_html = _first_child_text(
            item, ("description", "summary", "content", "encoded")
        )
        description = _html_to_text(description_html)
        published_at = _first_child_text(
            item, ("pubdate", "published", "updated", "date", "datepublished")
        )
        author = _first_child_text(item, ("author", "creator", "name"))
        source_name = _first_child_text(item, ("source",))
        if not url or not title:
            continue
        hits.append(
            SearchHit(
                url=url,
                title=title,
                description=description,
                markdown=("# %s\n\n%s" % (title, description)).strip(),
                metadata={
                    "sourceURL": url,
                    "title": title,
                    "description": description,
                    "published_at": published_at,
                    "author": author,
                    "source_name": source_name,
                    "feed_url": feed_url,
                },
                raw={
                    "provider": "rss",
                    "feed_url": feed_url,
                    "source_name": source_name,
                },
            )
        )
    return hits


def _matches_path(url: str, patterns: Iterable[str]) -> bool:
    patterns = [pattern.strip() for pattern in patterns if pattern and pattern.strip()]
    if not patterns:
        return True
    path = urlsplit(url).path or "/"
    for pattern in patterns:
        regex = re.escape(pattern).replace(r"\*", ".*")
        if re.fullmatch(regex, path) or re.match(regex + r"$", path):
            return True
    return False


def _same_site(url: str, root_url: str) -> bool:
    hostname = (urlsplit(url).hostname or "").lower().lstrip("www.")
    root_hostname = (urlsplit(root_url).hostname or "").lower().lstrip("www.")
    return bool(hostname and root_hostname and hostname == root_hostname)


class OpenWebClient:
    """RSS/Atom and direct HTTP/HTML fallback implementation."""

    COMMON_FEED_PATHS = (
        "/feed/",
        "/feed.xml",
        "/rss/",
        "/rss.xml",
        "/atom.xml",
        "/index.xml",
        "/rss/index.xml",
    )

    def __init__(
        self,
        timeout_seconds: float = 20.0,
        feed_overrides: Optional[Mapping[str, Iterable[str]]] = None,
        http_fetcher: Optional[HttpFetcher] = None,
    ) -> None:
        self.timeout_seconds = max(1.0, timeout_seconds)
        self.feed_overrides = {
            key.rstrip("/"): list(value)
            for key, value in (feed_overrides or {}).items()
            if value
        }
        self._http_fetcher = http_fetcher or self._fetch_http

    def _fetch_http(self, url: str) -> HttpResponse:
        request = Request(
            url,
            headers={
                "Accept": (
                    "text/html,application/xhtml+xml,application/rss+xml,"
                    "application/atom+xml,application/xml;q=0.9,*/*;q=0.5"
                ),
                "Accept-Encoding": "gzip",
                "User-Agent": "Aut0ps/0.1 (+https://github.com/aut0ps)",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read(4 * 1024 * 1024)
                content_type = response.headers.get("Content-Type", "")
                final_url = response.geturl()
                if "gzip" in response.headers.get("Content-Encoding", "").lower():
                    body = gzip.decompress(body)
                return body, content_type, final_url
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("HTTP fallback failed for %s: %s" % (url, exc)) from exc

    def _fetch(self, url: str) -> HttpResponse:
        return self._http_fetcher(url)

    def _parse_html(self, url: str, body: bytes) -> Tuple[_TextParser, str]:
        decoded = body.decode("utf-8", errors="replace")
        parser = _TextParser()
        parser.feed(decoded)
        final_url = url
        metadata = parser.metadata
        title_match = re.search(r"<title[^>]*>(.*?)</title>", decoded, re.I | re.S)
        if title_match:
            metadata.setdefault("title", _clean_text(title_match.group(1)))
        metadata.setdefault("sourceURL", final_url)
        return parser, decoded

    def scrape(self, url: str) -> FetchedPage:
        body, content_type, final_url = self._fetch(url)
        if "xml" in content_type.lower() or body.lstrip().startswith((b"<?xml", b"<rss", b"<feed")):
            hits = _parse_feed(body, final_url, limit=1)
            if hits:
                hit = hits[0]
                return FetchedPage(
                    url=hit.url,
                    markdown=hit.markdown,
                    metadata=hit.metadata,
                    raw=hit.raw,
                )
        parser, _ = self._parse_html(final_url, body)
        metadata = dict(parser.metadata)
        metadata.setdefault("title", parser.title)
        metadata.setdefault("sourceURL", final_url)
        return FetchedPage(
            url=final_url,
            markdown=parser.text,
            metadata=metadata,
            raw={
                "provider": "http",
                "content_type": content_type,
                "links": parser.links,
                "feed_links": parser.feed_links,
            },
        )

    def map(self, url: str, search: Optional[str] = None) -> List[str]:
        page = self.scrape(url)
        links = page.raw.get("links", []) if isinstance(page.raw, Mapping) else []
        output = []
        for link in links:
            absolute = urljoin(url, str(link))
            if not absolute.startswith(("http://", "https://")):
                continue
            if search and search.lower() not in absolute.lower():
                continue
            if absolute not in output:
                output.append(absolute)
        return output

    def _feed_urls(self, url: str, page: Optional[FetchedPage]) -> List[str]:
        urls = list(self.feed_overrides.get(url.rstrip("/"), []))
        if page and isinstance(page.raw, Mapping):
            urls.extend(str(item) for item in page.raw.get("feed_links", []) if str(item).strip())
        parts = urlsplit(url)
        origin = "%s://%s" % (parts.scheme or "https", parts.netloc)
        for path in self.COMMON_FEED_PATHS:
            urls.append(origin + path)
        output: List[str] = []
        for feed_url in urls:
            absolute = urljoin(url, feed_url)
            if absolute not in output:
                output.append(absolute)
        return output

    def _rss_pages(
        self,
        url: str,
        page: Optional[FetchedPage],
        limit: int,
        include_paths: Iterable[str],
        exclude_paths: Iterable[str],
    ) -> List[FetchedPage]:
        for feed_url in self._feed_urls(url, page):
            try:
                body, _, final_url = self._fetch(feed_url)
                hits = _parse_feed(body, final_url, limit=max(limit * 2, limit))
            except (RuntimeError, ValueError):
                continue
            pages = []
            for hit in hits:
                hit.url = urljoin(feed_url, hit.url)
                if not _same_site(hit.url, url):
                    continue
                if not _matches_path(hit.url, include_paths):
                    continue
                if exclude_paths and _matches_path(hit.url, exclude_paths):
                    continue
                pages.append(
                    FetchedPage(
                        url=hit.url,
                        markdown=hit.markdown,
                        metadata=hit.metadata,
                        raw=hit.raw,
                    )
                )
                if len(pages) >= limit:
                    return pages
            if pages:
                return pages
        return []

    def rss_page(self, url: str) -> Optional[FetchedPage]:
        """Return the newest feed item for a URL when the page itself is blocked."""

        for feed_url in self._feed_urls(url, None):
            try:
                body, _, final_url = self._fetch(feed_url)
                hits = _parse_feed(body, final_url, limit=1)
            except (RuntimeError, ValueError):
                continue
            if not hits:
                continue
            hit = hits[0]
            hit.url = urljoin(feed_url, hit.url)
            if not _same_site(hit.url, url):
                continue
            return FetchedPage(
                url=hit.url,
                markdown=hit.markdown,
                metadata=hit.metadata,
                raw=hit.raw,
            )
        return None

    def crawl(
        self,
        url: str,
        limit: int = 25,
        include_paths: Iterable[str] = (),
        exclude_paths: Iterable[str] = (),
        max_depth: Optional[int] = None,
    ) -> List[FetchedPage]:
        limit = max(1, limit)
        include_paths = list(include_paths)
        exclude_paths = list(exclude_paths)
        try:
            page = self.scrape(url)
        except RuntimeError:
            # A blocked or unavailable landing page should not prevent a
            # configured feed (or a common feed URL) from being tried.
            page = None
        feed_pages = self._rss_pages(url, page, limit, include_paths, exclude_paths)
        if feed_pages:
            return feed_pages
        if page is None:
            return []

        # Fall back to a bounded breadth-first crawl of same-host links.
        depth_limit = 1 if max_depth is None else max(0, max_depth)
        queue: List[Tuple[str, int]] = [(url, 0)]
        seen: Set[str] = set()
        pages: List[FetchedPage] = []
        while queue and len(pages) < limit:
            current_url, depth = queue.pop(0)
            if current_url in seen or not _same_site(current_url, url):
                continue
            seen.add(current_url)
            try:
                current_page = page if current_url == url else self.scrape(current_url)
            except RuntimeError:
                continue
            if _matches_path(current_url, include_paths) and not (
                exclude_paths and _matches_path(current_url, exclude_paths)
            ):
                pages.append(current_page)
            if depth >= depth_limit:
                continue
            raw_links = current_page.raw.get("links", [])
            for link in raw_links if isinstance(raw_links, list) else []:
                next_url = urljoin(current_url, str(link)).split("#", 1)[0]
                if (
                    next_url.startswith(("http://", "https://"))
                    and _same_site(next_url, url)
                    and next_url not in seen
                ):
                    queue.append((next_url, depth + 1))
        return pages


class FallbackClient:
    """Search and collection fallback providers with no Firecrawl dependency."""

    def __init__(
        self,
        searxng_url: Optional[str] = None,
        timeout_seconds: float = 20.0,
        feed_overrides: Optional[Mapping[str, Iterable[str]]] = None,
        http_fetcher: Optional[HttpFetcher] = None,
    ) -> None:
        self.searxng_url = (searxng_url or "").strip().rstrip("/") or None
        self.web = OpenWebClient(
            timeout_seconds=timeout_seconds,
            feed_overrides=feed_overrides,
            http_fetcher=http_fetcher,
        )
        self._http_fetcher = self.web._http_fetcher

    @staticmethod
    def _query_with_domains(query: str, include_domains: Iterable[str]) -> str:
        domains = [str(domain).strip() for domain in include_domains if str(domain).strip()]
        if not domains:
            return query
        return "%s (%s)" % (query, " OR ".join("site:%s" % domain for domain in domains))

    def _search_searxng(
        self, query: str, limit: int, include_domains: Iterable[str]
    ) -> List[SearchHit]:
        if not self.searxng_url:
            return []
        search_url = "%s/search?q=%s&format=json&language=en&categories=general" % (
            self.searxng_url,
            quote_plus(self._query_with_domains(query, include_domains)),
        )
        body, _, _ = self._http_fetcher(search_url)
        payload = json.loads(body.decode("utf-8", errors="replace"))
        results = payload.get("results", []) if isinstance(payload, Mapping) else []
        hits = []
        for result in results[: max(0, limit)]:
            if not isinstance(result, Mapping) or not str(result.get("url", "")).strip():
                continue
            title = str(result.get("title", ""))
            description = _clean_text(str(result.get("content", result.get("description", ""))))
            hits.append(
                SearchHit(
                    url=str(result["url"]),
                    title=title,
                    description=description,
                    markdown=("# %s\n\n%s" % (title, description)).strip(),
                    metadata={
                        "sourceURL": str(result["url"]),
                        "title": title,
                        "description": description,
                    },
                    raw={"provider": "searxng", "result": dict(result)},
                )
            )
        return hits

    def _search_google_news(
        self, query: str, limit: int, include_domains: Iterable[str]
    ) -> List[SearchHit]:
        search_query = self._query_with_domains(query, include_domains)
        feed_url = (
            "https://news.google.com/rss/search?q=%s&hl=en-US&gl=US&ceid=US:en"
            % quote_plus(search_query)
        )
        body, _, final_url = self._http_fetcher(feed_url)
        return _parse_feed(body, final_url, limit=limit)

    def search(
        self,
        query: str,
        limit: int = 10,
        include_domains: Optional[Iterable[str]] = None,
    ) -> List[SearchHit]:
        if self.searxng_url:
            try:
                hits = self._search_searxng(query, limit, include_domains or [])
                if hits:
                    return hits
            except (HTTPError, URLError, RuntimeError, ValueError, KeyError, TypeError):
                pass
        return self._search_google_news(query, limit, include_domains or [])

    def scrape(self, url: str) -> FetchedPage:
        try:
            return self.web.scrape(url)
        except RuntimeError:
            feed_page = self.web.rss_page(url)
            if feed_page:
                return feed_page
            raise

    def map(self, url: str, search: Optional[str] = None) -> List[str]:
        return self.web.map(url, search=search)

    def crawl(
        self,
        url: str,
        limit: int = 25,
        include_paths: Iterable[str] = (),
        exclude_paths: Iterable[str] = (),
        max_depth: Optional[int] = None,
    ) -> List[FetchedPage]:
        return self.web.crawl(
            url,
            limit=limit,
            include_paths=include_paths,
            exclude_paths=exclude_paths,
            max_depth=max_depth,
        )


class ResilientClient:
    """Use Firecrawl first and switch to no-credit providers on failure."""

    def __init__(self, primary: Any = None, fallback: Any = None) -> None:
        self.primary = primary
        self.fallback = fallback
        self.fallback_uses: Dict[str, int] = {}
        self.primary_disabled = primary is None
        self.primary_disable_reason = (
            "Firecrawl API key is not configured" if primary is None else ""
        )

    @staticmethod
    def _is_budget_error(error: Exception) -> bool:
        message = str(error).lower()
        status_code = getattr(error, "status_code", None)
        if str(status_code) in {"402", "429"}:
            return True
        if re.search(r"\b(?:402|429)\b", message):
            return True
        return any(
            marker in message
            for marker in (
                "credit",
                "quota",
                "rate limit",
                "too many requests",
                "insufficient",
                "payment required",
            )
        )

    def _call(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        if not self.primary_disabled and self.primary is not None:
            try:
                return getattr(self.primary, operation)(*args, **kwargs)
            except Exception as exc:
                if self._is_budget_error(exc):
                    self.primary_disabled = True
                    self.primary_disable_reason = str(exc)
                if self.fallback is None:
                    raise
        if self.fallback is None:
            raise RuntimeError("No Firecrawl or fallback provider is configured")
        self.fallback_uses[operation] = self.fallback_uses.get(operation, 0) + 1
        try:
            return getattr(self.fallback, operation)(*args, **kwargs)
        except Exception as fallback_exc:
            reason = self.primary_disable_reason or "Firecrawl request failed"
            raise RuntimeError(
                "%s; fallback %s also failed: %s" % (reason, operation, fallback_exc)
            ) from fallback_exc

    def search(
        self,
        query: str,
        limit: int = 10,
        include_domains: Optional[Iterable[str]] = None,
    ) -> List[SearchHit]:
        return self._call(
            "search", query, limit=limit, include_domains=include_domains
        )

    def scrape(self, url: str) -> FetchedPage:
        return self._call("scrape", url)

    def map(self, url: str, search: Optional[str] = None) -> List[str]:
        return self._call("map", url, search=search)

    def crawl(
        self,
        url: str,
        limit: int = 25,
        include_paths: Iterable[str] = (),
        exclude_paths: Iterable[str] = (),
        max_depth: Optional[int] = None,
    ) -> List[FetchedPage]:
        return self._call(
            "crawl",
            url,
            limit=limit,
            include_paths=include_paths,
            exclude_paths=exclude_paths,
            max_depth=max_depth,
        )
