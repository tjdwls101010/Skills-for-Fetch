"""HTML -> clean markdown (trafilatura), plus an optional query-filtered companion
(rank_bm25). Fetch-venv only.

Do NOT import crawl4ai's own BM25 filter from here — even though it would save writing
a second BM25 call site, it would pull crawl4ai (and its lxml~=5.3 pin) into the fetch
venv's scrapling (lxml>=6.1.1) world and reintroduce the exact conflict the two-venv
split exists to avoid (G-lxml). `rank_bm25` is a tiny, dependency-free reimplementation
that has no opinion about lxml at all — that's why it's the one used on this side.
"""

from __future__ import annotations

import re

import trafilatura
from rank_bm25 import BM25Okapi

_WORD_RE = re.compile(r"[a-z0-9]+")


def extract(html: str, output_format: str = "markdown") -> str | None:
    """The one trafilatura call site. `output_format` is any value trafilatura
    accepts (markdown/html/txt/json/...) — fetch.py's --format flag maps straight
    through to this. Returns None on failure (trafilatura never raises on bad/thin
    input), which callers treat as "nothing extractable" rather than a crash."""
    return trafilatura.extract(
        html,
        output_format=output_format,
        include_tables=True,
        include_comments=False,
    )


def html_to_markdown(html: str) -> str | None:
    """The validation gate always checks markdown regardless of the user's requested
    --format, so escalation decisions don't depend on which output format was asked
    for — this is just `extract(html, "markdown")` under a clearer name at call
    sites that only ever want markdown."""
    return extract(html, output_format="markdown")


def extract_title(html: str) -> str | None:
    """trafilatura's own metadata extractor — more robust than a hand-rolled
    ``<title>`` regex (it falls back to OpenGraph/meta tags when a bare ``<title>``
    is missing or empty)."""
    metadata = trafilatura.extract_metadata(html)
    return metadata.title if metadata and metadata.title else None


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def query_filter(markdown: str, query: str, top_n: int = 10) -> str:
    """Ranks paragraph-level chunks of already-extracted markdown against `query` with
    BM25 — the same idea as built-in WebFetch's "relevant part" prompt, but produced as
    an explicit, inspectable companion rather than a silent truncation the caller can't
    see past. Returns the untouched markdown when there aren't enough paragraphs to
    rank meaningfully, so a short page never gets chopped down further."""
    paragraphs = [p.strip() for p in markdown.split("\n\n") if p.strip()]
    if len(paragraphs) <= top_n:
        return markdown
    tokenized_corpus = [_tokenize(p) for p in paragraphs]
    bm25 = BM25Okapi(tokenized_corpus)
    scores = bm25.get_scores(_tokenize(query))
    ranked = sorted(zip(paragraphs, scores), key=lambda pair: pair[1], reverse=True)
    top = [p for p, score in ranked[:top_n] if score > 0]
    return "\n\n".join(top) if top else markdown
