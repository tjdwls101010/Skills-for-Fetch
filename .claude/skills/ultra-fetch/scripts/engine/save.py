"""Output path, slug, preview, and manifest conventions shared by fetch.py and crawl.py.

Stdlib only — no scrapling/crawl4ai/trafilatura imports here. fetch.py runs in the
`fetch` venv and crawl.py runs in the `crawl` venv (see G-lxml); this module has to be
importable from both without dragging either venv's heavy deps into the other.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_OUTPUT_ROOT = "./.tmp/ultra-fetch"
_HEADER_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)


def domain_of(url: str) -> str:
    """Registrable host for the output subdir. Falls back to a literal placeholder for
    schemeless/relative input so a bad URL still produces a usable path instead of a
    KeyError-shaped crash — and for a netloc that's entirely dots, since
    `urlsplit("https://../../../etc/foo").netloc == ".."` (confirmed live: everything
    after the first `..` segment collapses into `.path`, not `.netloc`), and joining
    that straight into an output path (`Path(root) / ".."`) climbs one level OUT of
    the intended `./.tmp/ultra-fetch/<domain>/` root entirely. A netloc of only dots
    is never a real hostname, so it's treated the same as "no host at all"."""
    netloc = urlsplit(url).netloc.lower()
    if not netloc or set(netloc) <= {"."}:
        return "unknown-host"
    return netloc


def slugify(url: str, max_len: int = 80) -> str:
    """path+query -> filesystem-safe slug, always suffixed with an 8-char hash of the
    full URL so two different URLs that slugify to the same text (e.g. differing only
    by a dropped query param after sanitization) never collide on disk."""
    parts = urlsplit(url)
    tail = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
    base = tail.lower().lstrip("/")
    base = re.sub(r"[^a-z0-9-]+", "-", base)
    base = re.sub(r"-{2,}", "-", base).strip("-")
    if not base:
        base = "index"
    base = base[:max_len].rstrip("-")
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    return f"{base}-{url_hash}"


def fetch_output_path(url: str, root: str | Path = DEFAULT_OUTPUT_ROOT, ext: str = "md") -> Path:
    return Path(root) / domain_of(url) / f"{slugify(url)}.{ext}"


def crawl_page_path(url: str, ordinal: int, domain: str, root: str | Path = DEFAULT_OUTPUT_ROOT, ext: str = "md") -> Path:
    return Path(root) / domain / "crawl" / f"{ordinal:03d}-{slugify(url)}.{ext}"


def crawl_manifest_path(domain: str, root: str | Path = DEFAULT_OUTPUT_ROOT) -> Path:
    return Path(root) / domain / "crawl" / "manifest.json"


class OrdinalAllocator:
    """Assigns crawl page numbers by discovery order. crawl4ai processes pages
    concurrently, so a plain incrementing int would race between coroutines/threads;
    this makes "next number" an atomic operation so the manifest's ordinals never
    collide or skip."""

    def __init__(self) -> None:
        self._next = 0
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            n = self._next
            self._next += 1
            return n


def section_count(markdown: str) -> int:
    """Number of ATX headers (# .. ######) in the extracted markdown — a cheap, well
    defined proxy for "how much structure is in this page", printed in fetch metadata
    so a caller can sanity-check a suspiciously flat/short extraction before reading
    the file."""
    return len(_HEADER_RE.findall(markdown))


def write_output(path: str | Path, content: str) -> int:
    """Writes the file (markdown, html, text, or json — whatever --format produced),
    creating parent dirs, and returns the byte count written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8")
    path.write_bytes(data)
    return len(data)


def fetch_result_json(
    *,
    url: str,
    final_url: str,
    title: str | None,
    rung: str,
    saved_path: str | Path | None,
    content: str,
    preview_chars: int = 500,
) -> dict:
    """The ONLY thing fetch.py prints on success (§6: never the full body). `saved_path`
    is None for --no-save runs (still reports metadata + preview, just no file).
    `sections` (ATX header count) is a markdown-specific proxy — it's simply 0 for
    other --format values, which is honest rather than special-cased."""
    return {
        "url": url,
        "final_url": final_url,
        "title": title,
        "rung": rung,
        "saved_path": str(saved_path) if saved_path else None,
        "bytes": len(content.encode("utf-8")),
        "sections": section_count(content),
        "preview": content[:preview_chars],
    }


def write_manifest(domain: str, entries: list[dict], root: str | Path = DEFAULT_OUTPUT_ROOT) -> Path:
    """`entries`: list of {"url", "title", "path"} in discovery order. One manifest per
    crawl, at the fixed path crawl_manifest_path() computes — never per-page."""
    path = crawl_manifest_path(domain, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "page_count": len(entries),
        "pages": entries,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def crawl_result_json(*, seed_url: str, domain: str, manifest_path: str | Path, page_count: int, output_dir: str | Path) -> dict:
    """The ONLY thing crawl.py prints on success — a summary, never per-page previews
    (N previews for an N-page crawl would blow the context budget the whole family
    exists to protect)."""
    return {
        "seed_url": seed_url,
        "domain": domain,
        "pages_saved": page_count,
        "manifest_path": str(manifest_path),
        "output_dir": str(output_dir),
    }
