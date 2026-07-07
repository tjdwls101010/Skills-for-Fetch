# `scraper-for-facebook` — Implementation Plan (v1)

> Status: PLAN (hardened). Implementation happens in a separate future Claude session.
> This document lives in `docs/plan/` alongside [PLAN.md](PLAN.md) (the `web-fetch` skill plan). The `web-fetch` skill *consumes* this package as an external CLI (`scrape-fb`), the way it consumes `gh` / `yt-dlp`.
> This revision folds in 44 findings from a 6-lens adversarial review (see §23). Where a decision below reverses a naive first draft, that is deliberate.

## 0. Why this package exists

The single hardest, most novel capability the `web-fetch` skill needs is pulling posts from **logged-in personal Facebook profiles** — profiles you follow that (a) the Graph API can't reach and (b) show a login wall when logged out. We proved during planning that this works self-contained: drive a stealth browser against your own persisted logged-in profile, scroll the target, and **observe the `/api/graphql` XHR responses the browser itself makes** — then parse posts out of those JSON bodies. No token replay, no credential injection: the browser generates `fb_dtsg`/`doc_id`/`lsd` itself, so there is no token-maintenance treadmill.

Rather than bury this inside the skill, we extract it into a standalone, general-purpose open-source Python package. The skill stays thin and calls `scrape-fb` as a tool. The package is independently useful, testable, and versioned.

Honest framing (carry into the README, do not oversell): this is **not** first-of-kind. `facebook-graphql-scraper` (FaustRen, PyPI, updated 2026) does approximately the same thing — Selenium + `selenium-wire` capturing GraphQL, with login. Our differentiation is *incremental, not categorical*: (1) we reuse a **persisted browser-login profile** (`user_data_dir`) instead of injecting username/password — safer and less breakable; (2) we build on **scrapling's** modern stealth stack (patchright/Chromium) instead of the largely-unmaintained `selenium-wire`. State this plainly.

## 1. Scope

**In scope (v1):**
- Facebook only.
- Personal-profile **timeline posts**: author, body text (full, truncation-resolved), creation time, permalink, media URLs, link attachments, reaction/comment/share counts, post type, pinned flag, edited time, and the shared/quoted post (one level).
- Retrieval by **count** (`--limit N`) and by **date window** (`--since` / `--until`), the latter *best-effort* within a scroll budget (§11 is explicit about the limit).
- Output as **JSON** (default) and **NDJSON** (streaming).
- One-time interactive login persisted to a dedicated profile; headless reuse thereafter.

**Explicitly out of scope for v1 (roadmap):** Threads, Instagram; comment threads and reactor lists; media file download (URLs only in v1); incremental `--since-last` state; groups/pages/photo albums; guaranteed deep-history reach; non-macOS first-class support (§14).

**Non-goals (permanent):** no credential-injection login; no built-in batch/multi-profile crawling; no bundled proxy rotation or CAPTCHA-solving-for-scale; nothing marketed as detection-evasion. (These are backed by at least one non-bypassable limit — §9 — so the stance is true in code, not only prose.)

## 2. Legal, ethical, and safety posture — read first

This must be prominent in the README/DISCLAIMER and surfaced by the CLI, not buried.

- **Meta ToS.** Automating any Meta account to collect data violates Facebook's Terms of Service. Meta enforces via account bans, cease-and-desist, and litigation. Users assume this risk.
- **Maintainer exposure (not just the user).** Publishing a public package literally named "scraper-for-facebook" under a real identity ties *you, the author*, to it — PyPI Trusted Publishing binds each release to your named GitHub repo/account (here, this package's own `Scraper-for-Facebook` repo, whose name plus the PyPI package name carry the exposure directly). Meta has pursued named authors/operators of public FB/IG scrapers before. Documented enforcement skews toward commercial mass-scrapers and data brokers rather than solo personal-scale projects, so the probability for this project is uncertain — but the exposure is real and lands on the publisher, and this plan names it so the decision is informed. (You chose to keep the name and real identity; the DISCLAIMER records this exposure so the choice stays informed.)
- **Account-ban mitigation (§9).** Human-like randomized delays with a non-bypassable floor; a per-run scroll cap; single-session, single-target invocation; a clear recommendation to use a **dedicated/throwaway account**, not your primary.
- **Third-party PII — you may become a data controller.** Captured posts contain other people's personal data. Beyond "don't commit outputs," a plain-language DISCLAIMER note must state: scraping identifiable third parties can make *you* a data controller under GDPR/CCPA with real obligations (lawful basis, data-subject rights, retention); "personal use" is not automatically a lawful basis; minimize retention and delete outputs when done. (Non-legal-advice, but do not imply the MIT license cures privacy-law exposure.)
- **PII hygiene end-to-end.** Outputs are never committed/shared. The skill writes them to a gitignored path; standalone `scrape-fb` defaults output to a non-repo location and prints a privacy reminder — and the DISCLAIMER states plainly that *a printed reminder is not a technical control* (§10, §16). Committed fixtures are built PII-free by construction (§13).
- **Disclaimer + license.** MIT license; a `DISCLAIMER.md` covering all of the above. Licensing does not waive Meta's ToS or applicable privacy law.

## 3. Architecture

```
target profile URL
      │
      ▼
 scrape-fb fetch ─► Session (scrapling DynamicSession, user_data_dir, headless, capture_xhr="graphql")
      │                 │
      │                 ├─ navigate + human-like scroll (page_action), watch pagination XHRs
      │                 └─ page = session.fetch(...) ; read page.captured_xhr  (GraphQL Response objects)
      ▼                                  │
  Parser  ◄────────────────────────────┘
      │   body = resp.body.decode("utf-8","replace")  (Response.body is BYTES)
      │   split NDJSON + @defer chunks → find top-level story nodes under data.node
      │   deep-merge story fragments by feedback id → resolve truncation → normalize
      ▼
  list[Post]  ──►  JSON / NDJSON writer  (redaction-scrubbed diagnostics)
```

Core principle: **observe, don't replay.** We never construct GraphQL queries or reuse tokens. We let the real logged-in browser make its own requests and we read the responses. This removes the *token* maintenance problem but not the *response-shape* problem (§12) — and, crucially, not the *pagination-reach* problem: because we never advance the cursor ourselves, how far back we can read is bounded by how far the browser keeps paginating as we scroll (§11).

## 4. Package layout

**Its own separate repo (checked out inside the skill's working tree).** This package is a **standalone git repo** — `github.com/tjdwls101010/Scraper-for-Facebook`, distinct from the skill's `Skills-for-Fetch` repo. It is physically checked out at `package/scraper-for-facebook/` inside the Skills-for-Fetch working tree for convenience, but the parent does **not** track it: the Skills-for-Fetch root `.gitignore` lists `package/scraper-for-facebook/`, and this directory has its own `.git`. Commit/push it independently (`cd package/scraper-for-facebook`); after cloning the skill repo, clone this package into that path separately. Its CI/publish workflows live in **this repo's own** `.github/workflows/` (plain `ci.yml`/`publish.yml`, no path filters — it's a whole repo). The tree below is this package repo's root:

```
package/scraper-for-facebook/        (its own repo "Scraper-for-Facebook"; git-ignored by the parent; PyPI dist "scraper-for-facebook"; command scrape-fb)
├── pyproject.toml                   (hatchling; requires-python >=3.11; scripts: scrape-fb; classifiers incl. OS)
├── README.md                        (usage + prominent §2 disclaimer + isolated-install instructions)
├── LICENSE                          (MIT)
├── DISCLAIMER.md                    (ToS, maintainer exposure, PII/controller, ban, session-credential warning)
├── CHANGELOG.md                     (Keep a Changelog; starts 0.1.0)
├── .gitignore                       (see below — scoped, with an explicit un-ignore for fixtures)
├── .pre-commit-config.yaml          (ruff + a fixture PII/secret scan hook)
├── requirements-dev.lock            (pinned dev/CI deps for reproducible CI — NOT the published dep spec)
├── .github/workflows/
│   ├── ci.yml                       (ruff + pytest fixtures + build + wheel-install smoke + fixture PII scan)
│   └── publish.yml                  (on tag v* → build → PyPI Trusted Publishing/OIDC; id-token: write; SHA-pinned action)
├── src/scraper_for_facebook/        (import name scraper_for_facebook)
│   ├── __init__.py                  (exports FacebookScraper, Post, Media, LinkAttachment, errors)
│   ├── session.py                   (scrapling wrapper; login, status, capture; isolated browser path)
│   ├── scroll.py                    (human-like scroll page_action; pagination-XHR-aware stop signals)
│   ├── parse.py                     (bytes-decode, NDJSON/@defer split, top-level story finder, deep-merge)
│   ├── truncation.py                (detect truncation marker; permalink-refetch fallback)
│   ├── model.py                     (Post, Media, LinkAttachment; normalization; to_dict)
│   ├── retrieve.py                  (limit + since/until; stop-reason tracking; pinned/unavailable handling)
│   ├── profiles.py                  (platformdirs storage, 0700 perms; resolve/list; identifier validation)
│   ├── redact.py                    (single scrub helper for ALL diagnostic/log/raw output)
│   ├── config.py                    (defaults + non-bypassable floors; env/flag overrides)
│   ├── errors.py                    (LoginRequiredError, SessionExpiredError, ChallengeError, ProfileUnavailableError, SessionClosedError, ...)
│   └── cli.py                       (argparse: login / status / setup / fetch / doctor; --version)
├── tests/
│   ├── fixtures/*.ndjson            (SYNTHETIC skeletons — built PII-free by construction; read as bytes in tests)
│   ├── test_parse.py, test_truncation.py, test_retrieve.py, test_model.py, test_redact.py, test_identifier.py
│   └── live/                        (opt-in; SFB_LIVE_TESTS=1; never in CI; outputs gitignored)
└── scripts/
    ├── record_fixture.py            (local: capture real bodies to a gitignored *.raw.ndjson under scratch/)
    └── build_fixture.py             (extract only asserted values → inject into a hand-authored synthetic skeleton)
```

**`.gitignore` (get this exactly right — it is a PII trap otherwise):** ignore raw captures under a distinct, unambiguous name/home — `scratch/` and `*.raw.ndjson` — and ignore runtime outputs (`*.json` outputs, `profiles/`, `.venv`). Then **explicitly un-ignore the committed fixtures**: `!tests/fixtures/` and `!tests/fixtures/*.ndjson`. Never rely on a bare `*.ndjson` rule that both the safe fixture and the unsafe raw capture would match — that forces `git add -f`, which is the exact operation that silently commits an un-redacted capture.

## 5. Public Python API (general-OSS-first)

```python
from scraper_for_facebook import FacebookScraper, Post, Media, LinkAttachment
from scraper_for_facebook.errors import (
    LoginRequiredError, SessionExpiredError, ChallengeError,
    ProfileUnavailableError, SessionClosedError,
)

# One-time interactive login (opens a HEADED browser; you log in by hand; profile persists).
# login() is an INSTANCE method so construction options (profile_dir, config) flow through ONE path.
FacebookScraper(profile="default", profile_dir="/custom/dir").login()
# Convenience classmethod shim with the SAME keyword surface:
FacebookScraper.login(profile="default", profile_dir="/custom/dir")

url = "https://www.facebook.com/<user>"
with FacebookScraper(profile="default") as fb:                 # headless reuse
    posts: list[Post] = fb.fetch_profile(url, limit=30, since="2026-01-01", until="2026-06-30")
    for post in fb.iter_profile(url, limit=30):                # streaming generator (consume INSIDE the with-block)
        ...

FacebookScraper(profile="default").status()                    # -> Status.LOGGED_IN | EXPIRED | CHECKPOINT (enum)
```

Design notes:
- `FacebookScraper(profile="default", *, headless=True, profile_dir=None, config=None, scroll_pause=(2.0, 4.0), max_scrolls=40)`.
- `login()` is the canonical instance method; the classmethod is a thin shim taking the identical keywords. This closes the "classmethod can't receive `profile_dir`" gap — a library user with a custom `profile_dir` logs into and fetches from the *same* directory. The CLI `--profile-dir` maps to it on both `login` and `fetch`.
- `fetch_profile(...)` returns a materialized `list[Post]`; `iter_profile(...)` yields incrementally so long runs stream and stop as soon as `limit`/`since` is satisfied.
- **Generator lifecycle (specified, not left to chance):** the generator drives the browser and only makes progress while its owning `with` block is open. Early `break`/`.close()` cleanly stops scrolling and is safe. Iterating it *after* the context exits raises `SessionClosedError` (not an opaque browser error). Consume it within the `with` block; the docstring says so.
- Accepts a full profile URL **or** a bare vanity/username or numeric id — after **validation/normalization** (§10, identifier rules). Invalid input is rejected, not best-effort-expanded.
- Errors are typed (`errors.py`) so library users branch; the CLI maps them to messages + exit codes (§10).
- `raw=True` (opt-in) attaches the raw story node for debugging; it carries PII, so it is off by default and its output is redaction-scrubbed unless the caller explicitly opts out with an on-screen warning (§21).

## 6. `Post` / `Media` / `LinkAttachment` schema

Decide the schema **now** while pre-1.0; additive fields later are a minor bump, but reinterpreting existing output is breaking.

```python
@dataclass
class Media:
    kind: str            # "image" | "video" | "unknown"
    url: str             # scontent/fbcdn URL — SIGNED, EXPIRES, viewer-scoped (treat as sensitive — §21, G-media-expiry)
    width: int | None = None
    height: int | None = None

@dataclass
class LinkAttachment:
    url: str             # external/shared link target
    title: str | None = None
    description: str | None = None

@dataclass
class Post:
    id: str                       # feedback id (stable identity; dedup/merge key)
    url: str | None               # permalink (story.wwwURL)
    type: str                     # "status" | "photo" | "video" | "shared" | "link" | "reel" | "life_event" | "unknown"
    is_pinned: bool               # pinned/featured to top regardless of date (drives §8/§11 stop logic)
    author_name: str | None
    author_url: str | None
    author_id: str | None
    created_at: datetime | None   # from creation_time (unix int, UTC) — see §8 provenance + None handling
    edited_at: datetime | None
    text: str                     # full body (message.text), truncation-resolved ("" if none)
    text_truncated: bool          # payload was truncated (from a payload marker), regardless of resolution
    text_resolved: bool           # a fallback fetched the full body
    media: list[Media]
    links: list[LinkAttachment]   # external/link-share attachments (else a link post serializes empty)
    reaction_count: int | None
    comment_count: int | None
    share_count: int | None
    shared_post: "Post | None"    # attached_story (quoted/shared), one level; consumed from the tree, never emitted top-level
    captured_at: datetime         # UTC
    raw: dict | None = None       # only if raw=True
```

JSON output is `[Post.to_dict(), ...]`; NDJSON is one `Post.to_dict()` per line. Datetimes serialize as ISO 8601 UTC. A run also carries **metadata** (stop reason, whether the requested window was fully reached, counts) — emitted to stderr and, for NDJSON/JSON, available as a trailing/summary record so a caller can tell a complete window from a truncated one (§11).

## 7. Login & session model

- **Storage & permissions.** `platformdirs.user_data_dir("scraper-for-facebook")/profiles/<name>` (e.g. `~/Library/Application Support/scraper-for-facebook/profiles/default` on macOS). Overridable via `--profile-dir` / `SFB_PROFILE_DIR`. **Create the profile dir mode `0700` under a restrictive umask** — this directory is a *live, 2FA-satisfied Facebook session* (cookies + localStorage): anyone who can read it has authenticated account access with no password. The DISCLAIMER states plainly: do not back up, sync (Time Machine / cloud-mapped home / Dropbox), or share this directory; if the disk is lost, revoke by logging the session out on facebook.com. It is *less* protected than a normal browser's keychain-encrypted cookies (connect this to G-keychain, don't leave it as a mere UX note).
- **`scrape-fb login [--profile NAME] [--profile-dir PATH]`.** Opens a **headed** `DynamicSession(user_data_dir=<profile>, headless=False)`, navigates to `https://www.facebook.com/`, instructs "Log in in the browser window, then press Enter." On Enter, verifies login (no login-wall markers), persists, exits. The persistent context keyed to `user_data_dir` makes login survive across runs. (Proven in planning: a CDP-attach to an existing browser did **not** inherit login; a persistent `user_data_dir` did — G-headed-login-persist.)
- **`scrape-fb status [--profile NAME]`.** Loads the profile headless, hits facebook.com, returns `Status.{LOGGED_IN, EXPIRED, CHECKPOINT}` (an enum, not a bare string). CLI exit codes match `fetch`: `0` logged_in, `2` expired, `3` checkpoint. Also prints session age; a stale session is a soft warning.
- **Expiry during fetch.** On login-wall/checkpoint markers mid-fetch: **abort immediately** with `SessionExpiredError`/`ChallengeError`; the CLI prints `run: scrape-fb login --profile <name>` and exits non-zero. We do **not** auto-relogin (breaks headless automation) and do **not** hammer retries (raises ban risk). Vocabulary is unified: "checkpoint" everywhere for Meta account flagging (never "challenge" for that state).

## 8. Capture & parse engine

**Capture.** `DynamicSession(user_data_dir=..., headless=..., capture_xhr=r"graphql")`, then `page = session.fetch(url, page_action=<scroll>, load_dom=True, timeout=...)`. Read **`page.captured_xhr`** — pinned verbatim from the working POC (`poc_userdatadir.py`); it hangs off the object `fetch()` returns, *not* a `response` variable. Each element is a scrapling `Response`. The regex is `"graphql"` (proven); `"/api/graphql/"` under-captured (G-capture-pattern).

**Body is BYTES.** `Response.body` returns **raw bytes** (verified in scrapling 0.4.9 source: "Return the raw body of the response as bytes"). So decode explicitly before any string work:
```python
text = resp.body.decode("utf-8", errors="replace")
for line in text.split("\n"):
    ...
```
`resp.body.split("\n")` (str separator on bytes) raises `TypeError` — this is the single most load-bearing capture step, so the fixtures feed **raw bytes**, not pre-decoded strings, or the mismatch is invisible in CI and only bites on live captures.

**Body shape.** Each captured body is a **newline-delimited concatenation of JSON objects**, and content arrives across later **`@defer`** chunks, not only the first object. Parse **all** lines of **all** bodies (`json.loads` each; skip unparseable), then merge.

**Top-level story detection — anchor on `data.node`, not "any node with `message.text`".** A post that shares another yields TWO story-shaped nodes from one feed unit: the wrapper and the inner `attached_story`. If the finder treats "any object carrying `comet_sections.content.story` OR `message.text` OR `feedback.id`" as top-level, the inner shared post is emitted as a phantom standalone Post *and* attached as `shared_post` — double-counting (inflating `--limit`), polluting the timeline with content the owner didn't author, and (because the quoted post can be far older) tripping the `--since` stop early. Dedup-by-`feedback.id` does **not** catch it (the inner story keys on a different `attached_story.id`). So: treat only the **outermost story per captured `data.node`** as a top-level candidate; when you recurse into `attached_story`, **consume** it as `shared_post` and never enqueue it as a top-level candidate.

**Dedup is a DEEP MERGE, not pick-one.** `@defer` is partial-by-design: one chunk ships the story skeleton, later chunks patch in body text / reactions / media under the *same* `feedback.id`. "Keep the most complete instance" silently drops whichever fields live in the loser chunk (e.g. text in A, counts+media in B → nulls). Instead **deep-merge by `feedback.id` across all lines/bodies**: non-null wins, lists unioned and deduped by child id. A fixture must deliberately split one post's fields across two `@defer` lines and assert the merged `Post` has all of them.

**Field map (redacted, real, from planning) — a STARTING HINT, not a contract** (paths rotate; extract by searching within the resolved top-level story node):
```
post id (merge key):  <story>.feedback.id
author name/url/id:   <story>...actors[].name / .url / .id
author avatar:        <story>...actors[].profile_picture.uri
body text:            <story>...comet_sections.message.story.message.text   (also ...story.message.text — check both)
permalink:            <story>.wwwURL
creation_time:        a unix INT keyed relative to the story root (see below)
media:                ...attachment(s)...media...image.uri / ...uri
link attachment:      ...attachment(s)...(url/title/description of a shared external link)
shared/quoted post:   <story>.attached_story...  (consumed → shared_post)
```

**`creation_time` is a first-class, blocking probe (§13), not "resolve later".** It is both the sort key and the `--since`/`--until` filter key, so it cannot be vague. Match the exact `creation_time` key **relative to the resolved story root** — not "any int in the tree" (edit times, video timestamps, cache-bust ints) and not a nested `attached_story`/attachment `creation_time`. Define behavior when absent: a post with no locatable `creation_time` must not crash the sort/filter (`None < datetime` and `sorted([datetime, None])` raise `TypeError`) — treat missing as sort-last and **excluded from date-window filtering with a flag**, never silently mis-placed. A decoy-int fixture proves the probe doesn't grab the wrong int.

**Truncation — detect from a payload marker; probe multiple posts (§13).** Prefer `message.text` (usually the full body; FB expands "See more" client-side with no network request, evidence the body already shipped). But treat "`message.text` is always authoritative" as a claim to **disprove**, not assume: probe several long posts (including link/mention-heavy ones, which more often truncate server-side), and **always populate `text_truncated` from a payload marker** (`truncat*`/`*preferred_body*`/`see_more`-style key) even when a full-body field exists, so downstream can tell. On detected truncation, fall back to the post's permalink (or a captured SeeMore response) and set `text_resolved`.

**Non-post / special units (enumerate on a live sample — §13).** Real timelines interleave **pinned** posts (top, out of chronological order), **reels** (often no `message.text` but do carry `feedback.id`), suggested/non-post units, life-event/profile-update units, and shared albums. Decide per type: parse reels as media-only Posts (`type="reel"`), skip suggested units explicitly, tag pinned via `is_pinned`. Detect **memorialized ("Remembering") / blocked / restricted / unavailable** profiles from their distinct wall markers and raise `ProfileUnavailableError` (CLI exit 5) — do **not** let them collapse into "0 posts → possible parser drift" (exit 4), which misdiagnoses a correct capture.

## 9. Guardrails (conservative soft defaults + one non-bypassable floor)

Defaults live in `config.py`; most are overridable, but the wording matches the behavior (no claim of "refusal"/"enforcement" the code doesn't back):
- **Non-bypassable minimum inter-scroll delay:** the scroll pause floor is clamped to **≥ 0.5 s and cannot be set to 0** (a `--scroll-pause 0,0` is silently raised to the floor with a stderr note). This blocks the single most abusive/most-ban-inducing setting while leaving normal use unaffected — the one hard limit that makes "not a mass-scraping tool" true in code, not only in prose. It also protects the user's own account (0-delay scrolling is what gets flagged fastest).
- **Soft, overridable:** `scroll_pause` default `(2.0, 4.0)` s (randomized, human-like); `max_scrolls=40` default (a scroll-budget ceiling — see §11/§12 for the depth/ban tension).
- **Structural:** single browser session per run; **one target per invocation** (no batch profile list); no built-in scheduler/daemon/loop.
- **Account guidance:** README + first-run notice recommend a dedicated/throwaway account and low volume.

Honest note in the docs: deep `--since` runs scroll more, and **more/deeper scrolling raises checkpoint risk** — the safe operating point (shallow, recent, low-frequency) and the headline deep-`--since` feature pull in opposite directions; consider a headed default or a lower first-run depth if you value the account (G-fbheadless).

## 10. CLI surface (`scrape-fb`)

```
scrape-fb --version
scrape-fb login   [--profile NAME] [--profile-dir PATH]
scrape-fb status  [--profile NAME] [--profile-dir PATH] [--json]
scrape-fb setup                                 # provision scrapling's browser into an ISOLATED path (§14); idempotent verify
scrape-fb doctor  [--profile NAME]              # launches the browser + asserts a capture round-trips (real health, not --version)
scrape-fb fetch <profile_url_or_username>
    --profile NAME            # persisted login profile (default: "default")
    --limit N                 # max posts
    --since YYYY-MM-DD        # lower date bound (inclusive), best-effort within --max-scrolls
    --until YYYY-MM-DD        # upper date bound (inclusive)
    --format json|ndjson      # default: json
    --output PATH             # default: a non-repo path under the platformdirs data dir (NOT stdout, NOT cwd)
    --scroll-pause MIN,MAX    # seconds; MIN clamped to >= 0.5 (custom float-pair parser, clear error on bad input)
    --max-scrolls N           # scroll budget (default 40)
    --profile-dir PATH        # override storage location
    --headed                  # show the browser (debug)
    --raw                     # include raw story node (debug; PII; scrubbed unless --no-redact + on-screen warning)
    -v / --verbose            # diagnostics are redaction-scrubbed (§21)
```

**Exit codes** (specified for both `fetch` and, where applicable, `status`): `0` success (window fully reached, or `--limit` met, or feed genuinely exhausted); `2` login required/expired (→ `scrape-fb login`); `3` checkpoint; `4` zero posts + possible parser drift (hint at issue tracker only for a known-good profile); `5` profile unavailable (memorialized/blocked/restricted/nonexistent — distinct from drift); `7` partial: `--since` not reached (stopped at `--max-scrolls` or feed stall before crossing the date); `1` other. On success/partial, a one-line summary to stderr states count, observed date range, **stop reason**, and whether the requested window was fully reached — so a truncated window is never mistaken for a complete one.

**Identifier validation (before expansion).** Accept only `[A-Za-z0-9.]` vanity names → `https://www.facebook.com/<vanity>`, or `profile.php?id=<digits>`; for a full URL require scheme `https` and host in `{facebook.com, www.facebook.com, m.facebook.com}`, URL-encode the path segment, and **reject** anything else rather than best-effort-expanding it. This keeps an unvalidated string from steering the authenticated browser to arbitrary same-origin/off-domain pages, and stops garbage input from masquerading as parser drift. **Correction (found post-ship):** the `profile.php?id=<digits>` form is only accepted *inside a full `https://…` URL* — a bare `profile.php?id=123` string matches neither the numeric-id regex nor the vanity regex and is rejected with exit 1. Both this line and the `facebook-fetch` skill originally implied the bare form works; v0.2.0's enriched `--help` and the skill both state the URL-only reality.

### 10a. v0.2.0 — CLI self-description (planned; separate implementation session)

Motivation: the `facebook-fetch` skill currently hand-copies this CLI's flag list and output-field schema into its own docs. That mechanical layer drifts every time the CLI changes and forces a skill edit for each such change. v0.2.0 makes the CLI describe its own mechanical + CLI-intrinsic-semantic surface, so the skill can delegate that layer to the installed binary (version-matched, drift-free) and hand-maintain only what the CLI genuinely can't emit. Additive, minor bump — a new `schema` subcommand plus richer `--help` text, no breaking changes.

Be honest about the ceiling: this automates the *cheapest* layer only. Even a semantically complete `--help` cannot emit "exit 5 is an answer not an error", "metadata is on stderr", or "never loop unattended" — that stays in the skill. The win is narrow but real: the flag list and field enumeration stop drifting *for the skill's copy*, and the CLI's own `--help` becomes trustworthy for direct users too (today it prints bare flag names with no descriptions and no defaults — see the actual output, which was the empirical trigger for this section).

**Scope the drift claim honestly — the package keeps other copies of this surface.** This section reduces drift for the *skill's* copy, not the whole project's. The package already hand-maintains the same flag defaults and field enumeration in `README.md` and in `wiki/CLI-Reference.md` / `wiki/Output-Schema.md` / `wiki/Quick-Start.md` / `wiki/Python-API-Reference.md` — and `wiki/CLI-Reference.md` even claims its defaults are "read directly out of `build_parser()`… not copied from memory", which is itself a hand-copy that will drift. `schema` + enriched `--help` become a *new* authoritative source those pages do not yet consume, so after v0.2.0 a field rename still has to touch the dataclass, `to_dict()`, the descriptions dict, and each of those docs. Pick one when implementing and don't let this section read as a project-wide drift fix (it isn't one otherwise): (a) make `schema`/`--help` the single source and regenerate — or CI-diff — the wiki/README schema+flag sections against it, so there is genuinely one source; or (b) leave the wiki/README as-is and scope every "stops drifting" phrasing here to the skill's copy only. Option (a) is the real fix; (b) is the honest minimum.

**Part A — enrich `--help` (in `cli.py::build_parser`).** Give every currently-bare flag a `help=` string embedding its human default and any CLI-intrinsic semantics, so `scrape-fb fetch --help` is authoritative standalone. The load-bearing ones: `--max-scrolls` → "scroll-iteration ceiling; if the budget runs out before --limit/--since is met, the run stops with them unmet (default: 40)" (the earlier "overrides --limit/--since regardless" wording overstated it — `scroll.py` checks `--limit`/`--since` inside each iteration, so on a shallow feed they stop the run first; max-scrolls only wins when neither is reachable within the budget); `--scroll-pause` → "MIN,MAX seconds between scrolls; MIN clamped to ≥0.5s and cannot be bypassed (default: 2.0,4.0)"; `--since` → "keep posts on/after this date YYYY-MM-DD; best-effort within --max-scrolls (see exit 7)"; `--output` → "where to write (default: a timestamped file under the platform data dir, not cwd)"; `--profile-dir` → "override profile storage (default: platform data dir, or $SFB_PROFILE_DIR)"; plus plain one-liners for `--profile`/`--limit`/`--until`/`--format`/`-v` and `status --json`. Prefer explicit help strings over `argparse.ArgumentDefaultsHelpFormatter`: several defaults (`--limit` None = unbounded, `--output` None = generated path, `--profile-dir` None = platform dir) render as a misleading bare "None" under the formatter — write the human default into the string and skip the formatter. Hold one distinction firmly: `--help` carries CLI-*intrinsic* facts (the floor, the ceiling, the best-effort caveat); it must NOT carry skill-*context* advice ("always pass --output explicitly", "never --no-redact"), which is correct only in the skill's environment (the CLI's platform-dir default is deliberately right for a standalone user) and therefore stays in the skill.

**Part B — `scrape-fb schema` (new subcommand).** Prints the output object schema (a `Post`, the JSON-array element) so the skill can stop hand-copying the field list. Offline, deterministic, needs no profile/network/browser → always exit 0, fast, safe for the skill to call anytime.

Anchor the schema on the OUTPUT contract, not the dataclass — this is the trap that sank the first draft. The source of truth is `Post.to_dict()`'s emitted keys, **not** `dataclasses.fields(Post)`: they already differ on the shipped model — `dataclasses.fields(Post)` returns 20 names *including* `raw`, but `to_dict()` emits 19 and adds `raw` only when it is populated (i.e. only under `--raw`; `model.py`: `**({"raw": self.raw} if self.raw is not None else {})`). And the dataclass *types* are Python annotations, not the JSON shapes a consumer sees: `created_at`/`edited_at`/`captured_at` are `datetime | None` but serialize to ISO-8601 `string | null` via `_iso()`; `media` is `list[Media]` but serializes to an array of `{kind,url,width,height}`; `links` to an array of `{url,title,description}`; `shared_post` is `Post | None` but serializes to a nested object or `null`. So: derive the field **names** from a representative `to_dict()` output (an added/removed output key is then caught); give each a **JSON** type from an explicit Python→JSON mapping (datetime→string, `Media`/`LinkAttachment`→object, `shared_post`→nested-object-or-null), never the raw annotation; and mark `raw` as present-only-with-`--raw`. Pair the names with a descriptions dict co-located in `model.py` next to the dataclass — that dict is the one hand-maintained piece, but co-located, so it is edited in the same file as the field it describes rather than in the skill's separate repo.

Output a readable annotated listing (field · JSON type · one-line meaning) on **stdout** (matching the `status --json`-to-stdout idiom for capture-able reference data), with a `--json` flag emitting JSON Schema (draft 2020-12) — whose `properties`/`type` must be the JSON types above, not the Python annotations — for programmatic consumers; keep both thin. What `schema` still can't carry, and so stays in the skill: usage semantics — dedup on `id` never `captured_at`, `media`/`links` URLs are signed/expiring/sensitive, `shared_post` can nest deeper than one level, pinned / null-`created_at` posts bypass `--since`/`--until`. Those describe how to *use* the fields, not what they are.

**Versioning & the new drift surface it creates.** v0.2.0, additive, minor bump; CHANGELOG entry; existing CI/publish path unchanged (OIDC on GitHub Release, §15). Delegation makes the skill depend on `scrape-fb >= 0.2.0` (it calls `schema`, absent in 0.1.0) — a *new* failure mode, not a removed one: an old installed binary won't match the skill's assumptions. Mitigation, handled skill-side (FACEBOOK-FETCH-PLAN §11): its readiness path treats an unknown `schema` subcommand (argparse exit 1) or a `--version` below 0.2.0 as "upgrade: `uv tool install --upgrade scraper-for-facebook`", distinct from an exit-2 login problem; the skill states its minimum version explicitly.

**Next-session implementation order (small):** add help strings + the `schema` subcommand in `cli.py`; add the descriptions dict + a `schema` builder beside `model.py`; add a unit test asserting `schema`'s documented field set equals the keys of `Post.to_dict()` for a representative post — checked both without `--raw` (19 keys) and with `--raw` (20 keys) — **not** against `dataclasses.fields(Post)` (which includes the conditionally-emitted `raw` and would mis-document it as a standard field); this way a field the parser stops emitting, or a new `to_dict` key, fails CI until documented — turning drift into a caught error, the concrete answer to the sync worry. Also assert the descriptions dict has an entry for every `to_dict` key (a new field with no description fails CI too). The wheel-install smoke test already runs `--help`; extend it to run `schema`. Bump version + CHANGELOG; release per §15; then apply the skill-side delegation (FACEBOOK-FETCH-PLAN §11).

## 11. Retrieval semantics

- `--limit N`: stop after N top-level (non-shared, deduped) posts, newest first.
- `--since D` / `--until D`: a **date window**. Timeline scrolls newest→oldest; `--until` skips posts newer than D, `--since` stops once we've passed below D. Either may be omitted; `--limit` composes (first trigger wins).
- **Honest limit on `--since` reach (blocker fix).** Because we observe rather than replay, we cannot drive the cursor; we can only scroll and hope the browser keeps firing `ProfileCometTilesFeedPagination` pages. Facebook stalls pagination after a bounded depth (virtualization, throttling, idle). So a far-back `--since` on a prolific profile may stop at `--max-scrolls`/stall **before** reaching D. The tool must **track why it stopped** — `limit_reached | since_crossed | feed_exhausted | max_scrolls | feed_stalled` — and when it stops before crossing `since`, exit `7` with a distinct warning ("oldest retrieved is <date>; requested since=<date> not confirmed reached"). Reliable deep-history reach is **not guaranteed in v1**; the README says so.
- **Pinned posts must not break the stop condition.** A pinned post sits at the top regardless of age; a naive "oldest parsed older than `since` → stop" trips on batch 1 and aborts the run. Base scroll-termination on the newest **unseen, non-pinned** post's timestamp and on pagination end-of-feed / "no new ids for N batches" — never on `min(created_at)` of a batch that may contain a pinned outlier. Pinned posts are returned regardless of the window (documented).
- Empty result: exit `4` (with a drift hint only for a known-good profile) — distinct from `5` (profile unavailable).

## 12. Durability against Meta changes

"Observe don't replay" removes token maintenance, not response-shape drift. The recursive, anchor-based extractor (keys on stable-ish `feedback.id`, `message.text`, `actors`, `wwwURL`) degrades before it fully breaks. Mitigations: exit `4` + "possible drift" hint on a known-good profile yielding zero; a `--raw` escape hatch and `scripts/record_fixture.py` to capture a fresh body, diff, and re-anchor; fixture regression tests. We do **not** promise stability; we promise a fast re-anchor path — the realistic ceiling for solo-maintained internal-API scraping.

## 13. Testing

- **CI (public, safe): fixture-based unit tests, fed raw BYTES.** `tests/fixtures/*.ndjson` are **synthetic skeletons built PII-free by construction** — never a mutated real capture. `scripts/record_fixture.py` writes a real capture to a **gitignored `scratch/*.raw.ndjson`** locally; `scripts/build_fixture.py` extracts *only the specific values the tests assert on* and injects them into a hand-authored synthetic body (allowlist, not denylist). This is mandatory because the response shape rotates and nesting varies — a field-targeted denylist cannot know every place PII/secrets hide (nested `attached_story` actors, `@defer` chunks, avatar `scontent` URLs, `fb_dtsg` echoes). Tests assert: bytes→text decode, NDJSON/`@defer` split, top-level vs `attached_story` disambiguation (and post-count correctness against shares), **deep-merge across split `@defer` lines**, truncation-marker detection, creation_time exact-path (with a decoy-int fixture) and None handling, media + link extraction, date-window filtering, and identifier validation.
- **Mandatory PII/secret gate.** A pre-commit hook and a CI job grep committed fixtures for high-entropy strings, real `fbcdn`/`scontent` hosts, unicode names, phone/email patterns, `fb_dtsg`/token-shaped fields, and any numeric id not on a synthetic allowlist — **fail on any hit**. Every fixture diff gets human review.
- **Wheel-install smoke (CI).** A job that `pip install`s the built wheel into a clean venv (with scrapling, **without** browsers — fast, network-safe) and runs `scrape-fb --version` and `scrape-fb --help`, exercising the entry point and the full import graph (`__init__` → `session` → scrapling). This catches a wrong entry-point path, a missing runtime dep, or an import that only fails once scrapling is present — none of which the fixture tests touch.
- **Live integration (local opt-in, never public CI).** `tests/live/` runs only with `SFB_LIVE_TESTS=1` + `SFB_TEST_PROFILE_URL`, against the user's own profile; outputs gitignored. The **first two live tasks** during implementation are blocking probes: (a) **creation_time** exact path + per-node presence; (b) **truncation** across several long posts. Both drive the fixtures.

## 14. Packaging, dependencies, platform

- **`pyproject.toml`** (hatchling): `name="scraper-for-facebook"`, `version="0.1.0"`, `requires-python=">=3.11"` (chosen for alignment with the skill's stack and a smaller test matrix; documented tested range **3.11–3.13**, with patchright wheel availability as the real upper ceiling), `[project.scripts] scrape-fb = "scraper_for_facebook.cli:main"`, and **`classifiers`** including `Operating System :: MacOS` (so PyPI/`pip` warns non-macOS users *before* install) and the supported `Programming Language :: Python` versions.
- **Dependencies:** `scrapling[fetchers]>=0.4.9,<0.5` (floor: below it silently backtracks to a version lacking `DynamicSession`/`capture_xhr`; **upper bound** because `capture_xhr`/`captured_xhr` is observed-not-advertised API that a minor bump could rename/reshape), `platformdirs`. **Drop `python-dateutil`** — the only date strings are `--since`/`--until` in strict `YYYY-MM-DD`; parse with `datetime.date.fromisoformat` (errors on bad input) rather than dateutil's lenient parser (which turns `06-2026` into a silently-wrong date). Keep the surface minimal.
- **Library co-install constraint.** `scrapling[fetchers]` carries exact pins (`playwright==1.60.0`, `patchright==1.60.1`). As a *library*, those propagate into the installing environment, so `pip install scraper-for-facebook` alongside another Playwright-based tool at a different version fails to resolve. The **supported install path for all users is isolated** — `uv tool install` / `pipx` — and the README states this and warns against `pip install` into a shared env. The pins are scrapling's, not ours to loosen.
- **Browser install & isolation (`scrape-fb setup`).** Invokes scrapling's real `scrapling install` console entry via subprocess (the documented mechanism), provisioning patchright/Chromium into an **isolated browser path** (set `PLAYWRIGHT_BROWSERS_PATH` to a dir under the package's own data dir, and set it again at launch time) so the tool never shares a browser cache with the skill's fetch venv — this pre-empts the cross-tool cache clash (§16, H14). Record the resolved scrapling **and** browser-client versions; document that upgrading scrapling requires re-running `scrape-fb setup`. `setup` and `doctor` **verify by launching** and asserting a capture round-trips — a binary-presence check is not enough. *(Fallback if patchright ignores `PLAYWRIGHT_BROWSERS_PATH`: pin the exact scrapling version and have `doctor` cross-check the browser build against the skill's — but the isolated path is the primary, cleaner fix.)*
- **Supply-chain trust, stated.** README names the trust assumption (you are trusting scrapling + patchright + the Chromium download host, which run a browser against your authenticated session and can read the on-disk profile). Ship `requirements-dev.lock` for reproducible CI. (Browser-download integrity is playwright/patchright's own mechanism, not something this package can add.)
- **Platform.** macOS is the first-class, tested target. Linux likely works (fixture/CLI layer is browser-agnostic) but is untested in v1; a Linux CI leg for fixture tests + wheel-install + `--version` is a cheap way to make the parse/CLI layer's cross-platform claim real. Windows unsupported in v1. State this honestly (README top + classifiers), not implied parity. Note the macOS **keychain** prompt a persistent Chromium profile can trigger (G-keychain).
- **Versioning:** SemVer from `0.1.0`; `CHANGELOG.md`.

## 15. Publishing (Trusted Publishing, no token)

- **Revoke the exposed token FIRST — gated and verified.** The PyPI token pasted into chat earlier must be treated as a live, likely **account-scoped** credential (the project didn't exist yet, so it couldn't have been project-scoped — it can publish/yank *any* project the account owns). Before any publishing work: (1) revoke it in PyPI now; (2) **prove it is dead** (an authenticated `whoami`/upload attempt returns 401); (3) record its scope and, if account-wide, note other projects were exposed while it was live; (4) treat the chat transcript as containing plaintext (inert once the token is dead). Only then proceed.
- **First release uses a PENDING publisher (blocker fix).** For a brand-new name there is no project settings page yet. Before the first tag, create a **pending publisher** at PyPI → Account Settings → Publishing: project name `scraper-for-facebook`, owner `tjdwls101010`, repo **`Scraper-for-Facebook`** (this package's own repo — the skill's `Skills-for-Fetch` repo is unrelated to publishing), workflow filename **`publish.yml`**, and the environment name if that workflow uses one. It converts to a normal publisher on first successful upload. (Do not follow "register on the project settings page" — that page doesn't exist pre-publish.)
- **`publish.yml` must actually authenticate.** It triggers on a version tag (`v*`) in this package's own repo and builds from the repo root. The publish job sets `permissions: id-token: write` (plus `contents: read`) — without it, `gh-action-pypi-publish` cannot mint the OIDC token. Choose **either no environment on both sides, or the same environment name** on both the workflow and the pending-publisher registration (a mismatch → PyPI rejects the token). **Pin `pypa/gh-action-pypi-publish` to a full commit SHA** (a floating tag is a hijack vector for a package that runs against users' authenticated sessions), and scope the trusted publisher to that named environment. (Skip required-reviewer gates — impractical for a solo maintainer.)

## 16. Integration with the `web-fetch` skill (PLAN.md edits are APPLIED, not deferred)

The skill no longer embeds FB parsing. `engine/fbparse.py` is removed from PLAN.md; the generic `--capture-xhr` mechanism (for Reddit-class Tier-1 and arbitrary SPAs) **stays** in the skill — only the Facebook-specific parse/orchestration moves to `scrape-fb`. Concretely (these edits are made to PLAN.md in the same change as this plan):
- **Setup:** the skill installs the tool globally — `uv tool install scraper-for-facebook`, then `scrape-fb setup` (isolated browser path). Health check **executes `scrape-fb doctor`** (which launches the browser and round-trips a capture), *not* `scrape-fb --version` (which only proves the entry point imports, attesting nothing about the browser — the G-yt "execute the real capability" rule applied one layer deeper).
- **Cross-tool cache:** because `scrape-fb setup` uses an isolated `PLAYWRIGHT_BROWSERS_PATH`, the skill's fetch venv and the tool never fight over one browser build; `wf doctor` additionally reports the tool's health via `scrape-fb doctor`.
- **Login:** SKILL.md instructs the user to run `scrape-fb login` once (headed) for Meta targets; the skill runs headless.
- **Tier-2 fetch:** for Facebook, the skill shells out to `scrape-fb fetch <url> --profile <name> --limit N [--since ...] --format json --output <gitignored path>`, reads the JSON, renders a markdown preview, saves the full result under `./.tmp/web-fetch/` (gitignored). The skill passes an explicit gitignored `--output`; standalone `scrape-fb` defaults `--output` to a non-repo platformdirs path (never cwd/stdout) so a direct `scrape-fb fetch` in a repo can't drop third-party PII into a tracked location.
- **Threads:** PLAN.md currently frames Threads as skill-owned "mechanism-ready" reusing the in-skill capture core. Since that core moves to `scrape-fb`, PLAN.md must record that **Threads is deferred to the package's roadmap** (§19) — the skill will carry no Meta capture code of its own; adding Threads means a `scrape-fb` release, not skill work.
- **Gotchas:** move the FB-capture gotchas into this package's domain (capture pattern, bytes body, NDJSON/@defer, doc_id rotation, media expiry, headed-login persistence, camoufox incompatibility, checkpoint, ban) and **carry over PLAN.md's redaction rule** ("never print captured bodies/tokens to context; redact before saving") as §21 here. Leave skill-side notes (login prerequisite, gitignored outputs, ban warning) in PLAN.md. Fix PLAN.md's `response.captured_xhr` → **`page.captured_xhr`**.

## 17. Gotchas (package-side)

- **G-capture-pattern:** `capture_xhr=r"graphql"` (proven). `r"/api/graphql/"` under-captured.
- **G-body-bytes:** `Response.body` is **bytes** — `decode("utf-8","replace")` before splitting; feed fixtures as bytes.
- **G-captured-attr:** read `page.captured_xhr` (off `fetch()`'s return), not `response.captured_xhr`.
- **G-ndjson-defer:** newline-delimited; content across `@defer` chunks; parse all lines, then **deep-merge** by `feedback.id`.
- **G-attached-story:** anchor top-level on `data.node`; consume `attached_story` as `shared_post`, never emit it top-level.
- **G-creation-time:** unix **int**, exact key relative to the story root (not any int, not a nested node's); define None handling to avoid `TypeError` in sort/filter.
- **G-recursive-paths:** `doc_id`s/paths rotate; anchor on stable-ish keys, don't hardcode.
- **G-media-expiry:** `scontent`/`fbcdn` URLs are signed, **expire**, and are viewer-scoped credentials — v1 stores URLs only and treats them as sensitive in diagnostics (§21).
- **G-headed-login-persist:** login uses the same persistent `user_data_dir`; CDP-attach does not inherit login.
- **G-camoufox-incompat:** don't switch to camoufox/Firefox — the profile is Chromium.
- **G-checkpoint:** on checkpoint/login-wall markers, abort + prompt re-login; never hammer.
- **G-fbheadless:** headless durability is unproven beyond a run; deeper scrolling raises checkpoint risk — consider headed for the account you value.
- **G-keychain (macOS):** a persistent Chromium profile may prompt keychain on first launch; and the raw `user_data_dir` is *less* protected than keychain-encrypted cookies — treat as a credential (0700, no sync/backup).
- **G-ban:** automating a Meta account violates ToS; dedicated/throwaway account, low volume, no loops.
- **G-cache-isolation:** provision the browser into an isolated `PLAYWRIGHT_BROWSERS_PATH` so the tool and the skill's venv never share/clobber a build.

## 18. Build order (implementation session)

1. Scaffold the package files. The repo is **already initialized** (done in planning): `package/scraper-for-facebook/` is its own git repo on branch `main`, remote `github.com/tjdwls101010/Scraper-for-Facebook`, initial commit pushed to `origin/main`; the Skills-for-Fetch root `.gitignore` already excludes `package/scraper-for-facebook/`. Add: `pyproject.toml` (classifiers, `scrape-fb` entry point), `LICENSE` (MIT), `DISCLAIMER.md`, README skeleton (isolated-install + disclaimer), scoped `.gitignore` (with `!tests/fixtures/`), ruff + pre-commit (incl. PII/secret scan), and this repo's own `.github/workflows/ci.yml` + `publish.yml`. **Verify the `scrape-fb` console-script name collides with no installed PyPI/Homebrew command** before committing.
2. `profiles.py` (platformdirs, 0700, identifier validation) + `session.py` + `redact.py` + `scrape-fb login`/`status`(enum)/`setup`(isolated browser)/`doctor`.
3. **Blocking live probes:** creation_time exact path/presence, then truncation across several long posts. Capture → `build_fixture.py` → synthetic fixtures.
4. `parse.py` + `model.py`: bytes-decode, NDJSON/@defer split, `data.node` top-level detection, `attached_story` consumption, **deep-merge** by feedback id, field/media/link extraction, `Post`/`Media`/`LinkAttachment`. Fixture-driven.
5. `truncation.py`: marker detection + permalink-refetch fallback; set `text_truncated`/`text_resolved`.
6. `scroll.py` + `retrieve.py`: human-like scroll with the ≥0.5s floor, `--limit`/`--since`/`--until`, **stop-reason tracking**, pinned/reel/unavailable handling, exit-code mapping (incl. `5`, `7`).
7. `cli.py`: `fetch`, output formats, non-repo default `--output`, `--version`, redaction-scrubbed diagnostics, `--scroll-pause` float-pair parser.
8. Tests: synthetic fixtures + PII/secret gate + unit suite + wheel-install smoke; wire local live-integration (opt-in).
9. Packaging polish; finish `publish.yml` (tag `v*`, build from repo root, `id-token: write`, SHA-pinned action, environment); **revoke+verify the exposed token**; create the **pending publisher** (repo `Scraper-for-Facebook`, workflow `publish.yml`); tag `v0.1.0`.
10. Skill integration (separate repos): apply the §16 edits to PLAN.md (in the Skills-for-Fetch repo); the skill installs the *published* `scrape-fb` via `uv tool` (from PyPI, not a local path — so it exercises the real distribution) and calls `scrape-fb fetch`/`doctor`.

## 19. Roadmap (post-v1)

Threads then Instagram (separate parsers, **same capture core owned by this package** — this is the skill's Threads path too, §16); comment threads and reactor lists; optional media download; incremental `--since-last` with per-profile state; a documented deep-history strategy if one proves reliable; Linux/Windows first-class + CI matrix. Each added surface widens the break-and-maintain area — add deliberately.

## 20. Open questions (resolved ones removed)

- Exact truncation marker field name (resolved by the §13 probe).
- Exact `creation_time` key path + per-node presence (resolved by the §13 probe; None-handling already specified).
- Real-world scroll/stop tuning: how deep before Facebook stalls pagination, and the honest ceiling for `--since` reach (§11 already ships the partial-signal regardless).
- Whether patchright honors `PLAYWRIGHT_BROWSERS_PATH` for the isolated-cache fix (§14 fallback specified if not).

## 21. Redaction / diagnostics (single scrub path)

Captured bodies and story nodes contain third-party PII and viewer-scoped signed `scontent` URLs (bearer-like). One `redact.py` scrub helper routes **all** body-touching output — `-v/--verbose`, error/drift dumps, `--raw`, and anything printed to stdout/logs/context: strip `scontent`/`fbcdn` query strings, drop token-shaped/`fb_dtsg`/cookie fields, truncate/redact names and message text in diagnostics. Raw, unredacted output requires an explicit `--no-redact` plus an on-screen PII warning; `-v` must never dump raw captured bodies; the docs never encourage pasting raw bodies into the issue tracker. This carries over PLAN.md's "never print tokens to context; redact before saving" rule, which regressed when the capture engine moved into this package.

## 22. Security & threat model (summary)

- **On-disk session credential:** the profile dir is a live 2FA-satisfied session → `0700`, no sync/backup, revoke-by-logout guidance (§7).
- **Input as a navigation primitive:** validate/normalize the profile identifier before it reaches the authenticated browser (§10).
- **Diagnostics leak:** single redaction path; signed URLs treated as sensitive (§21).
- **Supply chain:** isolated install, pinned scrapling range, reproducible-CI lockfile, stated trust assumption (§14).
- **Publish integrity:** verified token revocation, pending-publisher OIDC with `id-token: write`, SHA-pinned action, scoped environment (§15).
- **Fixtures:** synthetic-by-construction + mandatory PII/secret CI gate (§13).

## 23. Review provenance

This plan was hardened against a 6-lens adversarial review (legal/PII, parsing durability, packaging/publish, API/CLI, skill integration, security/supply-chain): 48 findings raised, 4 rejected, 44 incorporated — 5 blockers (bytes body, `--since` reach honesty, `scrape-fb` command-name collision, applying the PLAN.md edits, gated token revocation) and the high/medium/low items folded into §§5–22. Decisions the user made on review: keep dist name `scraper-for-facebook` + real identity (with the maintainer-exposure note in the DISCLAIMER), command `scrape-fb`, and guardrail posture "soft defaults + one non-bypassable delay floor."
