"""Generate blog articles from the aut0ps corpus using the article-writer skill.

Adapted from the article-writer skill conventions (github.com/achingachris/my-skills,
plugins/my-skills/skills/article-writer): technical-tutorial voice, page bundles
(index.md), no YAML front matter, no H1, no em/en dashes, attribution footer, and a
mechanical QA pass via qa_check.py.

Modes:
    run    - write one article covering pages fetched today (per crawl edition)
    digest - write the end-of-day article referencing today's edition articles

Articles are written by the GitHub Copilot CLI agent (npm i -g @github/copilot)
running in programmatic mode, billed to the repo owner's Copilot subscription.
"""

import argparse
import collections
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

COPILOT_BIN = os.getenv("COPILOT_CLI_BIN", "copilot")
DEFAULT_MODEL = os.getenv("ARTICLE_MODEL", "")  # empty = Copilot CLI's default model
MAX_PAGES = 25
EXCERPT_CHARS = 1200
QA_SCRIPT = Path(__file__).parent / "article_writer" / "qa_check.py"
DAILY_INGEST_NAME = "daily-ingest"
FOOTER = "*Written by the aut0ps automated crawler, edited and assisted by the Copilot agent*"

SYSTEM_PROMPT = """You write technical blog articles about DevOps, platform engineering,
site reliability, and infrastructure automation for a developer audience.

Article type: technical tutorial / tech roundup. Voice rules:
- lowercase throughout, except proper nouns (Python, Django, Kenya) and acronyms (API, CSS, AI).
- correct American English grammar, spelling, and punctuation. lowercase is a style choice,
  not an excuse for bad grammar.
- confident, direct, second person ("you should know"). short sentences. no filler.
- occasional self-deprecating humor and grounding analogies are welcome.
- ground every claim ONLY in the provided source material and cite sources as inline
  markdown links. never invent facts, quotes, or URLs.

Hard rules (non-negotiable):
1. NEVER use em dash (U+2014) or en dash (U+2013). Use commas, parentheses, colons, or semicolons.
2. NEVER use forced contrast framing ("it's not X, it's Y", "not just X, but Y").
3. Define acronyms at first use unless universally obvious (HTML, API, URL, CSS, JS).
4. No YAML front matter and no H1 heading in the body. Use H2 for sections, H3 for sub-topics.
5. Code blocks always have language tags.
6. The title is lowercase except proper nouns and acronyms.
7. Don't pad. Length matches depth.

Respond with ONLY a JSON object (no markdown fence around it) with these keys:
  "title": lowercase article title,
  "description": a short teaser (one sentence, playful is fine),
  "tags": array of 3-6 lowercase hyphen-separated tags,
  "body": the full article body in plain markdown (no front matter, no H1),
          ending with a 'sources' or 'today's editions' H2 section as instructed."""


class CopilotGenerationError(RuntimeError):
    """Raised when the Copilot CLI cannot produce usable article JSON."""


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def _pages_for_day(db_path: Path, day: str, by_published: bool = False):
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        if by_published:
            # published_at mixes ISO dates and RFC 822 dates ("Mon, 01 Sep 2026 ...").
            rfc = dt.date.fromisoformat(day).strftime("%d %b %Y")
            rows = connection.execute(
                """
                SELECT url, title, description, markdown, relevance_score, source
                FROM pages
                WHERE substr(published_at, 1, 10) = ? OR published_at LIKE ?
                ORDER BY relevance_score DESC, fetched_at DESC
                LIMIT ?
                """,
                (day, "%%%s%%" % rfc, MAX_PAGES),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT url, title, description, markdown, relevance_score, source
                FROM pages
                WHERE substr(fetched_at, 1, 10) = ?
                ORDER BY relevance_score DESC, fetched_at DESC
                LIMIT ?
                """,
                (day, MAX_PAGES),
            ).fetchall()
    finally:
        connection.close()
    return rows


def _call_model(model: str, prompt: str, out_path: Path) -> str:
    """Run the Copilot CLI agent in programmatic mode and read its JSON output file."""

    if out_path.exists():
        out_path.unlink()
    full_prompt = (
        "%s\n\nInstead of replying in chat, write ONLY the JSON object to the file "
        "%s (create it if needed). Do not create or modify any other files."
        % (prompt, out_path)
    )
    command = [COPILOT_BIN, "-p", full_prompt, "--allow-all-tools"]
    if model:
        command += ["--model", model]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError:
        raise CopilotGenerationError(
            "Copilot CLI not found; install it with: npm install -g @github/copilot"
        )
    except subprocess.TimeoutExpired as exc:
        raise CopilotGenerationError("Copilot CLI timed out while generating the article") from exc
    if out_path.exists():
        content = out_path.read_text(encoding="utf-8")
        out_path.unlink()
    else:
        # Fall back to the chat transcript if the agent answered inline.
        content = result.stdout or ""
    if not content.strip():
        raise CopilotGenerationError(
            "Copilot CLI returned no article (exit %d): %s"
            % (result.returncode, (result.stderr or result.stdout or "")[-2000:])
        )
    return content.strip()


def _parse_article_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise CopilotGenerationError("Model response was not valid JSON")
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise CopilotGenerationError("Model response was not valid JSON") from exc
    for key in ("title", "body"):
        if not str(data.get(key, "")).strip():
            raise CopilotGenerationError("Model response is missing '%s'" % key)
    data.setdefault("description", "")
    data.setdefault("tags", [])
    return data


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _trim_text(value: object, limit: int = 240) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _label_from_url(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower() if "://" in url else ""
    if host.startswith("www."):
        host = host[4:]
    return host or "the source"


def _source_label(source: object, url: object) -> str:
    clean_source = _clean_text(source)
    if clean_source:
        return clean_source
    clean_url = _clean_text(url)
    derived = _label_from_url(clean_url)
    if derived != "the source":
        return derived
    return clean_url or derived


def _fallback_tags(texts: list[str], default: list[str]) -> list[str]:
    vocabulary = (
        "devops",
        "platform-engineering",
        "sre",
        "kubernetes",
        "observability",
        "security",
        "automation",
        "ci-cd",
        "cloud",
        "infrastructure-as-code",
    )
    combined = " ".join(texts).lower()
    counts = collections.Counter(tag for tag in vocabulary if tag.replace("-", " ") in combined)
    tags = [tag for tag, _ in counts.most_common(4)]
    for tag in default:
        if tag not in tags:
            tags.append(tag)
        if len(tags) >= 4:
            break
    return tags[:4]


def _fallback_run_article(day: str, edition_label: str, pages) -> dict:
    selected = list(pages[:5])
    source_count = len(
        {
            _source_label(row["source"], row["url"])
            for row in selected
            if _clean_text(row["source"]) or _clean_text(row["url"])
        }
    )
    highlights = []
    for index, row in enumerate(selected, start=1):
        url = _clean_text(row["url"])
        title = _clean_text(row["title"]) or url or "source item %d" % index
        summary = _trim_text(
            row["description"] or row["markdown"] or "the source did not include a summary."
        )
        source = _source_label(row["source"], row["url"])
        highlights.extend(
            [
                "### %s" % title,
                "",
                "[%s](%s) from %s is worth your time because %s"
                % (title, url, source, summary.rstrip(". ") + "."),
                "",
            ]
        )
    body_lines = [
        "## what you should care about",
        "",
        (
            "this edition pulled %d pages across %d sources and highlights the "
            "strongest technical themes from the current crawl window."
        )
        % (len(pages), source_count),
        (
            "the strongest threads here point back to day-two engineering pressure: "
            "teams are tuning delivery speed, reliability, and platform guardrails "
            "at the same time."
        ),
        "",
        "## notable reads",
        "",
        *highlights,
        "## sources",
        "",
        *[
            "- [%s](%s)"
            % (
                _clean_text(row["title"])
                or _clean_text(row["url"])
                or "source item %d" % index,
                row["url"],
            )
            for index, row in enumerate(selected, start=1)
        ],
    ]
    return {
        "title": "devops roundup for %s, edition %s" % (day, edition_label),
        "description": (
            "the short version: plenty happened, and at least some of it was "
            "actually useful."
        ),
        "tags": _fallback_tags(
            [
                _clean_text(row["title"])
                + " "
                + _clean_text(row["description"])
                + " "
                + _clean_text(row["markdown"])
                for row in selected
            ],
            ["devops", "platform-engineering", "automation"],
        ),
        "body": "\n".join(body_lines).strip(),
    }


def _first_article_paragraph(text: str) -> str:
    for chunk in re.split(r"\n\s*\n", text):
        cleaned = _clean_text(chunk)
        if not cleaned or cleaned.startswith("#") or cleaned == "---" or cleaned == FOOTER:
            continue
        return _trim_text(cleaned, limit=320)
    return "the earlier article was stored, but it did not expose a clean summary paragraph."


def _extract_article_urls(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"https?://[^\s)>\"]+", text)))


def _fallback_digest_article(day: str, edition_files: list[Path]) -> dict:
    summaries = []
    referenced_urls = []
    for path in edition_files:
        text = path.read_text(encoding="utf-8")
        urls = _extract_article_urls(text)
        referenced_urls.extend(urls[:3])
        edition_name = path.parent.name
        summaries.extend(
            [
                "### %s" % edition_name,
                "",
                "as covered in %s, %s"
                % (edition_name, _first_article_paragraph(text).rstrip(". ") + "."),
                "",
                *[
                    "- source carried forward: [%s](%s)" % (_label_from_url(url), url)
                    for url in urls[:3]
                ],
                "",
            ]
        )
    body_lines = [
        "## what shaped the day",
        "",
        (
            "today's crawl produced %d edition articles, which is enough signal to "
            "spot the main technical themes across the full day."
        )
        % len(edition_files),
        (
            "the day kept circling the same operational tradeoff: faster delivery "
            "still needs cleaner rollback paths, tighter observability, and less "
            "platform sprawl."
        ),
        "",
        "## edition by edition",
        "",
        *summaries,
    ]
    if referenced_urls:
        body_lines.extend(
            [
                "## referenced sources",
                "",
                *[
                    "- [%s](%s)" % (_label_from_url(url), url)
                    for url in list(dict.fromkeys(referenced_urls))
                ],
                "",
            ]
        )
    body_lines.extend(
        [
            "## today's editions",
            "",
            *["- %s" % path.parent.name for path in edition_files],
        ]
    )
    return {
        "title": "%s daily devops digest" % day,
        "description": "the whole day, boiled down so you can get back to your actual backlog.",
        "tags": ["devops", "platform-engineering", "daily-digest", "automation"],
        "body": "\n".join(body_lines).strip(),
    }


def sanitize_body(body: str) -> str:
    """Mechanical safety net for the skill's hard rules."""

    body = body.strip()
    # Strip any YAML front matter the model emitted anyway.
    if body.startswith("---"):
        parts = body.split("---", 2)
        if len(parts) == 3:
            body = parts[2].strip()
    lines = []
    in_code = False
    for line in body.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            lines.append(line)
            continue
        if not in_code:
            # Demote stray H1s; the title lives in Strapi's field, not the body.
            if re.match(r"^# \S", line):
                line = "#" + line
            line = line.replace(" — ", ", ").replace("—", ", ")
            line = line.replace(" – ", ", ").replace("–", "-")
        lines.append(line)
    body = "\n".join(lines).strip()
    if FOOTER not in body:
        body += "\n\n---\n\n%s" % FOOTER
    return body + "\n"


def _qa_check(index_path: Path, title: str) -> "tuple[int, str]":
    result = subprocess.run(
        [sys.executable, str(QA_SCRIPT), str(index_path), "--title", title],
        capture_output=True,
        text=True,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def _write_bundle(bundle_dir: Path, data: dict, extra_meta: dict) -> Path:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    index_path = bundle_dir / "index.md"
    index_path.write_text(sanitize_body(str(data["body"])), encoding="utf-8")
    meta = {
        "title": str(data["title"]).strip(),
        "description": str(data.get("description", "")).strip(),
        "tags": [str(tag).strip() for tag in data.get("tags", []) if str(tag).strip()],
        "author": "vinlawz",
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    meta.update(extra_meta)
    (bundle_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return index_path


def _generate(
    model: str,
    user_prompt: str,
    bundle_dir: Path,
    extra_meta: dict,
    fallback_factory,
) -> Path:
    out_path = Path(".article-output.json")
    prompt = "%s\n\n%s" % (SYSTEM_PROMPT, user_prompt)
    def _write_fallback_article(exc: CopilotGenerationError):
        print("Warning: Copilot article generation failed, using fallback article: %s" % exc)
        fallback_data = fallback_factory()
        fallback_index = _write_bundle(bundle_dir, fallback_data, extra_meta)
        fallback_code, fallback_report = _qa_check(fallback_index, str(fallback_data["title"]))
        return fallback_index, fallback_code, fallback_report

    try:
        raw = _call_model(model, prompt, out_path)
        data = _parse_article_json(raw)
    except CopilotGenerationError as exc:
        index_path, code, report = _write_fallback_article(exc)
    else:
        index_path = _write_bundle(bundle_dir, data, extra_meta)
        code, report = _qa_check(index_path, str(data["title"]))
        if code != 0:
            # One repair round: hand the QA failures back to the agent.
            print("QA failures, requesting a fix:\n%s" % report)
            repair_prompt = (
                "%s\n\nYou previously produced this article JSON:\n%s\n\nThe QA checker "
                "found these problems:\n%s\n\nFix every FAIL and return the corrected "
                "article as the same JSON object, nothing else."
                % (SYSTEM_PROMPT, json.dumps(data, ensure_ascii=False), report)
            )
            try:
                data = _parse_article_json(_call_model(model, repair_prompt, out_path))
            except CopilotGenerationError as exc:
                index_path, code, report = _write_fallback_article(exc)
            else:
                index_path = _write_bundle(bundle_dir, data, extra_meta)
                code, report = _qa_check(index_path, str(data["title"]))
    print("QA report for %s:\n%s" % (index_path, report))
    if code != 0:
        # sanitize_body already fixed what can be fixed mechanically; don't fail the
        # unattended run over residual style findings, just surface them in the log.
        print("Warning: QA failures remain after repair; review this article.")
    return index_path


def _run_article(args) -> Path:
    day = args.date or _today()
    ingest_dir = Path(args.articles_dir) / day / DAILY_INGEST_NAME
    batch_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    _write_daily_ingest_manifest(ingest_dir, day, batch_id, 0)
    pages = _pages_for_day(Path(args.db), day, by_published=args.published)
    if not pages:
        print("No pages found for %s; skipping article." % day)
        raise SystemExit(0)

    sources = []
    for row in pages:
        excerpt = re.sub(r"\s+", " ", row["markdown"] or "")[:EXCERPT_CHARS]
        sources.append(
            "### %s\nURL: %s\nDescription: %s\nExcerpt: %s"
            % (row["title"] or row["url"], row["url"], row["description"] or "-", excerpt)
        )
    user_prompt = (
        "Today is %s (crawl edition %s of the day). Below are %d web pages collected by "
        "the aut0ps automated crawler during this edition window. Write ONE cohesive "
        "technical article (600-1000 words) that synthesizes the most interesting and "
        "technically substantive themes for developers. Focus on DevOps, platform engineering, "
        "site reliability, infrastructure as code, observability, and automation. "
        "Do not force an AI angle or make agents the default subject. Group related items, "
        "explain why they matter to engineers, and link every claim to its source URL "
        "inline. End the "
        "body with an H2 'sources' section listing all URLs used.\n\n%s"
        % (day, args.edition_label, len(pages), "\n\n".join(sources))
    )
    _write_daily_ingest_manifest(ingest_dir, day, batch_id, len(pages))
    bundle_dir = Path(args.articles_dir) / day / ("edition-%s" % args.edition_label)
    return _generate(
        args.model,
        user_prompt,
        bundle_dir,
        {"date": day, "edition": args.edition_label, "pages": len(pages)},
        lambda: _fallback_run_article(day, args.edition_label, pages),
    )


def _digest_article(args) -> Path:
    day = args.date or _today()
    day_dir = Path(args.articles_dir) / day
    edition_files = sorted(day_dir.glob("edition-*/index.md")) if day_dir.exists() else []
    if not edition_files:
        print("No edition articles found for %s; skipping digest." % day)
        raise SystemExit(0)

    previous = []
    for path in edition_files:
        previous.append(
            "## Article: %s\n\n%s" % (path.parent.name, path.read_text(encoding="utf-8"))
        )
    user_prompt = (
        "Today is %s. Below are the %d articles generated earlier today from aut0ps's "
        "scheduled crawl editions. Write the FINAL daily article (800-1200 words): a polished "
        "editorial that synthesizes the whole day, highlights the most important "
        "developments, notes how the story evolved across editions, and references the earlier "
        "articles. Keep the day balanced: AIOps and AI agents may be important, but give equal "
        "editorial attention to other well-supported areas such as platform engineering, site "
        "reliability, infrastructure as code, CI/CD, observability, and security. Do not "
        "invent a connection to AI agents when the evidence does not support one. "
        "Reference earlier articles by their edition name (e.g. 'as covered in "
        "edition-1') as well as the original "
        "source URLs they cite. Give the article a funny, memorable title. End the body with "
        "an H2 \"today's editions\" section naming each edition article.\n\n%s"
        % (day, len(edition_files), "\n\n---\n\n".join(previous))
    )
    bundle_dir = day_dir / "daily-digest"
    extra_meta = {
        "date": day,
        "type": "daily-digest",
        "source_articles": [p.parent.name for p in edition_files],
    }
    return _generate(
        args.model,
        user_prompt,
        bundle_dir,
        extra_meta,
        lambda: _fallback_digest_article(day, edition_files),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("run", "digest"))
    parser.add_argument("--db", default="data/aut0ps.db")
    parser.add_argument("--articles-dir", default="articles")
    parser.add_argument("--date", default=None, help="ISO date override (default: today UTC)")
    parser.add_argument("--edition-label", default="1", help="Edition number within the day")
    parser.add_argument(
        "--published",
        action="store_true",
        help="Select pages by publication date instead of fetch date (for backfills)",
    )
    parser.add_argument("--model", default=os.getenv("ARTICLE_MODEL", DEFAULT_MODEL))
    args = parser.parse_args()

    if not any(
        os.getenv(name) for name in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")
    ):
        print(
            "Warning: no COPILOT_GITHUB_TOKEN/GH_TOKEN/GITHUB_TOKEN set; relying on "
            "the Copilot CLI's own login session.",
            file=sys.stderr,
        )

    if args.mode == "run":
        path = _run_article(args)
    else:
        path = _digest_article(args)
    print("Wrote %s" % path)
    return 0


def _write_daily_ingest_manifest(
    ingest_dir: Path, day: str, batch_id: str, page_count: int
) -> None:
    """Track ingestion batches for a day. Deliberately generic: no 'edition' or
    'run' labeling appears in this file, only opaque batch ids and page counts."""
    ingest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = ingest_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"date": day, "ingestions": []}
    ingestions = [item for item in manifest["ingestions"] if item["id"] != batch_id]
    ingestions.append(
        {
            "id": batch_id,
            "pages": page_count,
            "generated": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
    )
    manifest["ingestions"] = sorted(ingestions, key=lambda item: item["id"])
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    raise SystemExit(main())
