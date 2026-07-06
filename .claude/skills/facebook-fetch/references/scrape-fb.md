# scrape-fb reference

Trigger: a real fetch is about to run, something failed, or this is the first time in a project/profile. Deciding *whether* to reach for this skill never needs this file — SKILL.md's body already carries the trigger, the command, and the traps that bite on every call.

## One-time setup and login, in order

```bash
uv tool install scraper-for-facebook   # isolated install — never a shared `pip install`
scrape-fb setup                          # provisions an isolated Chromium (~hundreds of MB, one time)
scrape-fb login                          # opens a REAL headed browser; log in by hand, then press Enter in the terminal
scrape-fb doctor                         # confirms the browser + GraphQL-capture pipeline actually works
```

`uv tool install` (not a bare `pip install`) matters because `scrape-fb` pins an exact Playwright/patchright build — installing it into a shared environment can silently break some other tool's own Playwright install, and there'd be no obvious error pointing back at this package as the cause. `scrape-fb setup` is a separate step from installing the wheel; the wheel alone does not pull down the browser binary, and `--force` re-provisions if a prior install was interrupted or corrupted. `scrape-fb login` blocks on a real terminal prompt ("...press Enter here to continue...") while a visible browser window is open — log in by hand, including any 2FA, then return to the terminal; the session (cookies + local storage) is then persisted to a profile directory named `default` unless `--profile <name>` picks a different one, which is how a second identity (e.g. a dedicated/throwaway account, `--profile burner`) stays separate from the first. Every one of `login`/`status`/`doctor`/`fetch` accepts `--profile NAME` (default `default`) and `--profile-dir PATH` (overrides where that profile lives on disk); if neither `--profile-dir` nor the `SFB_PROFILE_DIR` environment variable is set, profiles live under this package's own platform data directory, never inside a project.

If `scrape-fb` itself can't be found at all — a shell "command not found," or a Python import error if it's invoked some other way — that's the signal to run `uv tool install scraper-for-facebook` and `scrape-fb setup`, not a login problem; a fetch's exit 2 (below) is the login-specific signal, and the two shouldn't be conflated.

`scrape-fb doctor` launches the browser against a profile and confirms a GraphQL response can be observed and parsed — it is a **pipeline** check, and it passes even against Facebook's logged-out page, so a green `doctor` proves the mechanism works, not that anyone is logged in. Session health is a separate, narrower question: `scrape-fb status` checks it without opening a visible browser at all. `status --json` is the one place in this CLI that writes to **stdout** instead of stderr (`{"status": "logged_in", "session_age_seconds": 3421.0}`) — every other command's metadata and every human-readable form of `status` itself goes to stderr, so don't assume stdout is empty when scripting against this one. `session_age_seconds` can come back `null` (JSON) or `unknown` (human-readable) when the tool can't determine session age — treat that as "don't know," never as "just logged in."

## Full `fetch` flag reference

| Flag | Default | Notes |
|---|---|---|
| `<identifier>` (positional) | — | vanity name, numeric id, or a full URL on `facebook.com`/`www.facebook.com`/`m.facebook.com` — a `profile.php?id=<digits>` path only works inside that full URL, never as a bare string on its own; anything else is rejected before the browser opens |
| `--profile NAME` | `default` | which saved login session to use |
| `--profile-dir PATH` | none | overrides where that profile lives; falls back to `SFB_PROFILE_DIR`, then the platform data dir |
| `--limit N` | unbounded | stop after this many posts |
| `--since YYYY-MM-DD` | none | keep posts on/after this date — best-effort, see the exit-7 note below |
| `--until YYYY-MM-DD` | none | keep posts on/before this date |
| `--format json\|ndjson` | `json` | a single pretty-printed array vs one object per line |
| `--output PATH` | a generated path in the tool's own data dir | always pass this explicitly — see SKILL.md |
| `--scroll-pause MIN,MAX` | `2.0,4.0` | seconds between scrolls; floor is 0.5s and cannot be bypassed (see Troubleshooting) |
| `--max-scrolls N` | `40` | hard scroll-iteration ceiling, overrides `--limit`/`--since` regardless |
| `--headed` | off | show the browser window (debugging only) |
| `--raw` | off | attach the raw captured GraphQL story node per post, redacted by default |
| `--no-redact` | off | only matters with `--raw` — disables that redaction; essentially never use this |
| `-v`, `--verbose` | off | print the full (still redaction-scrubbed) error text instead of just the exception type name |

## Exit codes, every subcommand

**`login`** — `0` confirmed login; `2` still sees a login wall after Enter was pressed (try again); `1` any other failure (browser crash, permissions, etc).

**`status`** — `0` `logged_in`; `2` `expired`; `3` `checkpoint`; `1` the check itself failed unexpectedly, which is a different thing from `expired` (something broke before a status could even be determined).

**`setup`** — `0` provisioned; `1` provisioning failed (network, disk space, unsupported platform).

**`doctor`** — `0` the full round trip succeeded; `1` anything in it failed (browser wouldn't launch/navigate, or navigated but never observed a matching GraphQL response).

**`fetch`** — `0` success (limit satisfied, since/until window covered, or feed genuinely exhausted); `1` invalid identifier / bad or missing flags / any other unexpected error; `2` login required or session expired; `3` account checkpoint mid-run, never auto-retried; `4` zero posts retrieved; `5` profile unavailable (memorialized, blocked, restricted, or nonexistent); `7` `--since` was requested but the run stopped for a reason that says nothing about whether it was reached. There is no exit 6.

**Argparse usage errors exit 1, not argparse's own default of 2.** `cli.py` deliberately overrides `ArgumentParser.error()` for exactly this reason: exit 2 already means "login required or session expired" in this CLI's contract, so a typo'd flag has to land somewhere else, or a caller branching on "exit 2 → run login" could be fooled by an unrelated usage mistake into thinking a live session had expired. `scrape-fb` with no subcommand, an unknown flag, or a missing required identifier — all exit 1, same as "unexpected error," on purpose.

**Reading exit 4 correctly.** The stderr line accompanying a zero-post result distinguishes two different situations: if the internal stop reason is `unknown_error`, scrolling was interrupted before a single post was captured — rerun with `-v` for the underlying cause. Otherwise, the message points at a possible Facebook response-shape change and the package's issue tracker — which in practice usually means the target wasn't actually a personal profile timeline (a Page/Group/album slipped through identifier validation because it superficially matched the accepted URL shapes). Check the identifier's actual target before assuming the parser broke.

**Reading exit 7 correctly.** `--limit` is checked before `--since` on every scroll batch, so `--limit N --since <date>` together will almost always stop on the limit — reported as ordinary success (exit 0) — with `--since` never independently verified; that is expected, not a bug, because the CLI treats "got exactly N posts, as asked" as a complete success regardless. Exit 7 is reserved for the narrower case where `--since` was requested *alone* (or the limit wasn't hit first) and the run stopped on `max_scrolls` (ran out of scroll budget) or `feed_stalled` (Facebook stopped returning new posts) — both genuinely inconclusive about whether the requested date was reached. The stderr summary always includes the real post count and observed date range either way, so a partial run is never silently indistinguishable from a complete one.

## Output schema

`--format json` writes a single pretty-printed JSON array of post objects; `--format ndjson` writes one object per line. No wrapper object, no `posts` key, no run metadata in the file itself — that's all on stderr (SKILL.md's top trap).

A post object: `id` (stable dedup key — dedupe on this, never on `captured_at`), `url` (permalink, may be null), `type` (`status`/`photo`/`video`/`shared`/`link`/`reel`/`life_event`/`unknown`), `is_pinned` (pinned posts bypass `--since`/`--until` and always appear first), `author_name`/`author_url`/`author_id`, `created_at` (ISO-8601 UTC with a `Z` suffix, may be null if unlocatable), `edited_at` (same shape, null if never edited), `text` (full body, empty string if none), `text_truncated` (the payload carried a truncation marker, regardless of whether it was resolved) and `text_resolved` (a follow-up fetch recovered the full text), `media` (a list of `{kind, url, width, height}` — `url` is a signed, expiring, viewer-scoped `fbcdn`/`scontent` link; treat it as sensitive, never print it), `links` (a list of `{url, title, description}` for shared external links), `reaction_count`/`comment_count`/`share_count` (any may be null), `shared_post` (a nested post built from an attached/shared story — can itself have a non-null `shared_post` on a share-of-a-share, so don't assume the nesting stops at one level), `captured_at` (when this tool captured the response — changes every run, never a dedup key), and `raw` (present only when `--raw` was passed, redacted by default unless `--no-redact` was also passed).

## Legal exposure beyond Meta's ToS

Posts captured this way belong to other people — authors, commenters, anyone tagged or mentioned — and collecting/storing identifiable personal data about other people can make the person running this tool a data controller under GDPR, CCPA, or similar law in some jurisdictions, with real obligations around lawful basis, honoring deletion/access requests, and limiting retention; "I did this for personal use" isn't automatically a lawful basis on its own. The practical takeaway is the same one already in play for PII generally: keep what's captured to what's actually needed, and delete it once it's served its purpose. See the `scraper-for-facebook` package's own `DISCLAIMER.md` (https://github.com/tjdwls101010/Scraper-for-Facebook/blob/main/DISCLAIMER.md) for the fuller discussion, including a note on maintainer identifiability that doesn't change how this skill should be used but is worth knowing about.

## Troubleshooting

**Checkpoint (exit 3).** Meta has flagged the session mid-run with a security checkpoint. Never retry automatically — that's the single behavior most likely to turn a checkpoint into an outright ban. The only fix is a real, headed `scrape-fb login` to resolve the challenge by hand.

**Session expiry (exit 2, after a profile that used to work).** A previously-good session can simply expire — this looks identical to never having logged in at all from the CLI's point of view, and the fix is the same either way: `scrape-fb login --profile <name>`.

**`SingletonLock` / `ProcessSingleton` error on `status`, `doctor`, or `fetch`.** Chromium refuses to open a second instance against the same profile directory at once. This almost always means a `scrape-fb login` for that same `--profile`/`--profile-dir` is still sitting open somewhere — most often stuck at the "press Enter here to continue" prompt in a terminal nobody returned to. Find and finish (or kill) that process before retrying; this is not a sign the profile itself is corrupted.

**The scroll-pause floor is silent unless you look at stderr.** `--scroll-pause` below 0.5 seconds on either end is raised automatically, with a one-line stderr note (`scrape-fb: --scroll-pause 0,0 raised to 0.5,0.5 (minimum is 0.5s)`) — the run still proceeds normally at the floor instead of erroring, so don't mistake the note for a failure, and don't read its absence as "the pause I asked for was honored" without checking whether it was already above the floor.

**A checkpoint or expiry can also masquerade as thin/zero content rather than the expected exit code**, if something upstream changed — when in doubt after any surprising `fetch` result, run `scrape-fb status` directly rather than inferring session health from `fetch`'s exit code alone.
