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

    def test_run_fallback_article_uses_placeholder_when_title_is_missing(self):
        article = self.module._fallback_run_article(
            "2026-09-25",
            "5",
            [
                {
                    "url": "",
                    "title": "",
                    "description": "",
                    "markdown": "",
                    "source": "",
                }
            ],
        )

        self.assertIn("### source item 1", article["body"])
        self.assertIn("- [source item 1]()", article["body"])

    def test_run_fallback_article_counts_named_sources_without_urls(self):
        article = self.module._fallback_run_article(
            "2026-09-25",
            "5",
            [
                {
                    "url": "",
                    "title": "internal note",
                    "description": "deployment cleanup",
                    "markdown": "deployment cleanup",
                    "source": "Internal Feed",
                }
            ],
        )

        self.assertIn("this edition pulled 1 pages across 1 sources", article["body"])

    def test_run_fallback_article_counts_malformed_urls_as_distinct_sources(self):
        article = self.module._fallback_run_article(
            "2026-09-25",
            "5",
            [
                {
                    "url": "/first",
                    "title": "first",
                    "description": "one",
                    "markdown": "one",
                    "source": "",
                },
                {
                    "url": "/second",
                    "title": "second",
                    "description": "two",
                    "markdown": "two",
                    "source": "",
                },
            ],
        )

        self.assertIn("this edition pulled 2 pages across 2 sources", article["body"])

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

        self.assertEqual(
            article["title"],
            "2026-09-25 daily digest for people who still have tickets to close",
        )
        self.assertIn("## today's editions", article["body"])
        self.assertIn("- edition-1", article["body"])
        self.assertIn("https://example.com/a", article["body"])

    def test_label_from_url_only_strips_www_prefix(self):
        self.assertEqual(
            self.module._label_from_url("https://www.example.com/a"), "example.com"
        )
        self.assertEqual(
            self.module._label_from_url("https://web.example.com/a"), "web.example.com"
        )

    def test_parse_article_json_wraps_decode_failures(self):
        with self.assertRaises(self.module.CopilotGenerationError):
            self.module._parse_article_json('prefix {"title": "bad", } suffix')

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

    def test_generate_uses_fallback_when_repair_call_fails(self):
        module = self.module
        original_call_model = module._call_model
        original_qa_check = module._qa_check
        try:
            responses = iter(
                [
                    '{"title":"model article","body":"## sources\\n\\n- [one](https://example.com)"}'
                ]
            )
            qa_results = iter([(1, "FAIL line 1"), (0, "all checks passed")])

            def call_model(*_args, **_kwargs):
                try:
                    return next(responses)
                except StopIteration as exc:
                    raise module.CopilotGenerationError(
                        "Authentication failed during repair"
                    ) from exc

            def qa_check(*_args, **_kwargs):
                return next(qa_results)

            module._call_model = call_model
            module._qa_check = qa_check

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

                meta = json.loads((bundle_dir / "meta.json").read_text(encoding="utf-8"))

            self.assertEqual(index_path.name, "index.md")
            self.assertEqual(meta["title"], "fallback article")
        finally:
            module._call_model = original_call_model
            module._qa_check = original_qa_check


class DevToPublishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_module()

    def test_devto_tags_strips_hyphens_dedupes_and_caps_at_four(self):
        tags = self.module._devto_tags(
            ["Platform-Engineering", "devops", "DevOps", "site-reliability", "ci-cd", "extra"]
        )
        self.assertEqual(tags, ["platformengineering", "devops", "sitereliability", "cicd"])

    def test_devto_tags_handles_empty_input(self):
        self.assertEqual(self.module._devto_tags(None), [])
        self.assertEqual(self.module._devto_tags([]), [])

    def test_publish_to_devto_creates_when_no_existing_id(self):
        module = self.module
        original_request = module._devto_request
        calls = []
        try:
            def fake_request(method, url, api_key, payload):
                calls.append((method, url, api_key, payload))
                return {"id": 42, "url": "https://dev.to/user/new-article"}

            module._devto_request = fake_request
            result = module._publish_to_devto(
                api_key="key123",
                title="new article",
                body_markdown="body",
                tags=["devops"],
                description="desc",
            )
        finally:
            module._devto_request = original_request

        self.assertEqual(result["id"], 42)
        method, url, api_key, payload = calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, module.DEVTO_API_URL)
        self.assertEqual(payload["article"]["title"], "new article")
        self.assertEqual(payload["article"]["tags"], ["devops"])

    def test_publish_to_devto_updates_when_existing_id(self):
        module = self.module
        original_request = module._devto_request
        calls = []
        try:
            def fake_request(method, url, api_key, payload):
                calls.append((method, url))
                return {"id": 7, "url": "https://dev.to/user/updated"}

            module._devto_request = fake_request
            module._publish_to_devto(
                api_key="key123",
                title="t",
                body_markdown="b",
                tags=[],
                devto_id=7,
            )
        finally:
            module._devto_request = original_request

        method, url = calls[0]
        self.assertEqual(method, "PUT")
        self.assertEqual(url, "%s/7" % module.DEVTO_API_URL)

    def test_publish_bundle_writes_devto_id_and_url_to_meta(self):
        module = self.module
        original_publish = module._publish_to_devto
        try:
            module._publish_to_devto = lambda **_kwargs: {
                "id": 99,
                "url": "https://dev.to/user/sample",
            }
            with tempfile.TemporaryDirectory() as tmp:
                bundle_dir = Path(tmp)
                index_path = bundle_dir / "index.md"
                index_path.write_text("body text", encoding="utf-8")
                (bundle_dir / "meta.json").write_text(
                    json.dumps({"title": "existing", "tags": ["devops"]}), encoding="utf-8"
                )

                module._publish_bundle(
                    index_path,
                    {"title": "existing", "tags": ["devops"], "description": "d"},
                    "key123",
                )

                meta = json.loads((bundle_dir / "meta.json").read_text(encoding="utf-8"))
        finally:
            module._publish_to_devto = original_publish

        self.assertEqual(meta["devto_id"], 99)
        self.assertEqual(meta["devto_url"], "https://dev.to/user/sample")

    def test_publish_bundle_warns_and_keeps_meta_on_failure(self):
        module = self.module
        original_publish = module._publish_to_devto
        try:
            def failing_publish(**_kwargs):
                raise module.DevToPublishError("dev.to API error 422: bad request")

            module._publish_to_devto = failing_publish
            with tempfile.TemporaryDirectory() as tmp:
                bundle_dir = Path(tmp)
                index_path = bundle_dir / "index.md"
                index_path.write_text("body text", encoding="utf-8")
                (bundle_dir / "meta.json").write_text(
                    json.dumps({"title": "existing"}), encoding="utf-8"
                )

                module._publish_bundle(index_path, {"title": "existing"}, "key123")

                meta = json.loads((bundle_dir / "meta.json").read_text(encoding="utf-8"))
        finally:
            module._publish_to_devto = original_publish

        self.assertNotIn("devto_id", meta)


if __name__ == "__main__":
    unittest.main()
