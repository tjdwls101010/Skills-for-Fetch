#!/usr/bin/env python3
"""uf login <site> [--url START_URL] — headed onboarding for a Tier-1 profile.

Thin CLI, runs in the `fetch` venv (needs scrapling for the browser). All real logic
is in engine/session.py — this just parses args and reports the outcome.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import session as session_mod

# A handful of common Tier-1 (cookie-auth, non-fortified) sites so `uf login reddit`
# just works without also requiring --url. Anything else needs --url on first use —
# this is a convenience list, not an allowlist; any site can be logged into.
DEFAULT_START_URLS = {
    "reddit": "https://www.reddit.com/login/",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="uf login", description="Headed login into a dedicated Tier-1 profile.")
    parser.add_argument("site", help="a short name for this profile, e.g. 'reddit' — reused later as --profile <site>")
    parser.add_argument("--url", help="the login page to open (required unless `site` has a built-in default)")
    args = parser.parse_args(argv)

    if not args.url:
        args.url = DEFAULT_START_URLS.get(args.site.lower())
        if not args.url:
            parser.error(f"--url is required for '{args.site}' (no built-in default login URL for it)")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        session_mod.login_flow(args.site, args.url)
    except (session_mod.ProfileLockError, ValueError) as exc:
        print(f"uf login: {exc}", file=sys.stderr)
        return 1
    print(f"Saved. Use --profile {args.site} on `uf fetch`/`uf crawl` to reuse this login.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
