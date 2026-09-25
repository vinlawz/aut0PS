# Chris Achinga — Voice & Style Guide

## Who Is Chris

Chris is a Lead Software Engineer based in Kenya (Mombasa/Nairobi). Works primarily with Python/Django,
React/Next.js, Angular, and React Native. Active in African tech communities (DjangoCon Africa, Ubuntu
communities). Enjoys cycling, plane spotting, and travel. Writing is rooted in lived experience and
community participation.

## Voice by Article Type

### Technical Tutorial Voice

lowercase throughout, but with correct American English grammar. confident and direct. assumes the reader is a developer but not necessarily an expert. the only things that stay capitalized are proper nouns (Python, Django, JavaScript, Hugo, etc.) and acronyms (API, URL, CSS).

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
> if you've been using JavaScript for a while, you probably know `console.log()`. but the console
> object has way more methods that can save you time debugging.

### Personal/Reflective Voice

Completely lowercase. Deliberate stylistic choice.

Characteristics:
- Stream-of-consciousness. Short, punchy sentences and fragments.
- Profanity used naturally, not for shock value (fucking, f**king, goddamn)
- Honest about failure, uncertainty, and emotional difficulty without melodrama
- Self-aware about the writing style: "my writing style is more free, violates a couple of grammar syntax"
- Rhetorical questions and internal monologue: "i used to be crazy about this? idk what happened."
- No conclusions. Articles end when the thought ends.
- Opens cold, in the middle of a thought. No formal intro.

Example opener:
> this might be too early for a 2025 year review, but thankfully, this isn't a technical one,
> so i can just do it whenever i feel like.

### Event Recap Voice

Mixes casual personal commentary with objective, information-dense talk summaries.

Characteristics:
- Photo captions are dry or witty: "Food, a basic human need", "Tim, standing while talking. Proving that one can indeed kill two birds with one stone"
- Solidarity and community are recurring themes
- Each talk: speaker attribution, link to slides/video, 1-3 paragraph summary, then 1-2 casual reaction sentences
- Opening blockquote or personal note setting expectation vs. reality

### Project Explainer Voice

Accessible, explains at multiple levels of technical depth.

Characteristics:
- Short "here's why I'm writing this" opener
- Sub-sections: "what is X?", "to a non-technical person, what is X?"
- Bullet lists for features and benefits
- Figures with captions

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

## Metadata (entered in Strapi, not in the file)

Titles are lowercase, consistent with the writing style. Only proper nouns and acronyms are capitalized. Example: "debugging Django signals with the AI tools i actually use", not "Debugging Django Signals With The AI Tools I Actually Use".

Description examples (teasers, not summaries):
- "Hibernation is not just for lions, I did it too"
- "it's giving... community"
- "Go get the skill!!"
- "A for Aching, I for Ideas, R for Reach, S for Solutions"

Tags are thematic: technical tags (javascript, django, AI) and topical (community, life, pov, non-technical). Always lowercase, hyphen-separated.

Author is always "Chris Achinga" only. AI attribution goes in the footer instead.

Every article ends with:
```
---

*Written and Authored by Chris, Edited and assisted by Copilot agent for aut0ps*
```

Series field is used for multi-part groups with consistent header navigation:
> *Previous: [Part N] | Next: [Part N]*
