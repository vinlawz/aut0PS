import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from aut0ps.models import PageRecord
from aut0ps.storage import Storage


def make_record(url="https://example.com/post"):
    return PageRecord(
        url=url,
        canonical_url=url,
        title="Python cloud engineering",
        description="A technology article",
        author="Ada",
        published_at="2026-09-01",
        source="test",
        query="python",
        markdown="# Python cloud engineering\n\nTechnology article",
        technologies=["Python", "cloud"],
        topics=["technology"],
        relevance_score=0.8,
        content_hash="abc",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        raw_json='{"ok": true}',
    )


class StorageTests(unittest.TestCase):
    def test_upsert_and_full_text_search(self):
        with tempfile.TemporaryDirectory() as directory:
            with Storage(Path(directory) / "test.db") as storage:
                storage.upsert(make_record())
                self.assertEqual(storage.count(), 1)
                rows = storage.search("Python cloud")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["title"], "Python cloud engineering")
                self.assertEqual(rows[0]["technologies"], ["Python", "cloud"])

    def test_upsert_updates_same_canonical_url(self):
        with tempfile.TemporaryDirectory() as directory:
            with Storage(Path(directory) / "test.db") as storage:
                storage.upsert(make_record())
                updated = make_record()
                updated.title = "Updated Python article"
                storage.upsert(updated)
                self.assertEqual(storage.count(), 1)
                self.assertEqual(storage.list_pages()[0]["title"], "Updated Python article")
                # The FTS index must reflect the update, not the original text.
                self.assertEqual(len(storage.search("Updated")), 1)

    def test_bulk_defers_commits_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            with Storage(path) as storage:
                with storage.bulk():
                    storage.upsert(make_record("https://example.com/a"))
                    storage.upsert(make_record("https://example.com/b"))
            with Storage(path) as storage:
                self.assertEqual(storage.count(), 2)
                self.assertEqual(len(storage.search("Python")), 2)

    def test_wal_mode_and_busy_timeout_are_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            with Storage(Path(directory) / "test.db") as storage:
                mode = storage.connection.execute("PRAGMA journal_mode").fetchone()[0]
                self.assertEqual(str(mode).lower(), "wal")
                timeout = storage.connection.execute("PRAGMA busy_timeout").fetchone()[0]
                self.assertEqual(int(timeout), 5000)

    def test_legacy_fts_schema_is_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            with Storage(path) as storage:
                storage.upsert(make_record())
                # Recreate the old content-duplicating FTS layout.
                storage.connection.executescript(
                    """
                    DROP TRIGGER pages_fts_after_insert;
                    DROP TRIGGER pages_fts_after_delete;
                    DROP TRIGGER pages_fts_after_update;
                    DROP TABLE pages_fts;
                    CREATE VIRTUAL TABLE pages_fts USING fts5(
                        page_id UNINDEXED, title, description, markdown,
                        technologies, topics
                    );
                    """
                )
                storage.connection.commit()
            with Storage(path) as storage:
                self.assertEqual(len(storage.search("Python cloud")), 1)


if __name__ == "__main__":
    unittest.main()

