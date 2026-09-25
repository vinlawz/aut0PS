import unittest

from aut0ps.article import build_article_brief, select_article_sources


class ArticleBriefTests(unittest.TestCase):
    def test_selection_keeps_multiple_sources(self):
        rows = [
            {
                "url": "https://example.com/a",
                "canonical_url": "https://example.com/a",
                "source": "source-a",
                "title": "AI launch",
                "relevance_score": 0.9,
                "published_at": "2026-09-02",
                "fetched_at": "2026-09-02T10:00:00Z",
            },
            {
                "url": "https://example.com/b",
                "canonical_url": "https://example.com/b",
                "source": "source-b",
                "title": "Cloud release",
                "relevance_score": 0.4,
                "published_at": "2026-09-02",
                "fetched_at": "2026-09-02T09:00:00Z",
            },
        ]

        selected = select_article_sources(rows, limit=2)

        self.assertEqual({row["source"] for row in selected}, {"source-a", "source-b"})

    def test_brief_contains_citations_and_extracts(self):
        brief = build_article_brief(
            [
                {
                    "url": "https://example.com/ai",
                    "source": "Example News",
                    "title": "AI launch",
                    "published_at": "2026-09-02",
                    "relevance_score": 0.8,
                    "markdown": "A new AI technology release.",
                }
            ],
            topic="AI developments",
            excerpt_chars=100,
        )

        self.assertIn("# Agent brief: AI developments", brief)
        self.assertIn("[Example News](https://example.com/ai)", brief)
        self.assertIn("A new AI technology release.", brief)


if __name__ == "__main__":
    unittest.main()
