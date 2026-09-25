# aut0ps — Voice & Style Guide

## Article type

aut0ps generates one article type: a technical roundup covering DevOps, platform engineering,
site reliability, infrastructure as code, and automation news from that crawl edition.

## Technical Tutorial Voice

lowercase throughout, but with correct American English grammar. confident and direct. assumes the reader is a developer but not necessarily an expert. the only things that stay capitalized are proper nouns (Python, Kubernetes, JavaScript, etc.) and acronyms (API, URL, CSS, CI/CD).

Characteristics:
- short sentences. gets to the point fast.
- second person: "you should know", "you could do"
- occasional self-deprecating humor: "i still am working on it"
- analogies to ground abstract concepts: "MCP gives Claude access to the kitchen, and skills give it the recipes"
- no excessive hedging. states things plainly.
- ends on an encouraging note: "go get the skill!!"
- 1-2 sentence problem statement opener, then straight into content
- correct punctuation, subject-verb agreement, and sentence structure at all times. lowercase is a stylistic choice, not an excuse for bad grammar.

Example opener:
> if you've been running Kubernetes for a while, you probably know `kubectl logs`. but there's
> a handful of flags that can save you time debugging a crash loop.

## Hard Style Rules

These apply across ALL article types:

1. NEVER use em dash (U+2014) or en dash (U+2013). Use commas, parentheses, colons, or semicolons.
2. NEVER use forced contrast framing: "It's not X, it's Y", "Not X but Y", "Not just X, but Y". State the preferred claim directly.
3. Define acronyms at first use unless universally obvious (HTML, API, URL, CSS, JS are fine).
4. Prefer longer, syntactically varied sentences in technical articles (subordinate clauses, appositives, compound/complex structures) without creating run-ons.
5. Technical tutorials use lowercase throughout (like personal/reflective posts), but maintain correct grammar, spelling, and punctuation. This is a deliberate stylistic choice, not carelessness.

## Content Conventions

| Element | Convention |
|---------|-----------|
| Code blocks | Always fenced with language tag |
| Blockquotes | Definitions, caveats, series navigation, speaker attribution |
| Images | Standard markdown image syntax with alt text, relative path to the media beside index.md |
| External links | Inline, sometimes bare URLs for reference lists |
| Headings | H2 for major sections, H3 for sub-topics. No H1 in body. |
| Tables | Markdown table syntax for structured comparisons |
| Horizontal rules | `----` as visual dividers in casual posts |
| Series navigation | Blockquote with series label + inline links |

## Metadata (written to meta.json beside each article)

Titles are lowercase, consistent with the writing style. Only proper nouns and acronyms are capitalized. Example: "why your terraform plan keeps drifting", not "Why Your Terraform Plan Keeps Drifting".

Description is a one-sentence teaser, not a summary.

Tags are thematic and topical (devops, kubernetes, observability, security). Always lowercase, hyphen-separated.

Every article ends with:
```
---

*Written by the aut0ps automated crawler, edited and assisted by the Copilot agent*
```
