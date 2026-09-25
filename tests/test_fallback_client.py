import unittest

from aut0ps.fallback_client import OpenWebClient, ResilientClient
from aut0ps.models import SearchHit


class CreditClient:
    def __init__(self):
        self.calls = 0

    def search(self, query, limit=10, include_domains=None):
        self.calls += 1
        raise RuntimeError(
            "Unexpected error during start crawl job: Status code 429. "
            "Rate limit exceeded. Consumed (req/min): 5, Remaining (req/min): 0."
        )


class SearchFallback:
    def search(self, query, limit=10, include_domains=None):
        return [SearchHit(url="https://example.com/article", title="Fallback result")]


class FallbackClientTests(unittest.TestCase):
    def test_rss_feed_is_used_for_a_site_crawl(self):
        root_url = "https://example.com/tech"
        feed_url = "https://example.com/feed.xml"
        responses = {
            root_url: (
                b'<html><head><link rel="alternate" type="application/rss+xml" '
                b'href="/feed.xml"></head><body>Technology</body></html>',
                "text/html",
                root_url,
            ),
            feed_url: (
                b"""<?xml version="1.0"?>
                <rss version="2.0"><channel><item>
                <title>New AI device</title>
                <link>https://example.com/2026/09/ai-device</link>
                <description><![CDATA[An AI technology launch.]]></description>
                <pubDate>Wed, 02 Sep 2026 08:00:00 GMT</pubDate>
                </item></channel></rss>""",
                "application/rss+xml",
                feed_url,
            ),
        }

        def fetch(url):
            if url not in responses:
                raise RuntimeError("not found: %s" % url)
            return responses[url]

        client = OpenWebClient(http_fetcher=fetch)
        pages = client.crawl(
            root_url,
            limit=1,
            include_paths=["/2026/*"],
            max_depth=1,
        )

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].url, "https://example.com/2026/09/ai-device")
        self.assertIn("AI technology launch", pages[0].markdown)

    def test_credit_failure_switches_to_fallback_for_the_rest_of_the_run(self):
        primary = CreditClient()
        fallback = SearchFallback()
        client = ResilientClient(primary=primary, fallback=fallback)

        self.assertEqual(client.search("AI")[0].title, "Fallback result")
        self.assertEqual(client.search("cloud")[0].title, "Fallback result")
        self.assertEqual(primary.calls, 1)
        self.assertEqual(client.fallback_uses["search"], 2)

    def test_html_fallback_follows_bounded_same_site_links(self):
        root_url = "https://example.com/tech"
        article_url = "https://example.com/tech/ai-launch"
        responses = {
            root_url: (
                b'<html><head><title>Tech</title></head><body>'
                b'<a href="/tech/ai-launch">AI launch</a></body></html>',
                "text/html",
                root_url,
            ),
            article_url: (
                b"<html><article><h1>AI launch</h1><p>New technology release.</p>"
                b"</article></html>",
                "text/html",
                article_url,
            ),
        }

        def fetch(url):
            if url not in responses:
                raise RuntimeError("not found: %s" % url)
            return responses[url]

        client = OpenWebClient(http_fetcher=fetch)
        pages = client.crawl(root_url, limit=1, include_paths=["/tech/*"], max_depth=1)

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].url, article_url)
        self.assertIn("New technology release", pages[0].markdown)


if __name__ == "__main__":
    unittest.main()
