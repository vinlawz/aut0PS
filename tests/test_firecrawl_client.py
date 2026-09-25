import time
import unittest

from aut0ps.firecrawl_client import FirecrawlClient


class LegacyClient:
    def __init__(self):
        self.search_query = ""

    def search(self, query, limit=None, **kwargs):
        self.search_query = query
        return {"web": [{"url": "https://example.com", "title": "Example"}]}

    def scrape_url(self, url, formats=None):
        return {"markdown": "# Example", "metadata": {"sourceURL": url}}

    def map_url(self, url, search=None):
        return {"links": ["https://example.com/about"]}

    def crawl_url(self, url, limit=None, scrape_options=None, **kwargs):
        self.crawl_kwargs = kwargs
        return {"data": [{"markdown": "# Example", "metadata": {"sourceURL": url}}]}


class FlakyClient:
    def __init__(self):
        self.calls = 0

    def search(self, query, limit=None, **kwargs):
        self.calls += 1
        if self.calls < 3:
            raise RuntimeError("temporary failure")
        return {"web": [{"url": "https://example.com/recovered"}]}


class HangingClient:
    def search(self, query, limit=None, **kwargs):
        time.sleep(5)
        return {"web": []}


class AdapterTests(unittest.TestCase):
    def test_supports_legacy_named_sdk_methods(self):
        fake = LegacyClient()
        client = FirecrawlClient(client=fake)
        self.assertEqual(client.search("example")[0].url, "https://example.com")
        self.assertEqual(client.scrape("https://example.com").markdown, "# Example")
        self.assertEqual(client.map("https://example.com"), ["https://example.com/about"])
        self.assertEqual(
            len(
                client.crawl(
                    "https://example.com",
                    include_paths=["/blog/*"],
                    exclude_paths=["/login*"],
                    max_depth=1,
                )
            ),
            1,
        )
        self.assertEqual(fake.crawl_kwargs["include_paths"], ["/blog/*"])
        self.assertEqual(fake.crawl_kwargs["exclude_paths"], ["/login*"])
        self.assertEqual(fake.crawl_kwargs["max_depth"], 1)

    def test_domain_filters_are_encoded_in_the_query(self):
        fake = LegacyClient()
        client = FirecrawlClient(client=fake)
        client.search("AI launch", include_domains=["x.com", "twitter.com"])
        self.assertEqual(
            fake.search_query,
            "(AI launch) (site:x.com OR site:twitter.com)",
        )

    def test_retries_transient_sdk_errors(self):
        fake = FlakyClient()
        client = FirecrawlClient(client=fake, max_retries=2, backoff_seconds=0)
        self.assertEqual(client.search("example")[0].url, "https://example.com/recovered")
        self.assertEqual(fake.calls, 3)

    def test_hung_operations_time_out(self):
        client = FirecrawlClient(
            client=HangingClient(),
            max_retries=0,
            operation_timeout_seconds=0.1,
        )
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            client.search("example")
        self.assertLess(time.monotonic() - started, 2.0)


if __name__ == "__main__":
    unittest.main()
