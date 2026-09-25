import unittest

from aut0ps.models import FetchedPage
from aut0ps.normalize import canonicalize_url, normalize_fetched_page


class NormalizeTests(unittest.TestCase):
    def test_canonicalize_url_removes_tracking_parameters(self):
        value = canonicalize_url(
            "HTTPS://Example.com/article/?utm_source=newsletter&b=2&a=1#comments"
        )
        self.assertEqual(value, "https://example.com/article?a=1&b=2")

    def test_normalize_extracts_metadata_and_terms(self):
        page = FetchedPage(
            url="https://example.com/post/",
            markdown="# Building with Python\n\nA cloud engineering article.",
            metadata={
                "title": "Building with Python",
                "description": "A cloud engineering article.",
                "author": "Ada",
                "publishedTime": "2026-09-01",
            },
            raw={"success": True},
        )
        record = normalize_fetched_page(
            page,
            source="scrape",
            query="python",
            terms=["python", "cloud", "engineering"],
        )
        self.assertEqual(record.title, "Building with Python")
        self.assertEqual(record.author, "Ada")
        self.assertEqual(record.published_at, "2026-09-01")
        self.assertEqual(record.canonical_url, "https://example.com/post")
        self.assertIn("python", [term.lower() for term in record.technologies])
        self.assertGreater(record.relevance_score, 0)


if __name__ == "__main__":
    unittest.main()

