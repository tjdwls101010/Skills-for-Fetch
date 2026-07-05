"""URL -> route classification. Detection only.

This module never executes or shells out to `gh`/`yt-dlp` — it only tells fetch.py/
crawl.py "this URL belongs to a site with a better-suited tool", printed as a
non-blocking stderr nudge before the generic engine proceeds anyway. Claude is the one
that runs `gh`/`yt-dlp` directly, by reading references/routes.md itself.

The Facebook/X reason strings are short and self-contained here (not loaded from
references/routes.md) on purpose: routes.md's load-trigger is "the URL is GitHub/
YouTube", which by definition never fires for a Facebook/X URL, so a breadcrumb parked
there would never be read on the one path it exists to steer. A logged-in feed/profile/
timeline should be caught earlier by Claude reading the breadcrumb in SKILL.md before
ever invoking `uf fetch` — this is just a runtime safety net if it wasn't.
"""

from __future__ import annotations

from urllib.parse import urlsplit

_GITHUB_HOSTS = {"github.com", "www.github.com"}
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
_FACEBOOK_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com"}
_X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}


def classify(url: str) -> dict | None:
    """Returns {"site", "reason"} for a recognized host, else None. Advisory only —
    the caller still fetches the URL; this just decides whether to print a heads-up
    first."""
    host = urlsplit(url).netloc.lower()

    if host in _GITHUB_HOSTS:
        return {
            "site": "github",
            "reason": "GitHub content is usually better read via `gh` than scraped — see references/routes.md for exact commands.",
        }
    if host in _YOUTUBE_HOSTS:
        return {
            "site": "youtube",
            "reason": "YouTube metadata/transcripts are usually better pulled via `yt-dlp` than scraped — see references/routes.md for exact commands.",
        }
    if host in _FACEBOOK_HOSTS:
        return {
            "site": "facebook",
            "reason": "A logged-in Facebook feed/profile/timeline is out of scope here — the facebook-fetch skill drives a purpose-built CLI for that. An anonymous public page is still fine to fetch normally.",
        }
    if host in _X_HOSTS:
        return {
            "site": "x",
            "reason": "A logged-in X feed/profile/thread is out of scope here — the x-fetch skill drives a purpose-built CLI for that. An anonymous public page/tweet is still fine to fetch normally.",
        }
    return None
