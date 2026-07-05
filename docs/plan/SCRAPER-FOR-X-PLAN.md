# `scraper-for-x` — Implementation Plan (v1)

> Status: PLAN (hardened). Implementation happens in a separate future Claude session.
> This document lives in `docs/plan/` alongside [PLAN.md](PLAN.md) (the `web-fetch` skill plan) and [SCRAPER-FOR-FACEBOOK-PLAN.md](SCRAPER-FOR-FACEBOOK-PLAN.md) (its sibling package). The `web-fetch` skill *consumes* this package as an external CLI (`scrape-x`), the way it consumes `gh` / `yt-dlp` / `scrape-fb`.
> The architecture below was decided by a **live probe against a real logged-in X session on 2026-07-05** — not by reasoning alone (§23). This revision folds in **28 findings from an adversarial multi-lens review** (2 blockers, 9 high, 14 medium, 3 low); where a decision below reverses the naive first draft, that is deliberate (§23).

## 0. Why this package exists

The `web-fetch` skill needs logged-in read access to X/Twitter — profiles and content behind X's login wall that the official API prices out of reach. Rather than bury this in the skill, we extract it into a standalone, general-purpose OSS Python package (the same split we made for Facebook). The skill stays thin and calls `scrape-x` as a tool; the package is independently useful, testable, and versioned.

**What the live probe proved (and why X is *not* Facebook).** For Facebook we could not replay the backend API — Meta's tokens rot fast, so `scrape-for-facebook` drives a browser and *observes* the GraphQL responses. X is different, and we confirmed it live:

- X's read GraphQL endpoints (`UserTweets`, `SearchTimeline`, `TweetDetail`) accept plain `httpx` requests carrying only the browser-harvested session cookies (`auth_token`, `ct0`) + the well-known **public** bearer token + the `x-csrf-token` header — **with no `x-client-transaction-id`** (dropping it still returned HTTP 200 + valid data), and cursor pagination works cleanly over `httpx` (page 1 → page 2, zero overlap).
- So the single hardest, most fragile part of every X client — reproducing X's per-request `x-client-transaction-id` (a reimplementation of X's obfuscated `ondemand.js` / SVG-animation / SHA256 scheme), plus `ui_metrics` and the onboarding login flow — **we do not reproduce at all.**

**Honest framing (carry into the README; do not oversell).** This is *not* first-of-kind. `twikit` and `twscrape` are established, capable X clients. Our differentiation is *incremental, not categorical*, and it targets exactly what makes those libraries break:

1. We obtain the session with a **real stealth-browser login** (or the user's own imported cookies) instead of driving X's automated onboarding/`ui_metrics`/Arkose flow — the most fragile, most challenge-prone part of `twikit`.
2. We keep the current GraphQL **query-ids fresh on *both* entry paths, browser-free** (they rotate — the live UserTweets id on 2026-07-05 differed from twikit's): the login path harvests them from the browser session at login time; the cookie-import path re-anchors them by fetching X's public `main.js` bundle over `httpx` and regex-extracting the id/feature map (§8, §12) — no browser required. Twikit's single biggest maintenance burden becomes a self-updating step either way. (Shipped defaults are a fallback only, not the source of truth.)
3. We reproduce **none** of X's per-request anti-bot machinery (§3), because the probe proved reads don't need it. This is not academic: twikit's transaction-id generator **broke in the wild on 2026-03-18** when X changed its `ondemand.s.js` structure ([twikit#408](https://github.com/d60/twikit/issues/408), "completely unusable") — the exact failure class we sidestep by never computing it.
4. A **clean, typed, read-only, LLM-agent-friendly** interface (twikit's real pain points, per the user's own `serenity` scraper: monkey-patching missing model fields, digging `_data` for note-tweets / subscriber flags, no end-of-feed signal — §5, §6).

## 1. Scope

**In scope (v1) — read-only, three surfaces:**
- **User tweets/replies/media** (`UserTweets` / `UserTweetsAndReplies`): a profile's posts, replies, and media, deep via cursor pagination.
- **Search** (`SearchTimeline`, product `Latest` / `Top`): tweets matching a query / advanced operators.
- **Single tweet + thread** (`TweetDetail`): one tweet plus its reply/conversation thread.
- Retrieval by **count** (`--limit N`) and by **date window** (`--since` / `--until`), filtered on `created_at`; `--limit` and the date window **compose** (§11).
- Output as **JSON** (default) and **NDJSON** (streaming).
- Session obtained once via a **stealth-browser login** (persisted) **or** imported cookies; `httpx` reads thereafter.

**Explicitly out of scope for v1 (roadmap, §19):** followers/following lists; home timeline (For You / Following); likes/bookmarks; lists; communities; notifications; trends; DMs; media file download (URLs only in v1); incremental `--since-last` state.

**Non-goals (permanent):** no **write/mutation** actions of any kind (posting, delete, like, retweet, follow, block, DM); no **credential-injection automated login** (we use browser-harvest or cookie-import, both human-established sessions); no bundled **multi-account rotation / proxy-for-scale / CAPTCHA-solving**. These are non-goals because **no such code exists and none will be added — the guarantee is absence of capability** (the structural guardrails in §9). Separately, the ≥ 0.5 s non-bypassable *per-process* inter-request floor (§9) backs the "not a mass-scraping tool" *pacing* claim in code, not only prose — but it is a pacing floor, not an enforcement of the capability non-goals above (§9, §24-of-review).

## 2. Legal, ethical, and safety posture — read first

Prominent in README/DISCLAIMER and surfaced by the CLI, not buried. *(A background research pass on X's litigation/session-longevity landscape is folding additional citations into this section and §9/§12 — §23.)*

- **X ToS.** X's Terms of Service prohibit scraping and automated access without written permission; violations are enforced by suspension/termination and, historically, litigation. Users assume this risk.
- **Litigation context — honest and non-alarmist.** X Corp is notably litigious about scraping (e.g. *X Corp v. Bright Data*), but the documented outcomes and enforcement skew toward **commercial mass-scrapers and data brokers**, not solo personal-scale read use. The probability for this project is uncertain; the exposure is real and this plan names it so the choice stays informed.
- **Maintainer exposure (not just the user).** Publishing a public package named `scraper-for-x` under a real identity ties *you, the author*, to it — PyPI Trusted Publishing binds each release to your named GitHub repo/account (`tjdwls101010/Scraper-for-X`). Record this in the DISCLAIMER exactly as the FB package does.
- **Account-ban mitigation (§9).** Human-like pacing with a per-process non-bypassable floor; per-run request cap; single-session, single-target invocation; rate-limit-aware backoff; a clear recommendation to use a **dedicated/throwaway account**, never your primary — X flags automation aggressively (we saw the login-side of this live: §17 G-webdriver-login). Run from the **same network/egress IP** where the session was established (§7, §9 — an abrupt IP change against an existing session can soft-lock it).
- **Third-party PII — regimes stated correctly.** Captured tweets contain other people's personal data. Under **GDPR**, scraping identifiable third parties can make *you* a **data controller** with real obligations (lawful basis, data-subject rights, retention); "personal use" is not automatically a lawful basis. The **CCPA/CPRA** is different in kind — it regulates for-profit "businesses" meeting statutory thresholds (e.g. revenue / volume) and generally exempts purely personal/household activity, so it typically does *not* attach to a solo personal-scale scraper; even so, jurisdiction-specific privacy-tort and publication risk can still apply. Minimize retention; delete outputs when done. The MIT license does not cure privacy-law exposure. *(Non-legal-advice.)*
- **PII hygiene end-to-end.** Outputs are never committed/shared. The skill writes them to a gitignored path; standalone `scrape-x` defaults output to a non-repo location and prints a privacy reminder — and the DISCLAIMER states plainly that *a printed reminder is not a technical control*. Committed fixtures are built PII-free by construction (§13).
- **Disclaimer + license.** MIT license; a `DISCLAIMER.md` covering all of the above, incl. the same-IP operational note and the session-credential warning (§7).

## 3. Architecture — the hybrid (empirically grounded)

```
target (username / search query / tweet id)
      │
      ▼
 scrape-x login  ──►  Stealth browser (scrapling/patchright, headed)   ── one time ──►  harvest:
      │                 - user logs in by hand (webdriver spoofed; X allows it)          { auth_token, ct0,
      │                 - capture the browser's own GraphQL request                        live query-ids,
      │                                                                                    features, UA }
      ▼                                                                                        │
 scrape-x fetch / search / tweet                                                               │
      │                                                                                        ▼
   httpx client  ──►  GET https://x.com/i/api/graphql/<queryId>/<Op>?variables=…&features=…
      │                 headers: authorization: Bearer <PUBLIC static token>
      │                          cookie: auth_token=…; ct0=…        x-csrf-token: <ct0>
      │                          x-twitter-auth-type, x-twitter-active-user, x-twitter-client-language, <harvested UA>
      │                 NO x-client-transaction-id  (proven unnecessary for reads — 2026-07-05)
      ▼
  Parser  ──►  data.user.result.timeline.timeline.instructions[]   (all instructions; see §8 for per-op roots)
      │        tweets ← every TimelineAddEntries.entries[] + the single TimelinePinEntry.entry
      │        cursor ← entry(content.entryType=TimelineTimelineCursor, cursorType=Bottom).value
      │        mutate variables.cursor → next page (deep reach; clean pagination proven; §8 EOF rule)
      ▼
  list[Tweet]  ──►  JSON / NDJSON writer  (redaction-scrubbed diagnostics)
```

**Core principle: harvest a real session + keep query-ids fresh, then replay reads over `httpx`.** We reproduce **none** of X's per-request anti-bot (transaction-id, `ui_metrics`, onboarding flow). This is the middle path between the two extremes, and X uniquely allows it:

| | login | reads at fetch time | reproduces X anti-bot? | depth | fragility |
|---|---|---|---|---|---|
| **twikit** (replay-everything) | automated user/pass + ui_metrics + Arkose | httpx, self-computed txid + hardcoded query-ids | **yes — all of it** | deep | **highest** |
| **scrape-fb** (observe-don't-replay) | headed browser, persist | browser scrolls, we observe XHR | none (browser does it) | shallow (scroll-bound) | low, but slow |
| **scrape-x** (this plan: harvest-then-replay) | **stealth browser login** (or cookie import) | **httpx, harvested cookies + live query-ids, no txid** | **deep (cursor)** | **low** |

The one live-probe caveat we design around: if X ever begins **enforcing** `x-client-transaction-id` on reads (it does not today), the `httpx` read path stops working. The **documented degradation route** is architecture B — add a browser-observe read path that reuses `session.py`'s stealth browser to drive X and intercept the GraphQL XHR (the same JSON envelope `parse.py` already walks), exactly like `scrape-fb`. **This route is *not* built or tested in v1** (§12, §13); it is the escape hatch, not a shipped capability. We promise a fast re-anchor path and this route — not stability.

## 4. Package layout

**Its own separate repo (already initialized).** `github.com/tjdwls101010/Scraper-for-X`, distinct from the skill's `Skills-for-Fetch` repo. Physically checked out at `package/scraper-for-x/` inside the Skills-for-Fetch working tree, but the parent does **not** track it (root `.gitignore` lists `package/scraper-for-X/`; the dir has its own `.git`, initial commit already pushed). Commit/push it independently. Its CI/publish workflows live in **this repo's own** `.github/workflows/`.

```
package/scraper-for-x/            (own repo "Scraper-for-X"; git-ignored by parent; PyPI dist "scraper-for-x"; command scrape-x)
├── pyproject.toml                (hatchling; requires-python >=3.11; scripts: scrape-x; [browser] extra; classifiers incl. OS)
├── README.md                     (usage + prominent §2 disclaimer + isolated-install instructions)
├── LICENSE                       (MIT)
├── DISCLAIMER.md                 (ToS, litigation/maintainer exposure, PII/controller, ban, same-IP note, session-credential warning)
├── CHANGELOG.md                  (Keep a Changelog; starts 0.1.0)
├── .gitignore                    (scoped; scratch/*.raw.json; fixtures un-ignored BY NAME — see below)
├── .pre-commit-config.yaml       (ruff + fixture PII/secret scan hook)
├── requirements-dev.lock         (pinned dev/CI deps)
├── .github/workflows/
│   ├── ci.yml                    (ruff + pytest fixtures + build + wheel-install smoke + fixture PII scan)
│   └── publish.yml               (on GitHub Release published → build → PyPI Trusted Publishing/OIDC; id-token: write; SHA-pinned action)
├── src/scraper_for_x/            (import name scraper_for_x)
│   ├── __init__.py               (exports XScraper, Tweet, User, Media, errors — MUST NOT eagerly import scrapling; see §14)
│   ├── session.py                (stealth-browser login + cookie-import; harvests cookies + query-ids; status/doctor. scrapling imported LAZILY, inside login()/setup() bodies only)
│   ├── auth.py                   (Session credential store {auth_token, ct0, UA}; platformdirs; dir 0700 + FILE 0600; validation; cookie-import contract §7)
│   ├── queryids.py               (default query-ids/features + browser-free re-anchor: fetch x.com main.js over httpx, regex the id/feature map — §8/§12)
│   ├── gql.py                    (endpoint templates; bearer PUBLIC constant; per-op variables/features builders)
│   ├── client.py                 (httpx read client: headers, GET, 429/rate-limit handling)
│   ├── parse.py                  (timeline-envelope walk: ALL instructions → entries + pin-entry → tweet_results.result; cursor extract)
│   ├── model.py                  (Tweet, User, Media; normalization incl. retweet-original text; to_dict)
│   ├── retrieve.py               (limit + since/until; cursor loop; stop-reason; None-created_at-safe; pre-exit-4 session probe)
│   ├── redact.py                 (single scrub helper for ALL diagnostic/log output, incl. cookie-import parse errors §21)
│   ├── config.py                 (defaults + non-bypassable per-process floor; env/flag overrides)
│   ├── errors.py                 (LoginRequiredError, SessionExpiredError, RateLimitedError, ProfileUnavailableError, NotFoundError, InvalidCookieError, NotEnteredError, SessionClosedError, ...)
│   └── cli.py                    (argparse: login / status / setup / doctor / fetch / search / tweet; --version. Defers scrapling-touching imports to login/setup subcommands)
├── tests/
│   ├── fixtures/*.json           (SYNTHETIC skeletons — built PII-free by construction)
│   ├── test_parse.py, test_model.py, test_retrieve.py, test_redact.py, test_identifier.py
│   └── live/                     (opt-in; SFX_LIVE_TESTS=1; never in CI; outputs gitignored)
└── scripts/
    ├── record_fixture.py         (local: capture real bodies to a gitignored scratch/*.raw.json)
    ├── build_fixture.py          (extract only asserted values → inject into a hand-authored synthetic skeleton)
    └── harvest_queryids.py       (browser-free: fetch x.com main.js over httpx + regex the current query-ids/features, for re-anchoring — §12)
```

**`.gitignore` (get this exactly right — it is a PII trap otherwise):** ignore raw captures under a distinct name (`scratch/`, `*.raw.json`) and runtime outputs (`output/`, `profiles/`, `.venv`); then **un-ignore each committed fixture BY EXACT NAME**, never a wildcard — mirror the shipped FB `.gitignore` and its warning comment:
```
scratch/
*.raw.json
output/
!tests/fixtures/
!tests/fixtures/user_tweets.json
!tests/fixtures/search_timeline.json
!tests/fixtures/tweet_detail.json
# …one line per fixture. Enumerated explicitly, NOT `!tests/fixtures/*.json` —
# a wildcard un-ignore would silently re-include ANY new .json (incl. a raw capture)
# dropped into this dir. Adding a fixture means adding its name here too.
```
A wildcard un-ignore is exactly the residual hole the FB package's comment warns against; it re-opens the PII trap this section exists to close.

## 5. Public Python API (general-OSS-first, read-only)

```python
from scraper_for_x import XScraper, Tweet, User, Media
from scraper_for_x.errors import (
    LoginRequiredError, SessionExpiredError, RateLimitedError,
    NotFoundError, ProfileUnavailableError, InvalidCookieError,
    NotEnteredError, SessionClosedError,
)

# One-time session setup — these are SIDE-EFFECTING PERSISTERS: each writes the session
# credential to disk keyed by `profile`; their return value is NOT the read path.
XScraper(profile="default").login()                                  # stealth headed browser
XScraper.from_cookies(auth_token="…", ct0="…", profile="default")    # cookie import (validated — §7)
XScraper.from_cookie_file("cookies.txt", profile="default")          # Netscape/JSON/cURL export (validated — §7)

# ALL reads go through a fresh, context-managed instance that loads the persisted session:
with XScraper(profile="default") as x:
    tweets: list[Tweet] = x.fetch_user_tweets("nasa", limit=200, since="2026-01-01", replies=False)
    hits:   list[Tweet] = x.search("from:nasa artemis", product="Latest", limit=100)
    thread: list[Tweet] = x.fetch_tweet("https://x.com/nasa/status/…", replies=True)
    for t in x.iter_user_tweets("nasa", limit=1000):                 # streaming generator (deep archival)
        ...

XScraper(profile="default").status()   # -> Status.LOGGED_IN | EXPIRED | RATE_LIMITED  (enum)
```

Design notes:
- `XScraper(profile="default", *, profile_dir=None, config=None, min_request_pause=…, max_requests=…)`. No `headless` (reads are `httpx`; login is always headed, §7).
- **Constructor semantics (pinned):** `login()` / `from_cookies` / `from_cookie_file` **persist** a session and return an instance you should not read from directly; reads happen through a **fresh** `with XScraper(profile=...) as x:` that loads the persisted session. This removes the "assign-the-return-and-read" ambiguity.
- **Lifecycle (both ends specified):** reads require the context to be entered. Reading on a **not-yet-entered** instance raises `NotEnteredError` ("use `with XScraper(...) as x:` for reads") rather than leaking an unclosed `httpx` client; iterating a generator **after** the context closes raises `SessionClosedError`. `login()`, `status()`, and the alternative constructors remain valid on a non-entered instance (they don't open the read client).
- Accepts a `@handle`, a bare username, a numeric id, or a full profile/tweet URL — after **normalize-then-validate** (§10). A bare all-digit token is a **user id by default** (rest_ids are numeric; all-digit handles are vanishingly rare); force the handle reading with a `@`-prefix or `--by screen_name`.
- `fetch_*` return materialized `list[Tweet]`; `iter_*` yield incrementally. Errors are typed so callers branch; `RateLimitedError` carries the `x-rate-limit-reset` epoch.
- **Naming parity:** the reply-inclusion flag is `replies=` on **both** `fetch_user_tweets` and `fetch_tweet`, matching the single CLI `--replies` token (no `with_replies=` variant).
- `raw=True` (opt-in) attaches the raw `tweet_results.result` node for debugging; off by default, redaction-scrubbed unless `--no-redact` + an on-screen warning (§21).

## 6. `Tweet` / `User` / `Media` schema (grounded in the live 2026 model)

Decide the schema **now** while pre-1.0. Field paths below are the *real, current* X structure confirmed by the 2026-07-05 probe (newer than twikit's — X moved `name`/`screen_name`/`created_at` into `user.core`, added `views.count`).

```python
@dataclass
class Media:
    kind: str              # "photo" | "video" | "animated_gif" | "unknown"
    url: str               # pbs.twimg.com / video.twimg.com URL (see G-media-expiry, §17)
    width: int | None = None
    height: int | None = None
    alt_text: str | None = None

@dataclass
class User:
    id: str                # rest_id (stable)
    screen_name: str       # core.screen_name  (@handle)
    name: str | None       # core.name
    created_at: datetime | None
    followers_count: int | None      # legacy.followers_count
    following_count: int | None      # legacy.friends_count
    tweet_count: int | None          # legacy.statuses_count
    is_blue_verified: bool | None
    description: str | None
    url: str | None

@dataclass
class Tweet:
    id: str                          # rest_id (stable dedup/merge key)
    url: str | None                  # https://x.com/<handle>/status/<id>
    created_at: datetime | None      # legacy.created_at (X ts format), UTC — MAY be None (§11 handles it)
    text: str                        # long/full text (see extraction rule below)
    lang: str | None
    author: User | None              # core.user_results.result
    is_reply: bool
    in_reply_to_id: str | None       # legacy.in_reply_to_status_id_str
    conversation_id: str | None      # legacy.conversation_id_str
    reply_count: int | None
    retweet_count: int | None
    quote_count: int | None
    like_count: int | None           # legacy.favorite_count
    bookmark_count: int | None
    view_count: int | None           # views.count IF present, else None (views object may be absent / lack count)
    media: list[Media]               # legacy.extended_entities.media (of the resolved text node — see retweet rule)
    urls: list[str]                  # legacy.entities.urls[].expanded_url
    hashtags: list[str]
    is_note_tweet: bool              # resolved text came from note_tweet (long form)
    is_pinned: bool                  # delivered via a TimelinePinEntry instruction (§8)
    retweeted_tweet: "Tweet | None"  # legacy.retweeted_status_result (one level)
    quoted_tweet: "Tweet | None"     # quoted_status_result (one level)
    is_restricted: bool              # __typename == "TweetWithVisibilityResults" (subscriber/limited)
    captured_at: datetime            # UTC
    raw: dict | None = None          # only if raw=True
```

JSON output is `[Tweet.to_dict(), ...]`; NDJSON one per line. Datetimes serialize as ISO 8601 UTC. A run also carries **metadata** (stop reason, whether the window/limit was fully reached, counts, rate-limit hits) to stderr and as a summary record.

**Schema gotchas the parser must handle (from the live model):**
- **`text` extraction, with the retweet trap.** Prefer `note_tweet.note_tweet_results.result.text` (long > 280), else `legacy.full_text`. **But for a retweet** (`legacy.retweeted_status_result` present), the *outer* node's `full_text` is the truncated `"RT @handle: …"` stub with no `note_tweet` — resolve `text` (and `media`, `urls`, `is_note_tweet`) from the **retweeted original node** using the same note_tweet-then-full_text preference, not the outer node. (A fixture of a retweet-of-a-long-tweet pins this — §13.)
- **`view_count` is optional and string-typed.** `views.count` is a *string* when present; the `views` object can be absent or lack `count`. Extract None-safely: `int(v) if (vs := t.get("views")) and (v := vs.get("count")) and v.isdigit() else None`. A naive `int(...)` crashes an otherwise-valid page.
- **Restricted/subscriber tweets** arrive wrapped `__typename: "TweetWithVisibilityResults"` with the real tweet under `.tweet`; unwrap it, set `is_restricted`. (serenity's `exclusivityInfo`, cleanly modeled.)
- **Retweets** nest the original under `legacy.retweeted_status_result`; **quotes** under `quoted_status_result`. One level deep only.
- **Author** is `core.user_results.result`; read name/handle from its `.core`, counts from its `.legacy`.

## 7. Login & session model

- **Two ways in, one session shape.** Both produce the same on-disk **session credential** = `{auth_token, ct0, user-agent}` used by the `httpx` read path.
  - **`scrape-x login`** — the default. Opens a **headed stealth browser** (the config validated live: `channel="chrome"`, `--disable-blink-features=AutomationControlled`, `ignore_default_args=["--enable-automation"]`, plus a `navigator.webdriver` override), waits for a hand login, then harvests `auth_token`/`ct0` + the **current query-ids/features/UA** and persists them. The stealth config is **mandatory** (§17 G-webdriver-login, proven live).
  - **`scrape-x login --cookies <file>`** / `XScraper.from_cookies(...)` — imports an existing human-established session. No browser at runtime. Query-ids stay fresh via the **browser-free re-anchor** (`queryids.py` fetches x.com `main.js` over `httpx`; §8/§12), so this path does **not** decay to stale ids.
- **Cookie-import contract (validate before trust).** For all three cookie-import forms: (1) parse the export (Netscape/JSON/cURL), then **validate** that `auth_token`/`ct0` match expected token shapes before storing or firing them at X — on mismatch raise `InvalidCookieError`, never store best-effort; (2) write only the extracted `{auth_token, ct0, UA}` into the `0700` dir / `0600` file store — do **not** copy or retain the source export; (3) print a one-line reminder that the **source export file still contains a live, password-less session** the user should delete/secure; (4) parse/validation error messages must be **redaction-safe** (§21) — report "line 3 malformed" / "ct0 failed shape check", never echo a raw cookie value.
- **Storage & permissions.** `platformdirs.user_data_dir("scraper-for-x")/profiles/<name>` (macOS: `~/Library/Application Support/scraper-for-x/profiles/default`). Overridable via `--profile-dir` / `SFX_PROFILE_DIR`. The credential is a small plaintext `{auth_token, ct0, UA}` blob = **a live, logged-in X session with no password**. Enforce **both** the **dir mode `0700`** *and* the **file mode `0600`** (via `os.open(..., 0o600)` / `os.chmod`, re-asserted on every write, independent of umask — including the cookie-import path). When the store is redirected via `--profile-dir`/`SFX_PROFILE_DIR`, still apply `0700`/`0600` **and** print a one-line stderr warning that the credential now lives outside the default protected location and must not be synced/shared. The DISCLAIMER says: do not back up, sync, or share it; revoke by logging the session out on x.com.
- **Same-IP operational assumption.** Prefer running `scrape-x` from the **same network/egress IP** where the session was established. This matters most on cookie-import: pairing a session minted in your normal browser with a datacenter/VPN IP is exactly the abrupt IP+client change X's abuse systems weight against an existing session, and can soft-lock it. Documented in §9 and the DISCLAIMER.
- **`scrape-x status`.** Makes a cheap authenticated read (viewer account lookup) and returns `Status.{LOGGED_IN, EXPIRED, RATE_LIMITED}`.
- **Expiry / soft-lock detection (two variants).** (a) Explicit: a `401`/authentication error or a logged-out marker → `SessionExpiredError` → CLI prints `run: scrape-x login`, exit 2. (b) **Soft/silent:** X often degrades a stale/soft-locked session by returning **HTTP 200 with an empty/limited timeline** rather than a clean 401. So before treating a zero-result as drift, `retrieve.py` runs the same cheap viewer-lookup `status` does and branches (§11): soft-locked → exit 2; genuinely logged-in but empty → exit 0/4 per §11. We never auto-relogin and never hammer retries.

## 8. Read engine (query-id, request, parse)

**The `httpx` request (proven shape).** `GET https://x.com/i/api/graphql/<queryId>/<Op>` with `variables` and `features` as URL-encoded JSON query params. Required headers: `authorization: Bearer <PUBLIC constant>`, `cookie: auth_token=…; ct0=…`, `x-csrf-token: <ct0>`, `x-twitter-auth-type: OAuth2Session`, `x-twitter-active-user: yes`, `x-twitter-client-language`, and the harvested `user-agent`. **No `x-client-transaction-id`** (proven unnecessary — §0, §23). The bearer is the well-known static web token (public, not account-specific) — a named constant with a comment saying so.

**Query-ids + feature flags — fresh on both paths, browser-free.** Each op is `<queryId>/<OpName>`; the id rotates when X ships a web update. `queryids.py` ships a known-good default per op **and** provides a **browser-free re-anchor**: fetch X's public `main.js` bundle over `httpx` (using cookies the caller already holds) and regex-extract the current query-id/feature map. `scrape-x login` harvests them from the browser session; `scrape-x doctor --refresh` and `harvest_queryids.py` re-anchor via `main.js` — so a **cookie-import, browser-free install re-anchors without ever installing the `[browser]` extra**. Defaults are a fallback, not the source of truth.

**Per-op envelope roots (pinned, not hidden behind an ellipsis):**
- `UserTweets` / `UserTweetsAndReplies`: `data.user.result.timeline.timeline.instructions[]`
- `SearchTimeline`: `data.search_by_raw_query.search_timeline.timeline.instructions[]`
- `TweetDetail`: `data.threaded_conversation_with_injections_v2.instructions[]`

**Parse ALL instructions, not just one.** Iterate every element of `instructions[]`: collect `entries[]` from **each** `type == "TimelineAddEntries"`, **and** unwrap the single `entry` (singular) from the `type == "TimelinePinEntry"` instruction — the **pinned tweet lives there, not in `TimelineAddEntries`**, so a parser that reads only `TimelineAddEntries` silently drops it (contradicting §11). Tag the pin-entry tweet `is_pinned=true`. Tweet entries have `entryId` starting `tweet-` with content at `content.itemContent.tweet_results.result`; `TweetDetail` also uses `conversationthread-` prefixes. Normalize each op to the same `entries → tweet_results.result` walk; a fixture per op pins the shape.

**Cursor pagination + end-of-feed (deep reach — the key advantage over FB).** The **Bottom cursor** is the entry with `content.entryType == "TimelineTimelineCursor"` and `cursorType == "Bottom"`. To page: set `variables.cursor` to its value and refire. **The sole positive EOF signal is a non-advancing cursor:** stop when the returned Bottom cursor equals the one just sent (combined with `rest_id` de-dup); a page with **zero tweet entries but a *new* cursor means continue, not stop** (X occasionally returns a thin page mid-timeline); also stop when `--limit`/`--since` or the request budget is satisfied. Never stop on a single thin/empty page.

**Rate limits (real numbers, twikit `ratelimits.md`, per 15-min window):** `UserTweets` 50, `UserTweetsAndReplies` 50, `SearchTimeline` 50, `TweetDetail` 150, `UserByScreenName` 95. The read loop honors `x-rate-limit-remaining`/`x-rate-limit-reset`; on 429 it raises `RateLimitedError` (with reset) or, with `--wait-on-limit`, sleeps until reset (§9/§10).

## 9. Guardrails (conservative soft defaults + one per-process floor)

Defaults in `config.py`; wording matches behavior (no "enforcement" the code doesn't back):
- **Per-process minimum inter-request delay:** a floor (**≥ 0.5 s** between GraphQL reads) that cannot be set to 0 within a run (a `0` is silently raised with a stderr note). This **discourages bursting from a single invocation and protects the user's own account** — it is a *per-process* floor, **not a global rate ceiling** (N concurrent invocations are outside the tool's control; §1 files the mass-scrape *capability* non-goals under structural absence-of-code, not under this floor).
- **Rate-limit aware by default:** honor `x-rate-limit-reset`; on 429 either stop cleanly with a partial result + `RateLimitedError` (default) or wait-until-reset (`--wait-on-limit`, §10). Never blind-retry a 429.
- **Soft, overridable:** randomized human-like pause between reads (default e.g. `(1.0, 3.0)` s); a per-run **request budget** (`--max-requests`, default modest) so one invocation can't walk an entire prolific account unattended.
- **Structural (absence-of-capability):** single session per run; **one target per invocation** (no batch list); no built-in scheduler/daemon/loop; **no multi-account rotation, proxy-for-scale, or CAPTCHA-solving code exists** — this is what makes the §1 non-goals true in code.
- **Account guidance:** README + first-run notice recommend a dedicated/throwaway account, low volume, and running from the **same network/IP** where the session was established (§7). Honest note: deep `--since`/`--limit` runs make more requests and **raise both rate-limit and account-flag risk** — the safe operating point (shallow, recent, low-frequency) and deep-archival pull in opposite directions.

*(Session longevity and steady-state cadence to be refined from the background research pass — §23.)*

## 10. CLI surface (`scrape-x`)

```
scrape-x --version
scrape-x login    [--profile NAME] [--profile-dir PATH] [--cookies FILE]
scrape-x status   [--profile NAME] [--json]
scrape-x setup                                  # provision scrapling's browser into an ISOLATED path; idempotent verify
scrape-x doctor   [--profile NAME] [--refresh]  # authenticated round-trip + query-id freshness (and, with --refresh, browser-free re-anchor)
scrape-x fetch  <username_or_url>               # a profile's tweets
    [--replies]  [--limit N] [--since YYYY-MM-DD] [--until YYYY-MM-DD]   # all three bounds compose (§11)
    [--format json|ndjson]  [--output PATH]     # default output: a non-repo platformdirs path (NOT stdout, NOT cwd)
    [--wait-on-limit] [--max-wait SECONDS]      # sleep until rate-limit reset (bounded by --max-wait if given) — §9
    [--by screen_name|id]                       # disambiguate a bare all-digit identifier
    [--profile NAME] [--profile-dir PATH] [--raw] [-v]
scrape-x search <query>  [--product latest|top] [--limit N] [--since] [--until] [--format] [--output] [--wait-on-limit] ...
scrape-x tweet  <tweet_url_or_id>  [--replies]  [--format] [--output] ...
```

- **Synopsis convention:** `|` is reserved for **exclusive** enums (`json|ndjson`, `latest|top`, `screen_name|id`); independently-combinable bounds use separate `[…]` brackets (so `--limit` + `--since` + `--until` read as composable — they are, §11).
- **`--wait-on-limit` semantics:** on entering a wait, print `waiting <N>s until rate-limit reset (<HH:MM UTC>)` to stderr (so a shelled-out caller can tell "waiting" from "hung"); the wait is a single window (≤ ~15 min) and is **bounded by `--max-wait <seconds>` if given** (else unbounded — document the recommendation to set it or a caller-side timeout); `--max-requests` is the **hard stop even under `--wait-on-limit`** (budget counts across waits, evaluated before sleeping); if the run stops on the budget/limit while rate-limited, that is exit 3.

**Exit codes** (for `fetch`/`search`/`tweet` and, where applicable, `status`): `0` success — **including a valid target whose feed is genuinely empty** (`feed_exhausted`) and a **search that legitimately matched nothing** (`no_matches`); `2` login required/expired **or soft-locked** (→ `scrape-x login`); `3` rate-limited before completion (partial result written; retry after reset); `4` **structural parse/query-id drift only** — the envelope walk cannot locate the `instructions/entries/cursor` anchors (NOT merely an empty entries list); `5` target unavailable (suspended/protected/nonexistent); `7` partial: `--since` not reached before the request budget/limit; `1` other. A zero-result run is routed to exit 0/2/4/5 by the §11 decision, never blanket-4. On success/partial, a one-line stderr summary states count, observed date range, **stop reason**, and whether the window was fully reached.

**Identifier validation — normalize then validate.** For a URL: `urllib.parse` it, default the scheme to `https` (permit `http`), strip a leading `www.`/`mobile.`/`m.` subdomain, then **reject unless the host is exactly `x.com` or `twitter.com`** (preserves the anti-SSRF guarantee); discard query string and fragment; from the path accept both `/<handle>/status/<id>` and the handle-less `/i/web/status/<id>`, take the numeric segment after `/status/`, and ignore known trailing suffixes (`/photo/N`, `/video/N`, `/analytics`). This accepts the URLs users/LLMs actually paste (`?s=20`, `/photo/1`, `m.` subdomains) instead of rejecting them. For a bare token: `@?[A-Za-z0-9_]{1,15}` username or all-digit id, with the **id-by-default** precedence for all-digit tokens (§5). Protected/suspended/nonexistent → `ProfileUnavailableError` (exit 5), not exit 4.

## 11. Retrieval semantics

- `--limit N`: stop after N tweets (deduped by `rest_id`), newest first.
- `--since D` / `--until D`: a **date window** on `created_at`. Timeline is newest→oldest; `--until` skips newer-than-D, `--since` stops once below D. **`--limit` composes** with the window (first trigger wins).
- **`created_at = None` semantics (no `None`-vs-`datetime` comparison, ever):** a tweet with `created_at=None` is **never** included in a `--since`/`--until` comparison and is **never** used as the stop-condition anchor (the "newest non-pinned `created_at`" is taken over non-None values only); in ordering it takes a deterministic end position. Prevents the `TypeError` that would abort a run mid-fetch.
- **Deep reach is real (unlike FB).** Cursor pagination walks far back (proven), bounded by the request budget (§9) and X's rate limits (§8). A far-back `--since` may stop at the budget/limit/rate-limit; the tool **tracks why** (`limit_reached | since_crossed | feed_exhausted | no_matches | max_requests | rate_limited | soft_locked`) and exits `7` when it stops before crossing `--since`.
- **Pinned tweets** come from the `TimelinePinEntry` instruction (§8), out of chronological order; base the stop condition on the newest **non-pinned, non-None-dated** tweet, and always return pinned tweets (flagged) regardless of the window.
- **Zero-result decision (replaces the old blanket "empty → exit 4"):** when a run yields no tweets, `retrieve.py` decides:
  1. envelope failed to parse against the anchored `instructions/entries/cursor` paths → **exit 4** (real drift → re-anchor via `doctor --refresh`/`harvest_queryids.py`);
  2. envelope parsed fine but the target resolved and its feed is simply empty → **exit 0** (`feed_exhausted`); for `search`, an empty parsed `SearchTimeline` → **exit 0** (`no_matches`);
  3. a cheap viewer-lookup shows the session is soft-locked/expired → **exit 2** (`scrape-x login`);
  4. target protected/suspended/nonexistent → **exit 5**.

## 12. Durability against X changes

Harvest-then-replay removes the token-and-txid treadmill, not response-shape drift. Surfaces and mitigations:
- **Query-id / feature-flag rotation** (most common): X rotates GraphQL query-ids roughly **every 2–4 weeks** as a deliberate anti-scraping measure, and hardcoded clients then **fail silently or return empty results** (e.g. [gallery-dl#9275](https://github.com/mikf/gallery-dl/issues/9275), SearchTimeline id rotated → 404; the ecosystem reports ~10–15 hrs/month re-fixing hardcoded ids). We stay fresh on **both** paths — `scrape-x login` harvests at login; cookie-import/browser-free users re-anchor via `queryids.py` fetching x.com `main.js` over `httpx` (`doctor --refresh` / `harvest_queryids.py`). Shipped defaults are a fallback with a committed fast release cadence for id bumps. (This is also why exit 4 must mean *parse failure*, not *empty result* — §11: a silently-rotated id yields an empty parse, which the pre-exit-4 probe and re-anchor path are built to catch.)
- **Response-shape drift**: the anchor-based parser keys on stable-ish paths (`rest_id`, `legacy.*`, `tweet_results.result`, the `instructions/entries/cursor` envelope) and degrades before it breaks; **exit 4 fires only on a genuine parse failure** (anchors not locatable), not on an empty-but-parsed feed (§11); `record_fixture.py` + fixtures for re-anchoring.
- **Soft session invalidation** (X returns 200-empty instead of 401): handled at the retrieval layer by the pre-exit-4 viewer-lookup probe (§7, §11) so it maps to exit 2, not a misleading drift hint.
- **`x-client-transaction-id` enforcement returns** (not today): the mitigation is a browser-observe read path reusing `session.py`'s stealth browser (like `scrape-fb`). **This path is *not* built or tested in v1** — it is the documented degradation route, not a shipped capability (§3, §13). We promise a fast re-anchor path and this route, not stability — the realistic ceiling for solo-maintained internal-API scraping.

## 13. Testing

- **CI (public, safe): fixture-based unit tests.** `tests/fixtures/*.json` are **synthetic skeletons built PII-free by construction** — never a mutated real capture. `record_fixture.py` writes a real capture to a **gitignored `scratch/*.raw.json`** locally; `build_fixture.py` extracts *only the asserted values* (allowlist) into a hand-authored envelope. Tests assert: per-op envelope walk (UserTweets, SearchTimeline, TweetDetail); **all-instructions parse incl. the `TimelinePinEntry` pinned tweet**; cursor extraction + non-advancing-cursor EOF (thin-page-continues); `note_tweet` long-text preference; **retweet text resolved from the original node** (retweet-of-a-long-tweet fixture); `TweetWithVisibilityResults` unwrap; retweet/quote one-level nesting; **`view_count` string→int and no-count/no-views → None** (a fixture lacking `views`); **`created_at=None` handling** in sort/window (a None-dated fixture, run must not crash); date-window + `--limit` composition; identifier normalize/validate (URL variants + numeric-id precedence).
- **Mandatory PII/secret gate.** Pre-commit hook + CI job grep committed fixtures for high-entropy strings, real `pbs.twimg.com`/`video.twimg.com` hosts, `auth_token`/`ct0`/bearer-shaped tokens, unicode names, phone/email patterns, and any numeric id not on a synthetic allowlist — **fail on any hit**. Every fixture diff gets human review.
- **Wheel-install smoke (CI):** `pip install` the **base** wheel (no `[browser]`) into a clean venv and run `scrape-x --version` / `--help` — this specifically catches the eager-scrapling-import regression (§14): the base install must import and run the cookie-import path with scrapling absent.
- **Live integration (local opt-in, never public CI):** `tests/live/` (SFX_LIVE_TESTS=1, throwaway account, outputs gitignored) re-checks the **no-txid replay** and **cursor pagination** invariants against live X (proven once on 2026-07-05; live tests catch the day X changes its mind). *(This does not test the unbuilt browser-observe fallback — §12.)*

## 14. Packaging, dependencies, platform

- **`pyproject.toml`** (hatchling): `name="scraper-for-x"`, `version="0.1.0"`, `requires-python=">=3.11"` (tested 3.11–3.13), `[project.scripts] scrape-x = "scraper_for_x.cli:main"`, `classifiers` incl. `Operating System :: MacOS`.
- **Two install profiles + LAZY browser import (both mandatory, or the split is broken).** Base `pip install scraper-for-x` pulls only `httpx` + `platformdirs` and supports the **cookie-import read path + browser-free query-id re-anchor** with **zero browser**; `pip install scraper-for-x[browser]` adds `scrapling[fetchers]` for `scrape-x login`/`setup`. **For this to work, `scrapling` MUST be imported lazily** — function-locally inside `session.py`'s `login()`/`setup()` bodies, **never** at module top of `session.py`, `cli.py`, or `__init__.py`; `__init__.py` must not eagerly import the login machinery in a way that pulls scrapling; `cli.py` defers the scrapling-touching import until a browser subcommand runs and, on `ImportError`, prints a clear "run `pip install scraper-for-x[browser]` (or use `--cookies`)" message. Without this, a base install `import scraper_for_x` raises `ImportError` and **every** command (`--version`, `status`, `from_cookies`) crashes on startup — the wheel-install smoke test (§13) guards it.
- **Dependencies:** `httpx` (the read client — a real runtime dep, unlike FB; bundles `certifi`, avoiding the macOS stdlib-`urllib` cert failure the probe hit), `platformdirs`; `scrapling[fetchers]>=0.4.10,<0.5` only under the `[browser]` extra. Parse dates with a small X-timestamp parser, not `dateutil`.
- **Browser install & isolation (`scrape-x setup`).** Only for the login path. Provision scrapling's Chromium into an **isolated `PLAYWRIGHT_BROWSERS_PATH`** under the package's own data dir (never shared with `scrape-fb` or the skill). `channel="chrome"` uses system Google Chrome (validated live); `doctor` verifies by launching.
- **Platform.** macOS first-class/tested. The `httpx` read + cookie-import path is OS-agnostic (a cheap Linux CI leg for fixture+base-wheel+`--version` makes that real); only the login browser is macOS-tuned in v1. Windows unsupported v1.
- **Versioning:** SemVer from `0.1.0`; `CHANGELOG.md`.

## 15. Publishing (Trusted Publishing, no token)

- **Release-triggered OIDC (same as FB, already set up).** `publish.yml` fires on **`release: published`** (not a bare tag push), runs `check_tag_version.py`, builds sdist+wheel, publishes via `pypa/gh-action-pypi-publish` **pinned to a full commit SHA**, with `permissions: id-token: write`. The **pending publisher is already created**: project `scraper-for-x`, owner `tjdwls101010`, repo `Scraper-for-X`, workflow `publish.yml`, environment blank (all) — confirmed by the user. Release: bump `version` + `CHANGELOG.md` → commit/push → `gh release create vX.Y.Z`. Mirror the FB package's proven `publish.yml` exactly (it is known-good).
- **No stored token anywhere.** Revoke + verify-dead the earlier exposed PyPI tokens (carried from the FB work); OIDC needs none.

## 16. Integration with the `web-fetch` skill

The skill consumes `scrape-x` as an external CLI, exactly like `scrape-fb`:
- **Setup:** `uv tool install "scraper-for-x[browser]"` (login path) or base for cookie-import; health check runs `scrape-x doctor`.
- **Login:** SKILL.md instructs `scrape-x login` once (headed stealth) or cookie-import; the skill then runs read-only `httpx`.
- **Tier fetch:** the skill shells out to `scrape-x fetch|search|tweet … --format json --output <gitignored path>`, reads the JSON, renders a markdown preview, saves under `./.tmp/web-fetch/` (gitignored). Standalone `scrape-x` defaults `--output` to a non-repo path so a direct run can't drop PII into a tracked location.
- **PLAN.md edits (applied when this plan is executed):** record X as a delegated external tool (like Facebook); carry over the redaction rule (§21).

## 17. Gotchas (X-specific — each a proven trap)

- **G-webdriver-login:** X **blocks login** when `navigator.webdriver=true` (proven live; FB's vanilla-Playwright stack would be blocked too). Working stealth config: `channel="chrome"`, `--disable-blink-features=AutomationControlled`, `ignore_default_args=["--enable-automation"]`, `add_init_script` overriding `navigator.webdriver`. Login **must** use it; reads (httpx) don't care.
- **G-no-txid:** X read GraphQL does **not** require `x-client-transaction-id` (proven). **Do not reproduce it** — re-adding it re-imports twikit's fragility for nothing.
- **G-queryid-rotation:** query-ids rotate. Re-anchor **browser-free** via x.com `main.js` (`doctor --refresh`/`harvest_queryids.py`) so both entry paths stay fresh; don't hardcode-and-forget.
- **G-bearer-static:** the `Authorization: Bearer` token is the public web constant (not secret) — a documented constant. (`ct0` *is* secret; the bearer isn't.)
- **G-ct0-csrf:** the `ct0` cookie value must also be sent as `x-csrf-token`, or X 403s.
- **G-note-tweet + G-retweet-text:** long text is in `note_tweet`, not `legacy.full_text`; and for a **retweet** the outer `full_text` is the truncated `"RT @…"` stub — resolve text/media/urls from `retweeted_status_result`'s original node.
- **G-view-count-none:** `views.count` is a string and often absent; extract None-safely or the whole page parse crashes.
- **G-pin-instruction:** the pinned tweet is a separate `TimelinePinEntry` instruction (singular `entry`), not inside `TimelineAddEntries` — iterate all instructions or you silently drop it.
- **G-visibility-wrapper:** restricted/subscriber tweets are `__typename: "TweetWithVisibilityResults"`; unwrap `.tweet`.
- **G-user-core:** author `name`/`screen_name`/`created_at` are under `user.core` (2026), counts under `user.legacy` — twikit's `legacy.name` is partially stale.
- **G-cursor-eof:** paginate on the Bottom cursor; EOF = the cursor stops advancing (with rest_id de-dup), **never** a single thin/empty page.
- **G-soft-lock:** a stale session can return **200-empty**, not 401 — probe with a viewer-lookup before calling a zero-result "drift" (§7/§11).
- **G-ip-origin:** replay from the **same network/IP** the session was minted on; an abrupt IP change (esp. cookie-import → VPS/VPN) can soft-lock the session.
- **G-ratelimit:** honor `x-rate-limit-reset`; never blind-retry a 429; limits are modest (50 for UserTweets/Search).
- **G-session-cred:** the stored `auth_token`+`ct0` is a live, password-less session — dir `0700` **and** file `0600`, no sync/backup, revoke-by-logout.
- **G-ban:** automating an X account violates ToS; dedicated/throwaway account, low volume, no loops.
- **G-media-expiry:** `pbs.twimg.com`/`video.twimg.com` URLs — confirm during implementation whether they are signed/expiring (Twitter media historically is *not* signed, unlike FB's `scontent`, but verify); treat as sensitive in diagnostics regardless.
- **G-lazy-import:** `scrapling` must be imported lazily (inside login/setup) so the base (browser-free) install runs — a top-level import breaks `--version` and the cookie-import path (§14).

## 18. Build order (implementation session)

1. Scaffold the (already-initialized) repo: `pyproject.toml` (classifiers, `scrape-x` entry point, `[browser]` extra), `LICENSE` (MIT), `DISCLAIMER.md`, README skeleton, scoped `.gitignore` (fixtures un-ignored **by name**), ruff + pre-commit (incl. PII/secret scan), `ci.yml` + `publish.yml` (mirror FB's). Verify `scrape-x` collides with no installed command.
2. `auth.py` (session store, dir 0700 + file 0600, identifier normalize/validate, cookie-import contract) + `queryids.py` (defaults + browser-free `main.js` re-anchor) + `gql.py` (endpoint templates, bearer constant) + `session.py` (stealth login **with lazy scrapling import** + cookie-import; status; doctor `--refresh`) + `scrape-x login`/`status`/`setup`/`doctor`. **Confirm the base install imports with scrapling absent** (wheel-smoke early).
3. `client.py` (httpx GET, headers, 429/rate-limit) + `parse.py` (all-instructions walk incl. pin-entry, per-op roots, cursor) + `model.py` (Tweet/User/Media incl. retweet-original text, None-safe view_count). Fixture-driven.
4. `retrieve.py` (limit/since/until compose, cursor loop with non-advancing-cursor EOF, None-created_at-safe, pinned handling, pre-exit-4 viewer-lookup probe, stop-reason, exit-code mapping incl. 5/7) + `cli.py` (`fetch`/`search`/`tweet`, composable-bounds synopsis, `--wait-on-limit`/`--max-wait`, `--by`, non-repo default output, redaction-scrubbed diagnostics).
5. Tests: synthetic fixtures (per op + pin + retweet-of-long + no-views + None-created_at) + PII/secret gate + unit suite + **base-wheel smoke (scrapling absent)** + live opt-in (no-txid + cursor invariants).
6. Packaging polish; finish `publish.yml`; **revoke+verify the exposed tokens**; confirm the pending publisher; `gh release create v0.1.0`.
7. Skill integration: apply §16 edits to PLAN.md; the skill installs the *published* `scrape-x` via `uv tool` and calls `fetch`/`search`/`tweet`/`doctor`.

## 19. Roadmap (post-v1)

Followers/following lists; home timeline (For You / Following); likes/bookmarks; lists; communities; notifications; trends; media file download; incremental `--since-last` with per-profile state; a Linux/Windows first-class login path; and — only if X starts enforcing txid — actually building + testing the browser-observe fallback (§12). **No write actions, ever** (permanent non-goal). Each added read surface widens the break-and-maintain area — add deliberately. *(Prioritization to be informed by the ecosystem research pass — §23.)*

## 20. Open questions (to resolve in review / implementation)

- Exact `note_tweet` / `TweetWithVisibilityResults` / retweet nesting across all three ops (fixtures will pin; the probe saw UserTweets clearly, less so search/thread).
- Whether `pbs.twimg.com` media URLs are signed/expiring (G-media-expiry) — verify live during implementation.
- The exact `main.js` regex for query-id/feature extraction, and how stable that extraction is across X web builds (validate during implementation; defaults are the fallback).
- Session/cookie longevity before X invalidates, and the safest steady-state cadence — pending the research pass.

## 21. Redaction / diagnostics (single scrub path)

One `redact.py` helper routes **all** output that could carry secrets/PII: `-v`, error/drift dumps, `--raw`, anything to stdout/logs/context. On the **response side**, drop `auth_token`/`ct0`/bearer/`x-csrf-token`-shaped fields and strip `pbs.twimg.com`/`video.twimg.com` query strings, truncate/redact names and tweet text in diagnostics. On the **input side** (the leak the FB package never had): **cookie-import parse/validation errors** (`from_cookie_file`/`from_cookies`, Netscape/JSON/cURL parsing) must never echo a raw cookie line or credential value — they report only structural/positional context ("line 3 malformed", "ct0 failed shape check") with any `auth_token`/`ct0`/bearer/`csrf`-shaped value redacted, because at parse-failure time the value is a raw line, not a named field. Raw unredacted output requires `--no-redact` + an on-screen PII warning; `-v` never dumps raw bodies.

## 22. Security & threat model (summary)

- **On-disk session credential:** `auth_token`+`ct0` is a live, password-less session → dir `0700` **and** file `0600` (re-asserted every write, incl. cookie-import), no sync/backup, revoke-by-logout; a `--profile-dir`/`SFX_PROFILE_DIR` override warns on stderr (§7).
- **Cookie-import ingestion:** validate token shapes before storing/firing; don't retain the source export; warn it's still live; redaction-safe parse errors (§7, §21).
- **Input as a request primitive:** normalize+validate username/id/URL (host must be `x.com`/`twitter.com`) before it reaches the read client (§10).
- **Network origin:** same-IP operational assumption to avoid soft-locking a session (§7, §9).
- **Diagnostics leak:** single redaction path covering both response bodies and input-side parse errors; session tokens + media URLs sensitive; bearer is public, `ct0` is not (§21).
- **Supply chain:** isolated browser install (login path only), pinned scrapling range, reproducible-CI lockfile, stated trust assumption (§14).
- **Publish integrity:** verified token revocation, pending-publisher OIDC with `id-token: write`, SHA-pinned action (§15).
- **Fixtures:** synthetic-by-construction + mandatory PII/secret CI gate + **by-name** `.gitignore` un-ignore (§4, §13).

## 23. Review provenance

This plan is grounded in: (a) a source inventory of `twikit` (~101 methods, its transaction-id/ui_metrics fragility, MIT license) and the user's `serenity` scraper (real twikit pain points); (b) a **live probe against a real logged-in X session on 2026-07-05** that established the decisive facts — X blocks `webdriver=true` login (stealth config required), read GraphQL needs **no** `x-client-transaction-id`, cursor pagination works over `httpx`, and the current query-ids + 2026 data model; (c) user decisions: read-only; dist `scraper-for-x` / command `scrape-x` / repo `Scraper-for-X`; architecture **C (harvest-then-replay hybrid)**; auth = **both** stealth-login (default) and cookie-import; v1 read surfaces = **user tweets/replies/media, search, single tweet+thread**; (d) an **adversarial multi-lens review** (6 lenses × find-then-adversarially-verify): 33 findings raised, 5 rejected, **28 incorporated** — 2 blockers (cookie-import query-id refresh must be browser-free on both paths; the `[browser]` extra requires lazy scrapling import), 9 high, 14 medium, 3 low, folded into §§0–22. A background research pass (competitors, X anti-bot trend, litigation landscape, session longevity, ecosystem) stalled mid-run (a hung web fetch), but two decisive facts it surfaced were independently verified and folded in: twikit's transaction-id generator broke in the wild on 2026-03-18 ([twikit#408](https://github.com/d60/twikit/issues/408)), and X rotates GraphQL query-ids every ~2–4 weeks causing silent empty results ([gallery-dl#9275](https://github.com/mikf/gallery-dl/issues/9275)) — both corroborating the live-probe architecture. Deeper legal-citation and session-longevity detail for §2/§9 remains a follow-up.
