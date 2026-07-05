#!/usr/bin/env python3
"""uf setup / uf doctor — prereq bootstrap, venv+library install, health checks.

Runs under whatever `python3` is ambient on the machine (macOS ships one via the
Xcode Command Line Tools stub) — this is the one script that can't assume its own
venv exists yet, since it's the thing that creates the venvs (G-prereq). Everything
it provisions afterward lives in ~/.ultra-fetch/, never in the skill folder, so
moving/deleting the project never touches multi-hundred-MB browser installs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
HOME = Path(os.environ.get("ULTRA_FETCH_HOME", str(Path.home() / ".ultra-fetch")))
VENVS_DIR = HOME / "venvs"
PROFILES_DIR = HOME / "profiles"
LOCKS_DIR = HOME / "locks"
CONFIG_PATH = HOME / "config.json"

# scrapling>=0.4.9 is the validated floor (a looser one risks a silent backtrack to
# 0.2.99's ancient API — G-extras/G-lxml in references/engines.md).
FETCH_VENV_PACKAGES = ["scrapling[fetchers]>=0.4.9", "trafilatura", "rank_bm25"]
CRAWL_VENV_PACKAGES = ["crawl4ai"]


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kwargs)


def try_run(cmd: list[str], timeout: int = 15) -> tuple[bool, str]:
    """Executes and reports ok/fail without raising — used everywhere a failure is
    informative rather than fatal to the whole run."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0, (result.stdout + result.stderr).strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, str(exc)


def have_brew() -> bool:
    return shutil.which("brew") is not None


def ensure_uv() -> None:
    """uv is the one true prerequisite — it supplies Python 3.12 for the venvs, so a
    missing system Python never becomes a failure class. Detected by executing, not
    `which` (a `which`-only check can't tell a working install from a broken shebang
    — see G-yt for exactly that failure mode elsewhere)."""
    ok, _ = try_run(["uv", "--version"])
    if ok:
        print("[ok] uv present")
        return
    print("[missing] uv — installing...")
    if have_brew():
        run(["brew", "install", "uv"])
    else:
        print("No Homebrew found. Running the official installer:")
        run(["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"])
    ok, _ = try_run(["uv", "--version"])
    if not ok:
        print(
            "uv install failed. Install it manually, then re-run `uf setup`: "
            "https://docs.astral.sh/uv/getting-started/installation/",
            file=sys.stderr,
        )
        sys.exit(1)


def ensure_route_tool(name: str, brew_pkg: str | None, uv_tool: str | None) -> str:
    """gh/yt-dlp are optional — routes.md degrades gracefully without them, so a
    persistent failure here is reported, not fatal to setup as a whole. Health-checked
    by executing (G-yt): yt-dlp is known to silently break after a Homebrew Python
    upgrade orphans its shebang, so a `which yt-dlp` check would report "present" on
    an install that actually crashes on first use."""
    ok, out = try_run([name, "--version"])
    if ok:
        print(f"[ok] {name} present ({out.splitlines()[0] if out else ''})")
        return "ok"
    print(f"[broken/missing] {name}: {out or 'not found'}")
    try:
        if have_brew() and brew_pkg:
            run(["brew", "reinstall" if shutil.which(name) else "install", brew_pkg])
        elif uv_tool:
            run(["uv", "tool", "install", uv_tool])
    except subprocess.CalledProcessError:
        pass
    ok, out = try_run([name, "--version"])
    if ok:
        print(f"[ok] {name} fixed ({out.splitlines()[0] if out else ''})")
        return "ok"
    print(f"[degraded] {name} still unavailable — routes.md commands needing it won't work; core fetch/crawl are unaffected.")
    return "degraded"


def create_venv(name: str) -> Path:
    venv_path = VENVS_DIR / name
    if (venv_path / "bin" / "python").exists():
        print(f"[skip] {name} venv already exists at {venv_path}")
        return venv_path
    run(["uv", "venv", "--python", "3.12", str(venv_path)])
    return venv_path


def pip_install(venv_path: Path, *packages: str) -> None:
    run(["uv", "pip", "install", "--python", str(venv_path / "bin" / "python"), *packages])


def setup_fetch_venv() -> Path:
    venv_path = create_venv("fetch")
    pip_install(venv_path, *FETCH_VENV_PACKAGES)
    run([str(venv_path / "bin" / "scrapling"), "install"])
    return venv_path


def setup_crawl_venv() -> Path:
    venv_path = create_venv("crawl")
    pip_install(venv_path, *CRAWL_VENV_PACKAGES)
    run([str(venv_path / "bin" / "crawl4ai-setup")])
    return venv_path


def smoke_test_fetch(venv_path: Path) -> tuple[bool, str]:
    """Proves the fetch venv can do all three things ultra-fetch depends on: reach a
    real page over HTTP, convert it to markdown, AND drive a real browser session with
    working `capture_xhr` (plan §10 step 5's decided assertion — "capture wiring is
    functional... assert >=1 captured response"). A prior, weaker version of this
    smoke test only imported `DynamicFetcher`/`StealthyFetcher` without exercising
    them, which would have reported "fetch venv OK" straight through the exact
    regression this build already hit once: captured responses moved from
    `page.captured_xhr` to `response.captured_xhr` between scrapling releases with no
    warning. quotes.toscrape.com is a public site built specifically for scraper
    testing/practice, making it a stable, low-risk fixture for this round-trip."""
    py = str(venv_path / "bin" / "python")
    code = (
        "from scrapling.fetchers import Fetcher, DynamicFetcher, StealthyFetcher\n"
        "import trafilatura\n"
        "from rank_bm25 import BM25Okapi\n"
        "r = Fetcher.get('https://example.com', impersonate='chrome')\n"
        "assert r.status == 200, f'unexpected status {r.status}'\n"
        "md = trafilatura.extract(r.html_content, output_format='markdown')\n"
        "assert md, 'trafilatura extracted nothing'\n"
        "def _scroll(page):\n"
        "    for _ in range(2):\n"
        "        page.mouse.wheel(0, 15000)\n"
        "        page.wait_for_timeout(600)\n"
        "    return page\n"
        "br = DynamicFetcher.fetch('https://quotes.toscrape.com/scroll', headless=True,\n"
        "                          capture_xhr='api/quotes', page_action=_scroll, timeout=45000)\n"
        "assert br.captured_xhr, 'capture_xhr wiring produced zero captured responses'\n"
        "print(f'fetch venv OK (fast+browser+capture_xhr verified, {len(br.captured_xhr)} captured)')\n"
    )
    # 120s outer budget, not just the 45s inner DynamicFetcher timeout: this smoke
    # test has a real network + browser-launch dependency (quotes.toscrape.com plus
    # a cold Chromium start), and a tight budget risks reporting a false "FAIL" on a
    # slow connection that's indistinguishable from an actual capture_xhr regression.
    return try_run([py, "-c", code], timeout=120)


def smoke_test_crawl(venv_path: Path) -> tuple[bool, str]:
    py = str(venv_path / "bin" / "python")
    code = (
        "import asyncio\n"
        "from crawl4ai import AsyncWebCrawler\n"
        "from crawl4ai.deep_crawling import BFSDeepCrawlStrategy, BestFirstCrawlingStrategy\n"
        "async def main():\n"
        "    async with AsyncWebCrawler() as crawler:\n"
        "        result = await crawler.arun('https://example.com')\n"
        "        assert result.markdown.raw_markdown, 'crawl4ai extracted nothing'\n"
        "asyncio.run(main())\n"
        "print('crawl venv OK')\n"
    )
    return try_run([py, "-c", code], timeout=60)


def resolved_version(venv_path: Path, *candidate_names: str) -> str | None:
    py = str(venv_path / "bin" / "python")
    for name in candidate_names:
        ok, out = try_run([py, "-c", f"import importlib.metadata as m; print(m.version('{name}'))"])
        if ok:
            return out.strip()
    return None


def ensure_gitignore() -> None:
    """The project-cwd output root (./.tmp/) must be gitignored before anything is
    ever written there — crawl dumps and Tier-1 captures can contain other people's
    content (OVERVIEW §4). `uf setup` is the one point in ultra-fetch's lifecycle
    that's guaranteed to run before first output, so it owns this."""
    gitignore = Path.cwd() / ".gitignore"
    entry = "./.tmp/"
    lines = gitignore.read_text().splitlines() if gitignore.exists() else []
    if any(line.strip() in (entry, ".tmp/", "/.tmp/") for line in lines):
        print(f"[ok] {entry} already in {gitignore}")
        return
    with gitignore.open("a") as f:
        if lines and lines[-1].strip() != "":
            f.write("\n")
        f.write(f"{entry}\n")
    print(f"[ok] added {entry} to {gitignore}")


def write_config(fetch_venv: Path, crawl_venv: Path, versions: dict, health: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    config = {
        "schema_version": SCHEMA_VERSION,
        "fetch_venv_python": str(fetch_venv / "bin" / "python"),
        "crawl_venv_python": str(crawl_venv / "bin" / "python"),
        "versions": versions,
        "browsers_cache": str(Path.home() / "Library" / "Caches" / "ms-playwright"),
        "profiles_dir": str(PROFILES_DIR),
        "outputs_default_root": "./.tmp/ultra-fetch",
        "health": health,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    CONFIG_PATH.write_text(json.dumps(config, indent=2))
    print(f"[ok] wrote {CONFIG_PATH}")


def cmd_setup() -> int:
    HOME.mkdir(parents=True, exist_ok=True)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)

    ensure_uv()
    gh_status = ensure_route_tool("gh", "gh", None)
    ytdlp_status = ensure_route_tool("yt-dlp", None, "yt-dlp")

    fetch_venv = setup_fetch_venv()
    crawl_venv = setup_crawl_venv()

    fetch_ok, fetch_out = smoke_test_fetch(fetch_venv)
    print(fetch_out)
    crawl_ok, crawl_out = smoke_test_crawl(crawl_venv)
    print(crawl_out)

    if not fetch_ok or not crawl_ok:
        print("[fail] one or both venv smoke tests failed — see output above.", file=sys.stderr)
        return 1

    versions = {
        "scrapling": resolved_version(fetch_venv, "scrapling"),
        "trafilatura": resolved_version(fetch_venv, "trafilatura"),
        "rank_bm25": resolved_version(fetch_venv, "rank-bm25", "rank_bm25"),
        "crawl4ai": resolved_version(crawl_venv, "crawl4ai"),
    }
    write_config(fetch_venv, crawl_venv, versions, {"gh": gh_status, "yt_dlp": ytdlp_status})
    ensure_gitignore()
    print('\nSetup complete. Try: "$SKILL/scripts/uf" fetch https://example.com')
    return 0


def find_orphaned_browsers() -> list[tuple[str, str]]:
    """Matches only Chromium-family processes launched with a `--user-data-dir` under
    our own PROFILES_DIR — narrower than a blanket "path substring anywhere in the
    command line" match, which would also flag an unrelated `tail -f`/editor/`grep`
    that merely has a `~/.ultra-fetch` path open (confirmed live as a real
    false-positive: `pgrep -fl` against the bare HOME path matches any such process,
    not just leaked browsers)."""
    ok, out = try_run(["pgrep", "-fl", str(PROFILES_DIR)])
    if not ok or not out.strip():
        return []
    matches = []
    for line in out.strip().split("\n"):
        pid, _, cmdline = line.partition(" ")
        if "--user-data-dir=" in cmdline and str(PROFILES_DIR) in cmdline:
            matches.append((pid, cmdline))
    return matches


def cmd_doctor(kill: bool = False) -> int:
    if not CONFIG_PATH.exists():
        print("uf doctor: not set up yet. Run `uf setup` first.", file=sys.stderr)
        return 1
    config = json.loads(CONFIG_PATH.read_text())
    fetch_venv = Path(config["fetch_venv_python"]).parent.parent
    crawl_venv = Path(config["crawl_venv_python"]).parent.parent

    print("=== Health ===")
    ensure_route_tool("gh", "gh", None)
    ensure_route_tool("yt-dlp", None, "yt-dlp")

    fetch_ok, fetch_out = smoke_test_fetch(fetch_venv)
    print(f"fetch venv: {'ok' if fetch_ok else 'FAIL'} — {fetch_out}")
    crawl_ok, crawl_out = smoke_test_crawl(crawl_venv)
    print(f"crawl venv: {'ok' if crawl_ok else 'FAIL'} — {crawl_out}")

    print("\n=== Tier-1 profiles ===")
    profiles_dir = Path(config["profiles_dir"])
    entries = [p for p in profiles_dir.iterdir() if p.is_dir()] if profiles_dir.exists() else []
    if entries:
        now = datetime.now(timezone.utc).timestamp()
        for entry in sorted(entries):
            age_days = (now - entry.stat().st_mtime) / 86400
            print(f"  {entry.name}: last touched {age_days:.1f} days ago")
    else:
        print("  (none yet — `uf login <site>` to create one)")

    print("\n=== Orphaned browser processes ===")
    orphans = find_orphaned_browsers()
    if orphans:
        for pid, cmdline in orphans:
            print(f"  {pid}  {cmdline}")
        if kill:
            import signal

            for pid, _ in orphans:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                    print(f"  killed {pid}")
                except ProcessLookupError:
                    pass
        else:
            print("  (leaked by a crashed uf run, holding a profile lock — re-run `uf doctor --kill` to terminate them)")
    else:
        print("  none found")

    return 0 if (fetch_ok and crawl_ok) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="uf setup")
    parser.add_argument("--doctor", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--kill", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        return cmd_doctor(kill=args.kill) if args.doctor else cmd_setup()
    except subprocess.CalledProcessError as exc:
        print(f"uf setup: command failed: {' '.join(exc.cmd)} (exit {exc.returncode})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
