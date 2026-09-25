import json
import os
import tempfile
import unittest
from pathlib import Path

from aut0ps.config import load_dotenv, load_settings


class ConfigTests(unittest.TestCase):
    def test_platform_terms_and_sites_are_loaded(self):
        config = {
            "search_terms": [
                {"name": "X search", "platform": "x", "query": "AI launch"},
                {"name": "Google search", "platform": "google", "query": "AI news"},
            ],
            "crawl_sites": [
                {
                    "name": "Tech blog",
                    "url": "https://example.com/blog",
                    "limit": 5,
                    "include_paths": ["/blog/*"],
                    "rss_feeds": ["https://example.com/feed.xml"],
                    "max_depth": 1,
                },
                {"url": "https://example.org", "enabled": False},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seeds.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            settings = load_settings(path)

        self.assertEqual([seed.platform for seed in settings.search_seeds], ["x", "google"])
        self.assertEqual(settings.search_seeds[0].include_domains, ["x.com", "twitter.com"])
        self.assertEqual(settings.crawl_sites[0].name, "Tech blog")
        self.assertEqual(settings.crawl_sites[0].provider, "fallback")
        self.assertEqual(settings.crawl_sites[0].limit, 5)
        self.assertEqual(settings.crawl_sites[0].include_paths, ["/blog/*"])
        self.assertEqual(settings.crawl_sites[0].rss_feeds, ["https://example.com/feed.xml"])
        self.assertEqual(settings.crawl_sites[0].max_depth, 1)
        self.assertFalse(settings.crawl_sites[1].enabled)

    def test_twitter_platform_is_canonicalized_to_x(self):
        config = {
            "search_terms": [
                {"name": "Legacy", "platform": "twitter", "query": "AI launch"},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seeds.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            settings = load_settings(path)

        self.assertEqual(settings.search_seeds[0].platform, "x")
        self.assertEqual(settings.search_seeds[0].include_domains, ["x.com", "twitter.com"])

    def test_dotenv_strips_only_one_matched_quote_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                'TEST_DOTENV_A="\\"quoted\\""\nTEST_DOTENV_B=\'plain\'\n'
                "TEST_DOTENV_C=\"mismatched'\n",
                encoding="utf-8",
            )
            for key in ("TEST_DOTENV_A", "TEST_DOTENV_B", "TEST_DOTENV_C"):
                os.environ.pop(key, None)
            try:
                load_dotenv(path)
                self.assertEqual(os.environ["TEST_DOTENV_A"], '\\"quoted\\"')
                self.assertEqual(os.environ["TEST_DOTENV_B"], "plain")
                self.assertEqual(os.environ["TEST_DOTENV_C"], "\"mismatched'")
            finally:
                for key in ("TEST_DOTENV_A", "TEST_DOTENV_B", "TEST_DOTENV_C"):
                    os.environ.pop(key, None)


if __name__ == "__main__":
    unittest.main()
