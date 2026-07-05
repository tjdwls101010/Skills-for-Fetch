#!/usr/bin/env python3
"""uf crawl <url> [opts] — thin CLI, runs in the `crawl` venv (crawl4ai).

Deep-crawls from a seed URL, saving one markdown file per page plus a single
manifest.json. Never prints per-page previews — an N-page crawl printing N previews
would blow the context budget the whole family exists to protect (§11); stdout gets
only the manifest summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import save


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="uf crawl", description="Deep-crawl a site to markdown files + a manifest.")
    parser.add_argument("url", help="seed URL to start crawling from")
    parser.add_argument("--max-pages", type=int, default=20,
                         help="stop after this many pages (default 20 — personal scale, not mass harvesting)")
    parser.add_argument("--max-depth", type=int, default=3, help="max link-follow depth from the seed (default 3)")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between requests (default 1.0)")
    parser.add_argument("--concurrency", type=int, default=2, help="max concurrent page fetches (default 2)")
    parser.add_argument("--respect-robots", action="store_true", dest="respect_robots",
                         help="honor robots.txt (default: ignored — the point is reaching soft-blocked sites; "
                              "still public content at personal scale, never mass harvesting)")
    return parser.parse_args(argv)


async def run_crawl(args: argparse.Namespace) -> dict:
    from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
    from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

    strategy = BFSDeepCrawlStrategy(max_depth=args.max_depth, max_pages=args.max_pages)
    config = CrawlerRunConfig(
        deep_crawl_strategy=strategy,
        check_robots_txt=args.respect_robots,
        mean_delay=args.delay,
        max_range=0.0,
        semaphore_count=args.concurrency,
    )

    domain = urlsplit(args.url).netloc.lower() or "unknown-host"
    ordinals = save.OrdinalAllocator()
    entries = []
    seen_urls: set[str] = set()

    async with AsyncWebCrawler() as crawler:
        results = await crawler.arun(args.url, config=config)
        for result in results:
            if not getattr(result, "success", True):
                continue
            # BFSDeepCrawlStrategy can rediscover the seed (or another already-visited
            # page) via a nav link before its own depth-based dedup catches it — skip
            # anything already saved this run rather than write the same page twice
            # under two different ordinals.
            if result.url in seen_urls:
                continue
            content = result.markdown.raw_markdown if result.markdown else None
            if not content:
                continue
            seen_urls.add(result.url)
            title = (result.metadata or {}).get("title")
            ordinal = ordinals.next()
            page_path = save.crawl_page_path(result.url, ordinal, domain)
            save.write_output(page_path, content)
            entries.append({"url": result.url, "title": title, "path": str(page_path)})

    manifest_path = save.write_manifest(domain, entries)
    return save.crawl_result_json(
        seed_url=args.url,
        domain=domain,
        manifest_path=manifest_path,
        page_count=len(entries),
        output_dir=manifest_path.parent,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = asyncio.run(run_crawl(args))
    except Exception as exc:  # CLI boundary: a clean message beats a raw traceback
        print(f"uf crawl: {exc}", file=sys.stderr)
        return 1

    if result["pages_saved"] == 0:
        print("uf crawl: no pages saved (seed URL may be unreachable, or entirely filtered/disallowed)", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
