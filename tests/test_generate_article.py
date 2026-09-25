import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "generate_article.py"
    spec = importlib.util.spec_from_file_location("generate_article", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class GenerateArticleFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_module()

    def test_run_fallback_article_includes_sources(self):
        pages = [
            {
                "url": "https://example.com/devops",
                "title": "devops update",
                "description": "platform teams tightened deploy guardrails.",
                "markdown": "platform teams tightened deploy guardrails and rollback steps.",
                "source": "Example",
            },
            {
                "url": "https://example.com/observability",
                "title": "observability fixes",
                "description": "teams improved alert quality.",
                "markdown": "teams improved alert quality and trimmed noisy dashboards.",
                "source": "Example",
            },
        ]

        article = self.module._fallback_run_article("2026-09-25", "5", pages)

        self.assertEqual(article["title"], "devops roundup for 2026-09-25, edition 5")
        self.assertIn("## sources", article["body"])
        self.assertIn("[devops update](https://example.com/devops)", article["body"])
        self.assertIn("automation", article["tags"])

    def test_digest_fallback_lists_editions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            edition_one = root / "edition-1"
            edition_two = root / "edition-2"
            edition_one.mkdir()
            edition_two.mkdir()
            (edition_one / "index.md").write_text(
                "## what changed\n\nthis one cites [example](https://example.com/a).\n",
                encoding="utf-8",
            )
            (edition_two / "index.md").write_text(
                "## what changed\n\nthis one cites [other](https://example.com/b).\n",
                encoding="utf-8",
            )

            article = self.module._fallback_digest_article(
                "2026-09-25",
                [edition_one / "index.md", edition_two / "index.md"],
            )

        self.assertEqual(article["title"], "2026-09-25 daily devops digest")
        self.assertIn("## today's editions", article["body"])
        self.assertIn("- edition-1", article["body"])
        self.assertIn("https://example.com/a", article["body"])

    def test_generate_uses_fallback_when_copilot_fails(self):
        module = self.module
        original_call_model = module._call_model
        original_qa_check = module._qa_check
        try:
            def failing_call_model(*_args, **_kwargs):
                raise module.CopilotGenerationError("Authentication failed")

            module._call_model = failing_call_model
            module._qa_check = lambda path, title: (0, "all checks passed")

            with tempfile.TemporaryDirectory() as tmp:
                bundle_dir = Path(tmp) / "articles" / "2026-09-25" / "edition-5"
                index_path = module._generate(
                    "",
                    "prompt",
                    bundle_dir,
                    {"date": "2026-09-25", "edition": "5"},
                    lambda: {
                        "title": "fallback article",
                        "description": "desc",
                        "tags": ["devops"],
                        "body": "## sources\n\n- [example](https://example.com/source)",
                    },
                )
                body = index_path.read_text(encoding="utf-8")
                meta = json.loads((bundle_dir / "meta.json").read_text(encoding="utf-8"))

            self.assertIn("## sources", body)
            self.assertIn(module.FOOTER, body)
            self.assertEqual(meta["title"], "fallback article")
        finally:
            module._call_model = original_call_model
            module._qa_check = original_qa_check


if __name__ == "__main__":
    unittest.main()
