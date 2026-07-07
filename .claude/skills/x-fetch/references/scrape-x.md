# scrape-x reference

Trigger: a real fetch is about to run, something failed, or this is the first time in a project/profile. Deciding *whether* to reach for this skill, and *which* subcommand, never needs this file — SKILL.md's body already carries the trigger, the routing rule, the command, and the traps that bite on every call.

## One-time setup and login — two paths

`scrape-x` gets its session one of two ways. Both end in the same on-disk credential (a live, password-less X session) that the read path replays over plain HTTP. Lead with the browser path; offer cookie-import when the user doesn't want a browser download.

### Path A — browser login (the default)

```bash
uv tool install "scraper-for-x[browser]"   # the [browser] extra pulls the stealth-login browser stack (isolated venv)
scrape-x setup                              # provisions that browser into an isolated cache (one time, ~hundreds of MB)
scrape-x login                              # opens a REAL headed Chromium at x.com; log in by hand, then press Enter in the terminal
scrape-x doctor                             # health check: one authenticated GraphQL round-trip over plain HTTP
```

`uv tool install` (not a bare `pip install`) matters because the `[browser]` extra pins an exact Playwright/patchright build that a shared environment install could silently break. `scrape-x setup` is a separate step from installing the wheel — the wheel alone doesn't pull the browser binary; `--force` re-provisions a corrupted or interrupted install. `scrape-x login` blocks on a real terminal prompt while a visible browser window is open — log in by hand, including any 2FA, then return to the terminal and press Enter; the session is harvested (cookies + the current query-ids) and persisted to a profile named `default` unless `--profile <name>` picks another.

### Path B — cookie-import (lighter; base install, no browser)

```bash
uv tool install scraper-for-x              # BASE install — HTTP client only, no browser download
scrape-x login --cookies <export-file>     # import a Netscape / JSON / cURL cookie export from a browser you're logged into
scrape-x doctor
```

Cookie-import needs the user to export their X cookies (the export **must** include `auth_token` and `ct0`) from a browser where they're already logged in — a cookie-export extension or a devtools copy. It's lighter and faster (base install, no browser download), but the export step is fiddlier for a non-expert. Two things to tell the user: the **source export file still holds a live session after import — delete it**; and cookie-parse errors deliberately scrub the sensitive token values (`auth_token`/`ct0`/bearer/`csrf`) from anything echoed, so the live credential never leaks into a message — but the error may still quote the offending (redacted) line or segment for context, not merely its position.

### Readiness facts (both paths)

- **`scrape-x doctor` requires a session to already exist and makes a real authenticated read** — exit 0 means the session is genuinely live. It does **not** launch a browser and does **not** re-verify `setup`'s provisioning. This is the opposite of `scrape-fb doctor` (a browser-pipeline check that passes even logged-out) — don't carry the FB mental model over.
- **`scrape-x status`** classifies without a browser: `logged_in` / `expired` / `rate_limited` at exit `0` / `2` / `3`. `status --json` writes `{"status": "..."}` to **stdout** (every other command's metadata goes to stderr) — don't assume stdout is empty when scripting against this one.
- **`--profile` defaults to `default`** on every command that takes it; a single-account user omits it and only needs it to keep more than one X login separate (`--profile burner`). `--profile-dir PATH` (or `$SFX_PROFILE_DIR`) overrides where the credential lives on disk; unset, it lives under the package's own platform data dir, never inside a project.
- **`--version` attests nothing operational** — it proves the entry point imports, not that anyone is logged in. Use `doctor` for real health.
- **A base install can't do browser login or `setup`** — those need the `[browser]` extra and fail with an import-shaped error otherwise. If you're guiding Path A, install the `[browser]` extra; if the user only has the base install and wants a browser login, that's the fix.
- **If `scrape-x` isn't found at all** — a shell "command not found" — that's the signal to `uv tool install` it, not a login problem; a fetch's exit 2 is the login-specific signal, and the two shouldn't be conflated.

The skill's readiness logic is try-first, not check-first (cheaper): run the read; on exit 2 → guide the chosen login path and retry; on exit 4 → `scrape-x doctor --refresh` then retry.

## Version check (`scrape-x` ≥ 0.2.0 required)

This skill points at `scrape-x fetch --help` / `scrape-x tweet --help` and `scrape-x schema` below instead of hand-copying the flag/field surface — all of those need 0.2.0 or later. Confirm with `scrape-x schema`: exit 0 means the install is new enough; a pre-0.2.0 install doesn't recognize the `schema` subcommand at all and exits 1 with an argparse "invalid choice" error — that is an **upgrade signal, not a login problem, and not exit 2**. The fix is to upgrade, and the exact command matters: use **`uv tool upgrade scraper-for-x`**, never `uv tool install --upgrade scraper-for-x[...]`. The reason is X-specific: `scrape-x` has a `[browser]` extra (Path A installs `scraper-for-x[browser]`; Path B installs the base). `uv tool upgrade scraper-for-x` upgrades in place from the recorded requirement, **preserving whichever extra was installed** — the single command correct for both paths. `uv tool install --upgrade <spec>` instead re-resolves from the spec as written, so `--upgrade scraper-for-x` would strip the `[browser]` stack from a Path A user (breaking `login`/`setup`), while `--upgrade "scraper-for-x[browser]"` would force the browser stack onto a Path B user who deliberately stayed base-only. (This differs from `scrape-fb`, which has no extras and can safely use the `install --upgrade` form — don't carry that habit to X.)

## The flags, and the output schema

Run `scrape-x fetch --help` and `scrape-x tweet --help` for the authoritative, version-matched flag lists — every flag's help text now states its own default and any built-in caveat directly (the `--since` best-effort → exit-7 note, the `--wait-on-limit`/`--max-wait` semantics, the `--by` all-digit-identifier rule), so none of it needs hand-copying here. Run `scrape-x schema` for the authoritative output field list — it describes all three object types (`Tweet`, its nested `User` under `author`, and `Media`) with each field's JSON type and one-line meaning, including the field-usage gotchas (dedup on `id`; a retweet's `text` is the retweeted *original*'s text, not an "RT @…" stub; `media` URLs are sensitive; `retweeted_tweet`/`quoted_tweet` can nest deeper than one level; what `is_restricted` means; and that pinned or null-`created_at` tweets fall outside a `--since`/`--until` window — pinned tweets bypass it and are always returned, a null-dated tweet is never compared, so a tweet appearing outside the requested window there is expected, not a filter bug). `scrape-x schema --json` emits the same as JSON Schema (draft 2020-12). Both are offline and exit 0 on a new-enough install — safe to run anytime, no profile or network needed.

What neither `--help` nor `schema` can tell you, because it's skill-context usage rather than a flag or a field:

- **`fetch` takes a profile; `tweet` takes a tweet** (SKILL.md's routing rule) — the single most important thing, and the CLI can't infer intent from a bare identifier. A `.../status/<id>` URL to `fetch` dies at exit 5.
- **An all-digit identifier is a user id by default** — `@12345` or `--by screen_name` forces the handle reading.
- **Always pass `--output` explicitly to `./.tmp/x-fetch/…`** — the CLI's own default (a path in its platform data dir) is deliberately right for a standalone user, wrong for this skill's read-then-gitignore flow.
- **Never pass `--no-redact`** — see SKILL.md's Critical traps.

## Exit codes, every subcommand

**`login`** — `0` session saved (browser harvested, or cookies imported); `2` a browser login finished but no `auth_token`/`ct0` was found (try again); `1` any other failure (a cookie export that fails validation, an unreadable file, a browser crash).

**`status`** — `0` `logged_in`; `2` `expired` (or no session at all); `3` `rate_limited`; `1` the check itself failed unexpectedly.

**`setup`** — `0` provisioned; `1` provisioning failed (network, disk, or the `[browser]` extra isn't installed).

**`doctor`** — `0` the authenticated round-trip succeeded (session is live); `1` it failed (expired/soft-locked session, or, with `--refresh`, a re-anchor that couldn't complete).

**`fetch` / `tweet`** — `0` success (limit satisfied, since/until window covered, feed exhausted, or a thread with no replies); `1` invalid identifier / not-implemented (`search`, `fetch --replies`) / bad flags / unexpected error; `2` login required, expired, or soft-locked; `3` rate-limited before completion (partial result written); `4` response envelope unparseable (likely query-id drift → `doctor --refresh`); `5` target unavailable (profile suspended/protected/nonexistent, or tweet deleted/thread gone — also the symptom of feeding a tweet URL to `fetch`); `7` `--since` requested but the run stopped on `--limit` or the request budget before confirming it was reached. There is no exit 6.

**Argparse usage errors exit 1, not argparse's own default of 2.** The CLI overrides `ArgumentParser.error()` on purpose: exit 2 already means "login required / expired / soft-locked", so a typo'd flag or a missing identifier has to land elsewhere — a caller branching on "exit 2 → run login" would otherwise be fooled by an unrelated usage mistake into thinking a live session had expired. So `scrape-x` with no subcommand, an unknown flag, or a missing identifier all exit 1, the same code as "unexpected error", by design.

## Legal exposure beyond X's ToS

Tweets captured this way belong to other people — authors, repliers, anyone quoted or mentioned — and collecting/storing identifiable personal data about others can make the person running the tool a **data controller** under GDPR, or attract other jurisdictions' privacy-tort and publication rules, with real obligations around lawful basis, honoring deletion/access requests, and limiting retention; "I did this for personal use" isn't automatically a lawful basis. X Corp is also notably litigious about scraping. The practical takeaway is the same one already in play for PII generally: keep what's captured to what's actually needed, and delete it once it's served its purpose. See the `scraper-for-x` package's own `DISCLAIMER.md` (https://github.com/tjdwls101010/Scraper-for-X/blob/main/DISCLAIMER.md) for the fuller discussion, including a note on maintainer identifiability that doesn't change how this skill should be used but is worth knowing about.

## Troubleshooting

**Exit 4 (envelope unparseable).** X rotates its GraphQL query-ids every few weeks; a stale id yields an envelope the parser can't walk — this is drift, not a login problem. `scrape-x doctor --refresh` re-anchors the ids from x.com's `main.js` over plain HTTP (no browser, works on a base/cookie-import install too), then the retry succeeds. If it persists after a refresh, it's genuine response-shape drift → a package issue, not something the skill can fix.

**Exit 2 right after a successful login.** Suspect a network/IP change. A session harvested on one network and replayed from another — especially a cookie-imported session hit from a datacenter/VPN IP — is exactly the abrupt client change X's abuse systems weight against an existing session, and it can soft-lock it. Re-run from the same egress IP where the session was established; if it's genuinely expired, `scrape-x login` again.

**Exit 3 (rate-limited).** X's read limits are modest (about 50 requests per 15-minute window for profile timelines and search-class ops). The partial result is already written. Offer `--wait-on-limit` (sleeps until the window resets) optionally bounded by `--max-wait <seconds>`, or just retry after the reset. Never build a retry loop that hammers a 429.

**A base-install user hitting a browser command.** `scrape-x login` (browser) and `scrape-x setup` need the `[browser]` extra; on a base install they fail with an import-shaped error. The fix is either `uv tool install "scraper-for-x[browser]"` for the browser path, or switch to cookie-import (`scrape-x login --cookies <file>`), which the base install fully supports.

**A soft-lock or expiry can masquerade as a thin/empty result rather than a clean exit 2.** X sometimes degrades a stale session by returning an empty timeline with HTTP 200 instead of a clean 401. `scrape-x` already probes for this before calling a zero-result "drift", but when in doubt after any surprising result, run `scrape-x status` directly rather than inferring session health from a fetch's exit code alone.
