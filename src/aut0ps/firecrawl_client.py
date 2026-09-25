"""Small adapter around the official Firecrawl Python SDK.

Keeping the SDK behind this adapter makes the rest of Aut0ps easy to test and
keeps SDK response-shape differences out of the storage and normalization layers.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, TypeVar

from .models import FetchedPage, SearchHit

T = TypeVar("T")


class FirecrawlConfigurationError(RuntimeError):
    """Raised when an API key or the SDK is unavailable."""


def _primitive(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_primitive(item) for item in value]
    for method_name in ("model_dump", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                return _primitive(method())
            except TypeError:
                pass
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return {
            str(key): _primitive(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return value


def _mapping(value: Any) -> Dict[str, Any]:
    primitive = _primitive(value)
    return dict(primitive) if isinstance(primitive, Mapping) else {}


def _value(value: Any, *keys: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        lowered = {str(key).lower(): item for key, item in value.items()}
        for key in keys:
            if key.lower() in lowered:
                return lowered[key.lower()]
    for key in keys:
        if hasattr(value, key):
            return getattr(value, key)
    return default


def _metadata(value: Any) -> Dict[str, Any]:
    return _mapping(_value(value, "metadata", default={}))


def _add_domain_filters(query: str, include_domains: Iterable[str]) -> str:
    """Use portable search operators instead of SDK-version-specific parameters."""

    domains = [str(domain).strip() for domain in include_domains if str(domain).strip()]
    if not domains:
        return query
    site_filters = " OR ".join("site:%s" % domain for domain in domains)
    return "(%s) (%s)" % (query, site_filters)


def _as_page(value: Any, fallback_url: str = "") -> FetchedPage:
    primitive = _primitive(value)
    url = str(
        _value(
            primitive,
            "sourceURL",
            "source_url",
            "url",
            default="",
        )
        or _value(_metadata(primitive), "sourceURL", "source_url", "url", default="")
        or fallback_url
    )
    markdown = str(_value(primitive, "markdown", default="") or "")
    return FetchedPage(
        url=url,
        markdown=markdown,
        metadata=_metadata(primitive),
        raw=_mapping(primitive),
    )


def _results(value: Any, key: str) -> List[Any]:
    primitive = _primitive(value)
    found = _value(primitive, key, default=None)
    if found is None:
        found = _value(_value(primitive, "data", default={}), key, default=[])
    if isinstance(found, list):
        return found
    return []


class FirecrawlClient:
    """Firecrawl SDK adapter used by Aut0psPipeline."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        client: Any = None,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
        request_interval_seconds: float = 0.0,
        operation_timeout_seconds: float = 300.0,
    ) -> None:
        self.max_retries = max(0, max_retries)
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.request_interval_seconds = max(0.0, request_interval_seconds)
        self.operation_timeout_seconds = max(0.0, operation_timeout_seconds)
        self._last_request_at = 0.0
        if client is not None:
            self._client = client
            return
        if not api_key:
            raise FirecrawlConfigurationError(
                "FIRECRAWL_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        try:
            try:
                from firecrawl import Firecrawl
            except ImportError:
                # firecrawl-py 2.x exposes the v2-compatible client as FirecrawlApp.
                from firecrawl import FirecrawlApp as Firecrawl
        except ImportError as exc:
            raise FirecrawlConfigurationError(
                "The Firecrawl SDK is not installed. Run: uv sync"
            ) from exc
        self._client = Firecrawl(api_key=api_key)

    def _invoke(self, operation: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Run one SDK call, bounded by the operation timeout so runs never hang."""

        if self.operation_timeout_seconds <= 0:
            return operation(*args, **kwargs)
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(operation, *args, **kwargs)
        try:
            return future.result(timeout=self.operation_timeout_seconds)
        except FutureTimeoutError:
            # Abandon the stuck thread; do not block shutdown waiting for it.
            executor.shutdown(wait=False, cancel_futures=True)
            raise TimeoutError(
                "Firecrawl operation timed out after %.0f seconds"
                % self.operation_timeout_seconds
            ) from None
        finally:
            executor.shutdown(wait=False)

    def _call(self, operation: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Call the SDK with bounded retries and a polite request interval."""

        for attempt in range(self.max_retries + 1):
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.request_interval_seconds:
                time.sleep(self.request_interval_seconds - elapsed)
            try:
                self._last_request_at = time.monotonic()
                return self._invoke(operation, *args, **kwargs)
            except (AttributeError, TypeError):
                # These errors indicate an SDK interface mismatch, not a transient request.
                raise
            except Exception:
                if attempt >= self.max_retries:
                    raise
                time.sleep(self.backoff_seconds * (2**attempt))
        raise RuntimeError("Firecrawl operation did not return")

    def search(
        self,
        query: str,
        limit: int = 10,
        include_domains: Optional[Iterable[str]] = None,
    ) -> List[SearchHit]:
        kwargs: Dict[str, Any] = {"limit": limit}
        search_query = _add_domain_filters(query, include_domains or [])
        result = self._call(self._client.search, search_query, **kwargs)
        hits: List[SearchHit] = []
        for item in _results(result, "web"):
            item = _mapping(item)
            metadata = _metadata(item)
            url = str(
                _value(item, "url", default="")
                or _value(metadata, "sourceURL", "source_url", default="")
            )
            if not url:
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=str(
                        _value(item, "title", default="")
                        or _value(metadata, "title", default="")
                    ),
                    description=str(
                        _value(item, "description", default="")
                        or _value(metadata, "description", default="")
                    ),
                    markdown=str(_value(item, "markdown", default="") or ""),
                    metadata=metadata,
                    raw=item,
                )
            )
        return hits

    def scrape(self, url: str) -> FetchedPage:
        try:
            result = self._call(self._client.scrape, url, formats=["markdown"])
        except (AttributeError, TypeError):
            legacy = getattr(self._client, "scrape_url", None)
            if not callable(legacy):
                raise
            try:
                result = self._call(legacy, url, formats=["markdown"])
            except TypeError:
                result = self._call(legacy, url)
        return _as_page(result, fallback_url=url)

    def map(self, url: str, search: Optional[str] = None) -> List[str]:
        kwargs = {"search": search} if search else {}
        try:
            result = self._call(self._client.map, url, **kwargs)
        except AttributeError:
            legacy = getattr(self._client, "map_url", None)
            if not callable(legacy):
                raise
            try:
                result = self._call(legacy, url, **kwargs)
            except TypeError:
                result = self._call(legacy, url, params=kwargs)
        links = _value(_primitive(result), "links", default=None)
        if links is None:
            links = _value(_value(_primitive(result), "data", default={}), "links", default=[])
        output = []
        for link in links or []:
            if isinstance(link, Mapping):
                link = _value(link, "url", default="")
            if str(link).strip():
                output.append(str(link).strip())
        return output

    def crawl(
        self,
        url: str,
        limit: int = 25,
        include_paths: Iterable[str] = (),
        exclude_paths: Iterable[str] = (),
        max_depth: Optional[int] = None,
    ) -> List[FetchedPage]:
        include_paths = list(include_paths)
        exclude_paths = list(exclude_paths)
        kwargs: Dict[str, Any] = {"limit": limit}
        if include_paths:
            kwargs["include_paths"] = include_paths
        if exclude_paths:
            kwargs["exclude_paths"] = exclude_paths
        if max_depth is not None:
            kwargs["max_depth"] = max_depth
        try:
            result = self._call(self._client.crawl, url, **kwargs)
        except AttributeError:
            legacy = getattr(self._client, "crawl_url", None)
            if not callable(legacy):
                raise
            try:
                from firecrawl import ScrapeOptions

                result = self._call(
                    legacy,
                    url,
                    **kwargs,
                    scrape_options=ScrapeOptions(formats=["markdown"]),
                )
            except (ImportError, TypeError):
                result = self._call(legacy, url, **kwargs)
        pages = _results(result, "data")
        if not pages:
            pages = _results(result, "documents")
        return [_as_page(item) for item in pages]
