import tempfile
import unittest
from pathlib import Path

from aut0ps.models import FetchedPage, SearchHit
from aut0ps.pipeline import Aut0psPipeline, domain_matches
from aut0ps.storage import Storage


class FakeClient:
    def __init__(self):
        self.crawl_options = None

    def search(self, query, limit=10, include_domains=None):
        return [
            SearchHit(
                url="https://example.com/python",
                title="Python engineering",
                description="Technology news",
            )
        ]

    def scrape(self, url):
        return FetchedPage(
            url=url,
            markdown="# Python engineering\n\nTechnology news for developers.",
            metadata={"title": "Python engineering"},
            raw={"source": "fake"},
        )

    def crawl(
        self,
        url,
        limit=25,
        include_paths=(),
        exclude_paths=(),
        max_depth=None,
    ):
        self.crawl_options = {
            "url": url,
            "limit": limit,
            "include_paths": list(include_paths),
            "exclude_paths": list(exclude_paths),
            "max_depth": max_depth,
        }
        return [self.scrape(url + "/article")]


class PipelineTests(unittest.TestCase):
    def test_domain_matches_strips_www_prefix_only(self):
        self.assertTrue(domain_matches("https://www.wired.com/a", ["wired.com"]))
        self.assertTrue(domain_matches("https://blog.wired.com/a", ["wired.com"]))
        self.assertTrue(domain_matches("https://wired.com/a", ["www.wired.com"]))
        # "weather.com" must not lose leading w/. characters.
        self.assertTrue(domain_matches("https://weather.com/a", ["weather.com"]))
        self.assertFalse(domain_matches("https://ired.com/a", ["wired.com"]))
        self.assertFalse(domain_matches("https://notwired.com/a", ["wired.com"]))

    def test_discover_continues_after_failed_seed(self):
        class SeedFailingClient(FakeClient):
            def search(self, query, limit=10, include_domains=None):
                if query == "broken":
                    raise RuntimeError("search exploded")
                return super().search(query, limit=limit, include_domains=include_domains)

        with tempfile.TemporaryDirectory() as directory:
            with Storage(Path(directory) / "test.db") as storage:
                pipeline = Aut0psPipeline(
                    client=SeedFailingClient(),
                    storage=storage,
                    terms=["python", "technology"],
                )
                summary = pipeline.discover(["broken", "python"], limit=1)
                self.assertEqual(summary.failed, 1)
                self.assertEqual(summary.stored, 1)
                self.assertIn("search 'broken'", summary.errors[0])

    def test_discover_scrapes_and_stores_hits(self):
        with tempfile.TemporaryDirectory() as directory:
            with Storage(Path(directory) / "test.db") as storage:
                pipeline = Aut0psPipeline(
                    client=FakeClient(),
                    storage=storage,
                    terms=["python", "technology"],
                )
                summary = pipeline.discover(["python"], limit=1)
                self.assertEqual(summary.discovered, 1)
                self.assertEqual(summary.fetched, 1)
                self.assertEqual(summary.stored, 1)
                self.assertEqual(storage.count(), 1)

    def test_excluded_domains_are_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            with Storage(Path(directory) / "test.db") as storage:
                pipeline = Aut0psPipeline(
                    client=FakeClient(),
                    storage=storage,
                    terms=["python"],
                    exclude_domains=["example.com"],
                )
                summary = pipeline.discover(["python"], limit=1)
                self.assertEqual(summary.stored, 0)
                self.assertEqual(summary.skipped, 1)

    def test_crawl_forwards_scope_options(self):
        with tempfile.TemporaryDirectory() as directory:
            with Storage(Path(directory) / "test.db") as storage:
                client = FakeClient()
                pipeline = Aut0psPipeline(
                    client=client,
                    storage=storage,
                    terms=["python"],
                )
                summary = pipeline.crawl(
                    "https://example.com",
                    limit=3,
                    include_paths=["/blog/*"],
                    exclude_paths=["/login*"],
                    max_depth=1,
                )
                self.assertEqual(summary.stored, 1)
                self.assertEqual(client.crawl_options["limit"], 3)
                self.assertEqual(client.crawl_options["include_paths"], ["/blog/*"])
                self.assertEqual(client.crawl_options["exclude_paths"], ["/login*"])
                self.assertEqual(client.crawl_options["max_depth"], 1)


if __name__ == "__main__":
    unittest.main()
