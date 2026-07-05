"""Escalation ladder (fast -> browser -> stealth) + content validation gate.

Fetch-venv only. HTTP 200 is where validation starts, not where it ends (G-200): a
Cloudflare challenge page, a login wall, and a genuinely empty page can all return 200.
This module decides whether a rung's output is real content or a wall to climb past,
and owns the one constants block so thresholds/markers are editable in one place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import session as session_mod
from .markdown import html_to_markdown

# Starting defaults — tune here, not scattered across callers.
MIN_CONTENT_CHARS = 500

CHALLENGE_MARKERS = (
    "just a moment",
    "sec-if-cpt-container",
    "cf-browser-verification",
    "attention required",
    "access denied",
    "checking your browser",
)

LOGIN_WALL_MARKERS = (
    "you must log in",
    "login_form",
)


@dataclass
class FetchAttempt:
    rung: str
    status: int | None
    final_url: str
    html: str
    markdown: str | None
    captured: list[dict] = field(default_factory=list)


class EscalationExhausted(RuntimeError):
    """Every rung the ladder was allowed to try failed validation."""

    def __init__(self, attempts: list[FetchAttempt], *, query: str | None = None, require_capture: bool = False):
        self.attempts = attempts
        reasons = "; ".join(f"{a.rung}: {_why_failed(a, query=query, require_capture=require_capture)}" for a in attempts)
        super().__init__(f"all rungs failed validation ({reasons})")


def _why_failed(attempt: FetchAttempt, *, query: str | None = None, require_capture: bool = False) -> str:
    if attempt.markdown is None:
        return "no extractable content"
    lowered = attempt.html.lower()
    if any(marker in lowered for marker in CHALLENGE_MARKERS):
        return "anti-bot challenge marker present"
    if any(marker in lowered for marker in LOGIN_WALL_MARKERS) or "/login" in attempt.final_url.lower():
        return "login-wall marker present (profile may have expired — see G-expiry)"
    if len(attempt.markdown) < MIN_CONTENT_CHARS:
        return f"thin content ({len(attempt.markdown)} chars)"
    if require_capture and not attempt.captured:
        return "--capture-xhr matched zero responses"
    if query and not _query_terms_present(attempt.markdown, query):
        return f"none of the query terms ({query!r}) found in extracted text"
    return "validation failed"


def _query_terms_present(markdown: str, query: str) -> bool:
    """"Query terms present" means at least one real word from the query shows up
    somewhere in the page — not the whole phrase verbatim. A query is usually a topic
    ("legal issues lawsuits"), and real prose scatters those words across separate
    sentences ("the *legality* of web scraping...", "...*lawsuits* have been filed...")
    rather than repeating the phrase intact. This is a coarse "is this even the right
    page" sanity check; the actual relevance ranking is rank_bm25's job in the --query
    companion file, not this gate."""
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2]
    if not words:
        return True
    lowered = markdown.lower()
    return any(word in lowered for word in words)


def validate(attempt: FetchAttempt, *, query: str | None = None, require_capture: bool = False) -> bool:
    """The gate every rung's output passes through before it's accepted. Escalate
    (return False) on: no extractable content, an anti-bot challenge marker, a
    login-wall marker, thin content, a `--capture-xhr` session that captured nothing,
    or (with `--query`) a page that mentions none of the query's words at all."""
    if attempt.markdown is None:
        return False
    lowered = attempt.html.lower()
    if any(marker in lowered for marker in CHALLENGE_MARKERS):
        return False
    if any(marker in lowered for marker in LOGIN_WALL_MARKERS):
        return False
    if "/login" in attempt.final_url.lower():
        return False
    if len(attempt.markdown) < MIN_CONTENT_CHARS:
        return False
    if require_capture and not attempt.captured:
        return False
    if query and not _query_terms_present(attempt.markdown, query):
        return False
    return True


def _attempt_from_response(response, rung: str, *, captured: list[dict] | None = None) -> FetchAttempt:
    html = response.html_content
    return FetchAttempt(
        rung=rung,
        status=getattr(response, "status", None),
        final_url=str(getattr(response, "url", "") or ""),
        html=html,
        markdown=html_to_markdown(html),
        captured=captured or [],
    )


def run_ladder(
    url: str,
    *,
    mode: str = "auto",
    profile: str | None = None,
    capture_xhr: str | None = None,
    scroll: int | None = None,
    query: str | None = None,
    timeout: int = 30000,
) -> FetchAttempt:
    """Climbs fast -> browser -> stealth, stopping at the first rung whose output
    passes `validate()`.

    `profile`/`capture_xhr`/`scroll` force the starting rung to `browser` and disable
    `fast` entirely, regardless of `mode` — the fast HTTP rung can't use a
    `user_data_dir` or capture XHR, so trying it first would just waste a request
    (rung-forcing coupling, decided in the plan; `fetch.py` rejects `--mode fast` OR
    `--mode stealth` combined with these flags outright with a clear CLI error rather
    than silently overriding either here). A forced-browser fetch also never escalates
    to stealth: the profile/capture flow is proven on `DynamicFetcher`, and mixing it
    with `StealthyFetcher`'s separately managed (patchright) browser stack on the same
    profile dir is untested — not worth risking on a session carrying a live login.
    This function still raises rather than silently downgrading if a caller other than
    `fetch.py` passes `mode="stealth"` together with a forcing flag, so the contract
    holds regardless of caller. An explicit `--mode browser` or `--mode stealth` (with
    no forcing flags) pins to exactly that rung with no escalation either way — that's
    the point of naming a rung: you already know the site.

    Browser and stealth rungs both float the timeout up to at least 90s regardless of
    what `timeout` was passed in — a real browser session (navigation + JS execution,
    or a Cloudflare challenge solve) routinely needs far longer than a bare HTTP GET,
    and the plan's own default reflects that (30s fast-only vs 90s once a browser is
    actually involved).
    """
    forced_browser = bool(profile or capture_xhr or scroll)
    require_capture = bool(capture_xhr)
    attempts: list[FetchAttempt] = []

    if mode in ("fast", "stealth") and forced_browser:
        raise ValueError(f"mode={mode!r} is incompatible with profile/capture_xhr/scroll (they pin to the browser rung)")

    try_fast = mode in ("auto", "fast") and not forced_browser
    try_browser = mode in ("auto", "browser") or forced_browser
    try_stealth = mode in ("auto", "stealth") and not forced_browser

    if try_fast:
        from scrapling.fetchers import Fetcher

        response = Fetcher.get(url, impersonate="chrome", timeout=timeout)
        attempt = _attempt_from_response(response, "fast")
        attempts.append(attempt)
        if validate(attempt, query=query):
            return attempt
        if mode == "fast":
            raise EscalationExhausted(attempts, query=query)

    if try_browser:
        response = session_mod.browser_fetch(
            url, profile=profile, capture_xhr=capture_xhr, scroll=scroll, timeout=max(timeout, 90000)
        )
        captured = session_mod.read_captured_xhr(response) if capture_xhr else []
        attempt = _attempt_from_response(response, "browser", captured=captured)
        attempts.append(attempt)
        if validate(attempt, query=query, require_capture=require_capture):
            return attempt
        if mode == "browser" or forced_browser:
            raise EscalationExhausted(attempts, query=query, require_capture=require_capture)

    if try_stealth:
        response = session_mod.browser_fetch(
            url, stealth=True, solve_cloudflare=True, timeout=max(timeout, 90000)
        )
        attempt = _attempt_from_response(response, "stealth")
        attempts.append(attempt)
        if validate(attempt, query=query):
            return attempt

    raise EscalationExhausted(attempts, query=query, require_capture=require_capture)
