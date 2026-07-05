"""Tier-1 logged-in profile management + generic XHR capture + scroll synthesis.

Fetch-venv only (imports scrapling). Owns everything that touches a persisted
`user_data_dir` browser profile: the OS file lock that keeps two concurrent uses of
the same profile from corrupting each other, and the capture/scroll mechanics that
only make sense once a browser session exists.

Verified against installed scrapling 0.4.10 source (not assumed): captured XHR
responses live on `response.captured_xhr` — NOT `page.captured_xhr` — as a list of
full `Response` objects, each with a `.body` bytes property (G-capture-body).
`DynamicFetcher.fetch`/`StealthyFetcher.fetch` are classmethods that internally do
`with DynamicSession(**kwargs) as session: return session.fetch(url)` — they already
open and close their own browser per call, so callers here never manage a session
object directly for one-shot fetches; only `login_flow` (which must keep the browser
open across an indefinite manual step) builds a `DynamicSession` by hand.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
from pathlib import Path

ULTRA_FETCH_HOME = Path(os.environ.get("ULTRA_FETCH_HOME", str(Path.home() / ".ultra-fetch")))
PROFILES_DIR = ULTRA_FETCH_HOME / "profiles"
LOCKS_DIR = ULTRA_FETCH_HOME / "locks"

_SAFE_SITE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_site(site: str) -> str:
    """`site` becomes a directory name (profile) and a filename (lock) — unlike every
    URL-derived path in engine/save.py, which always goes through slugify()/domain_of()
    first, `site` had no equivalent guard, so `uf login '../../../etc/cron.d/evil'` or
    a matching `--profile` value could point a real Chromium profile write and its
    lock file at an arbitrary filesystem path instead of confining it under
    PROFILES_DIR/LOCKS_DIR. Restricting to a safe charset closes that off entirely
    rather than trying to block specific bad patterns like ".."."""
    if not _SAFE_SITE_RE.match(site):
        raise ValueError(
            f"invalid profile name {site!r} — use only letters, digits, '-', '_' (no slashes or dots)"
        )
    return site


def profile_dir(site: str) -> Path:
    return PROFILES_DIR / _validate_site(site)


def profile_exists(site: str) -> bool:
    return profile_dir(site).exists()


class ProfileLockError(RuntimeError):
    """Another uf process already holds this profile's lock."""


@contextlib.contextmanager
def profile_lock(site: str):
    """Holds an OS file lock (fcntl.flock, exclusive, non-blocking) for the lifetime of
    any browser session that reuses `site`'s user_data_dir. Chromium-family browsers
    refuse to open a profile directory that's already open elsewhere — an opaque
    ProcessSingleton crash — so this turns that into a clear message instead, and the
    try/finally guarantees release even if the session inside raises."""
    site = _validate_site(site)
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = LOCKS_DIR / f"{site}.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ProfileLockError(
                f"profile '{site}' is in use (another uf command or `uf login {site}` is running)"
            ) from None
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def scroll_page_action(times: int, wait_ms: int = 800):
    """A `page_action` for DynamicFetcher/StealthyFetcher — called as `page_action(page)`
    after navigation. scrapling has no native "scroll N times" option, and gating on
    network-idle doesn't work here: many SPAs poll constantly and never go idle
    (G-spaidle). So this scrolls a fixed number of times with an explicit wait after
    each step, instead of waiting for a network signal that may never come."""

    def _action(page):
        for _ in range(times):
            page.mouse.wheel(0, 15000)
            page.wait_for_timeout(wait_ms)
        return page

    return _action


def _parse_json_or_ndjson(text: str):
    try:
        return json.loads(text)
    except ValueError:
        pass
    parsed_lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            parsed_lines.append(json.loads(line))
        except ValueError:
            continue
    return parsed_lines or None


def read_captured_xhr(response) -> list[dict]:
    """Reads whatever `capture_xhr` matched off `response.captured_xhr` (a list of full
    `Response` objects — G-capture-body). Each `.body` is bytes; this decodes
    defensively (utf-8, replacing undecodable bytes — `bytes.split("\\n")` against a
    str separator raises TypeError, so decode first) and tries whole-body JSON before
    falling back to NDJSON (one object per line), since XHR/GraphQL backends commonly
    stream one JSON object per line rather than a single top-level array."""
    results = []
    for captured in getattr(response, "captured_xhr", None) or []:
        text = captured.body.decode("utf-8", "replace")
        results.append({
            "url": getattr(captured, "url", None),
            "status": getattr(captured, "status", None),
            "body_text": text,
            "body_json": _parse_json_or_ndjson(text),
        })
    return results


def browser_fetch(
    url: str,
    *,
    profile: str | None = None,
    headless: bool = True,
    capture_xhr: str | None = None,
    scroll: int | None = None,
    timeout: int = 30000,
    stealth: bool = False,
    solve_cloudflare: bool = False,
):
    """The browser/stealth rungs, in one call. `profile`/`capture_xhr`/`scroll` all
    require a real Chromium session, so this is the only entry point that constructs
    one — `engine/escalate.py`'s fast rung never touches this module. Holds the
    profile lock (if any) for exactly the duration of the fetch; `DynamicFetcher.fetch`/
    `StealthyFetcher.fetch` already open and close their own session per call, so no
    session object leaks out of this function.
    """
    from scrapling.fetchers import DynamicFetcher, StealthyFetcher

    kwargs: dict = {"headless": headless, "timeout": timeout}
    if profile:
        kwargs["user_data_dir"] = str(profile_dir(profile))
    if capture_xhr:
        kwargs["capture_xhr"] = capture_xhr
    if scroll:
        kwargs["page_action"] = scroll_page_action(scroll)
    if stealth:
        kwargs["solve_cloudflare"] = solve_cloudflare

    fetcher = StealthyFetcher if stealth else DynamicFetcher
    lock_cm = profile_lock(profile) if profile else contextlib.nullcontext()
    with lock_cm:
        return fetcher.fetch(url, **kwargs)


def login_flow(site: str, start_url: str) -> None:
    """Headed onboarding for `uf login <site>`: opens a visible browser at
    `start_url` against a fresh/existing `user_data_dir` for `site`, blocks on manual
    2FA/captcha via `input()`, then closes — persisting cookies/localStorage to disk
    for every future `--profile <site>` call. Needs a real `DynamicSession` (not the
    auto-closing `DynamicFetcher.fetch`) because the browser must stay open across an
    indefinite manual step, not just for one page load."""
    from scrapling.fetchers import DynamicSession

    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    with profile_lock(site):
        with DynamicSession(headless=False, user_data_dir=str(profile_dir(site))) as session:
            session.fetch(start_url)
            input(
                f"\nLog in to {site} in the opened browser window. "
                "Once you're fully logged in, come back here and press Enter to save the session and close the browser..."
            )
