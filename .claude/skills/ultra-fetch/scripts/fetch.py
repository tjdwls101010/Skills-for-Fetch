#!/usr/bin/env python3
"""uf fetch <url> [opts] — thin CLI, runs in the `fetch` venv.

Parses args and prints results only; every real decision (escalation, validation,
conversion, saving) lives in engine/. Never prints the full saved content unless
--print is passed explicitly — metadata + a short preview is the whole point (see
engine/save.py and OVERVIEW.md's context-frugality rule).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import escalate, routes, save
from engine import markdown as markdown_mod

FORMAT_EXT = {"markdown": "md", "html": "html", "text": "txt", "json": "json"}
# trafilatura's own output_format vocabulary calls plain text "txt", not "text" —
# translated here so the CLI can use the word a user actually expects (per the plan's
# decided --format markdown|html|text|json surface) without leaking trafilatura's
# internal naming into it.
FORMAT_TO_TRAFILATURA = {"markdown": "markdown", "html": "html", "text": "txt", "json": "json"}


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="uf fetch", description="Fetch one URL to a clean, saved file.")
    parser.add_argument("url")
    parser.add_argument("--out", help="output file path (default: ./.tmp/ultra-fetch/<domain>/<slug>.<ext>)")
    parser.add_argument("--mode", choices=["auto", "fast", "browser", "stealth"], default="auto",
                         help="escalation rung; default auto climbs fast->browser->stealth as needed")
    parser.add_argument("--query", help="also save a rank_bm25-filtered companion file relevant to this query")
    parser.add_argument("--format", choices=["markdown", "html", "text", "json"], default="markdown")
    parser.add_argument("--profile", help="reuse a logged-in Tier-1 profile from `uf login` — forces the browser rung")
    parser.add_argument("--capture-xhr", metavar="REGEX", dest="capture_xhr",
                         help="capture matching backend XHR/fetch responses — forces the browser rung")
    parser.add_argument("--scroll", type=int, metavar="N",
                         help="scroll N times before reading the page (SPA pagination) — forces the browser rung")
    parser.add_argument("--timeout", type=int, default=30000, help="per-rung timeout in ms (default 30000)")
    parser.add_argument("--no-save", action="store_true", help="don't write a file — print metadata + preview only")
    parser.add_argument("--print", dest="print_full", action="store_true",
                         help="also print the full content to stdout (defeats context-frugality on purpose — use sparingly)")
    args = parser.parse_args(argv)

    if args.mode in ("fast", "stealth") and (args.profile or args.capture_xhr or args.scroll):
        parser.error(
            f"--mode {args.mode} is incompatible with --profile/--capture-xhr/--scroll "
            "(they require and are pinned to the browser rung)"
        )
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    route = routes.classify(args.url)
    if route:
        print(f"note: {route['reason']}", file=sys.stderr)

    # Everything past this point is a CLI boundary: any failure — a network error, an
    # unreachable host, a bad --capture-xhr regex, a concurrent --profile lock
    # conflict, an invalid --profile name — becomes a clean one-line message rather
    # than a raw multi-frame traceback, matching crawl.py/login.py's own handling.
    try:
        attempt = escalate.run_ladder(
            args.url,
            mode=args.mode,
            profile=args.profile,
            capture_xhr=args.capture_xhr,
            scroll=args.scroll,
            query=args.query,
            timeout=args.timeout,
        )

        if args.format == "markdown":
            content = attempt.markdown
        else:
            content = markdown_mod.extract(attempt.html, output_format=FORMAT_TO_TRAFILATURA[args.format])
            if content is None:
                print("uf fetch: content extraction failed for the requested --format", file=sys.stderr)
                return 1

        saved_path = None
        if not args.no_save:
            ext = FORMAT_EXT[args.format]
            out_path = Path(args.out) if args.out else save.fetch_output_path(args.url, ext=ext)
            save.write_output(out_path, content)
            saved_path = out_path

            if args.query:
                # Always markdown regardless of --format: query_filter ranks
                # attempt.markdown's paragraphs, so the companion's own extension
                # must say .md even when the primary file is .json/.html/.txt.
                companion = markdown_mod.query_filter(attempt.markdown, args.query)
                companion_path = out_path.with_name(f"{out_path.stem}.query.md")
                save.write_output(companion_path, companion)

            if attempt.captured:
                # The whole point of --capture-xhr is backend JSON the rendered page
                # never shows in its markdown — capturing it and only reporting a
                # count would silently throw away the one thing this flag exists to
                # reach.
                captured_path = out_path.with_name(f"{out_path.stem}.captured.json")
                save.write_output(captured_path, json.dumps(attempt.captured, indent=2, ensure_ascii=False))

        result = save.fetch_result_json(
            url=args.url,
            final_url=attempt.final_url,
            title=markdown_mod.extract_title(attempt.html),
            rung=attempt.rung,
            saved_path=saved_path,
            content=content,
        )
        if attempt.captured:
            result["captured_count"] = len(attempt.captured)
            if saved_path:
                result["captured_path"] = str(saved_path.with_name(f"{saved_path.stem}.captured.json"))
    except Exception as exc:
        print(f"uf fetch: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.print_full:
        print("\n--- full content ---\n")
        print(content)

    return 0


if __name__ == "__main__":
    sys.exit(main())
