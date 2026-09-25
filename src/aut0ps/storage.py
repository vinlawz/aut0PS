"""SQLite persistence and full-text search for normalized pages."""

import csv
import json
import re
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, TextIO

from .models import PageRecord


class Storage:
    """A small SQLite repository with an FTS5 search index."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path))
        self.connection.row_factory = sqlite3.Row
        # Tolerate overlapping runs: WAL allows concurrent readers during writes,
        # and busy_timeout waits for a lock instead of failing immediately.
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self._defer_commits = False
        self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                canonical_url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                author TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL,
                query TEXT NOT NULL DEFAULT '',
                markdown TEXT NOT NULL DEFAULT '',
                technologies_json TEXT NOT NULL DEFAULT '[]',
                topics_json TEXT NOT NULL DEFAULT '[]',
                relevance_score REAL NOT NULL DEFAULT 0,
                content_hash TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ok',
                error TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_pages_fetched_at ON pages (fetched_at DESC);
            """
        )
        self._migrate_legacy_fts()
        self.connection.executescript(
            """
            -- External-content FTS avoids duplicating markdown in the index.
            CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
                title,
                description,
                markdown,
                technologies_json,
                topics_json,
                content='pages',
                content_rowid='id'
            );

            CREATE TRIGGER IF NOT EXISTS pages_fts_after_insert AFTER INSERT ON pages BEGIN
                INSERT INTO pages_fts (
                    rowid, title, description, markdown, technologies_json, topics_json
                ) VALUES (
                    new.id, new.title, new.description, new.markdown,
                    new.technologies_json, new.topics_json
                );
            END;

            CREATE TRIGGER IF NOT EXISTS pages_fts_after_delete AFTER DELETE ON pages BEGIN
                INSERT INTO pages_fts (
                    pages_fts, rowid, title, description, markdown,
                    technologies_json, topics_json
                ) VALUES (
                    'delete', old.id, old.title, old.description, old.markdown,
                    old.technologies_json, old.topics_json
                );
            END;

            CREATE TRIGGER IF NOT EXISTS pages_fts_after_update AFTER UPDATE ON pages BEGIN
                INSERT INTO pages_fts (
                    pages_fts, rowid, title, description, markdown,
                    technologies_json, topics_json
                ) VALUES (
                    'delete', old.id, old.title, old.description, old.markdown,
                    old.technologies_json, old.topics_json
                );
                INSERT INTO pages_fts (
                    rowid, title, description, markdown, technologies_json, topics_json
                ) VALUES (
                    new.id, new.title, new.description, new.markdown,
                    new.technologies_json, new.topics_json
                );
            END;
            """
        )
        self.connection.commit()

    def _migrate_legacy_fts(self) -> None:
        """Rebuild the index when an older, content-duplicating pages_fts exists."""

        row = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'pages_fts'"
        ).fetchone()
        if row is None or "content='pages'" in (row[0] or ""):
            return
        self.connection.executescript(
            """
            DROP TABLE pages_fts;

            CREATE VIRTUAL TABLE pages_fts USING fts5(
                title,
                description,
                markdown,
                technologies_json,
                topics_json,
                content='pages',
                content_rowid='id'
            );

            INSERT INTO pages_fts(pages_fts) VALUES('rebuild');
            """
        )

    def _commit(self) -> None:
        if not self._defer_commits:
            self.connection.commit()

    @contextmanager
    def bulk(self) -> Iterator["Storage"]:
        """Defer commits across many upserts and commit once at the end."""

        self._defer_commits = True
        try:
            yield self
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        finally:
            self._defer_commits = False

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        for key in ("technologies_json", "topics_json"):
            output_key = key.replace("_json", "")
            try:
                result[output_key] = json.loads(result.pop(key))
            except (TypeError, json.JSONDecodeError):
                result[output_key] = []
        return result

    def upsert(self, record: PageRecord) -> int:
        self.connection.execute(
            """
            INSERT INTO pages (
                url, canonical_url, title, description, author, published_at, source,
                query, markdown, technologies_json, topics_json, relevance_score,
                content_hash, fetched_at, status, error, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_url) DO UPDATE SET
                url=excluded.url,
                title=excluded.title,
                description=excluded.description,
                author=excluded.author,
                published_at=excluded.published_at,
                source=excluded.source,
                query=excluded.query,
                markdown=excluded.markdown,
                technologies_json=excluded.technologies_json,
                topics_json=excluded.topics_json,
                relevance_score=excluded.relevance_score,
                content_hash=excluded.content_hash,
                fetched_at=excluded.fetched_at,
                status=excluded.status,
                error=excluded.error,
                raw_json=excluded.raw_json
            """,
            (
                record.url,
                record.canonical_url,
                record.title,
                record.description,
                record.author,
                record.published_at,
                record.source,
                record.query,
                record.markdown,
                json.dumps(record.technologies, ensure_ascii=False),
                json.dumps(record.topics, ensure_ascii=False),
                record.relevance_score,
                record.content_hash,
                record.fetched_at,
                record.status,
                record.error,
                record.raw_json,
            ),
        )
        row = self.connection.execute(
            "SELECT id FROM pages WHERE canonical_url = ?", (record.canonical_url,)
        ).fetchone()
        if row is None:
            raise RuntimeError("Page upsert did not return a row")
        page_id = int(row[0])
        self._commit()
        return page_id

    def count(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) FROM pages").fetchone()
        return int(row[0]) if row else 0

    def list_pages(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM pages ORDER BY fetched_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        tokens = re.findall(r"[A-Za-z0-9_]+", query)
        if not tokens:
            return self.list_pages(limit)
        match_query = " AND ".join('"%s"' % token for token in tokens)
        rows = self.connection.execute(
            """
            SELECT p.*
            FROM pages_fts f
            JOIN pages p ON p.id = f.rowid
            WHERE pages_fts MATCH ?
            ORDER BY p.relevance_score DESC, p.fetched_at DESC
            LIMIT ?
            """,
            (match_query, limit),
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def export_json(self, rows: Iterable[Dict[str, Any]], output: TextIO = sys.stdout) -> None:
        json.dump(list(rows), output, ensure_ascii=False, indent=2)
        output.write("\n")

    def export_csv(self, rows: Iterable[Dict[str, Any]], output: TextIO = sys.stdout) -> None:
        rows = list(rows)
        fieldnames = [
            "id",
            "url",
            "canonical_url",
            "title",
            "description",
            "author",
            "published_at",
            "source",
            "query",
            "technologies",
            "topics",
            "relevance_score",
            "content_hash",
            "fetched_at",
            "status",
            "error",
            "markdown",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            row = dict(row)
            row["technologies"] = ", ".join(row.get("technologies", []))
            row["topics"] = ", ".join(row.get("topics", []))
            writer.writerow(row)
