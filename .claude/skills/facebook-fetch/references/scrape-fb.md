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

**Version check (`scrape-fb` ≥ 0.2.0 required).** This skill points to `scrape-fb fetch --help` and `scrape-fb schema` below instead of hand-copying the flag/field surface — both need 0.2.0 or later. Confirm with `scrape-fb schema`: exit 0 means the install is new enough; an older install doesn't recognize `schema` at all and exits 1 (an argparse usage error, the same code a typo'd flag gets — not a login problem, and not exit 2). If that happens, the fix is `uv tool install --upgrade scraper-for-facebook`, not `scrape-fb login`.

## The `fetch` flags, and the output schema

Run `scrape-fb fetch --help` for the authoritative, version-matched flag list — every flag's help text now states its own default and any built-in caveat directly (the 0.5s `--scroll-pause` floor, the `--max-scrolls` ceiling behavior, the `--since` best-effort note), so it doesn't need hand-copying here. Run `scrape-fb schema` for the authoritative output field list (name, JSON type, one-line meaning); `scrape-fb schema --json` emits the same as JSON Schema (draft 2020-12) for programmatic use. Both are offline and exit 0 on a new-enough install — safe to run anytime, no profile or network needed.

What neither command can tell you, because it's usage semantics rather than a flag or a field name:
- The `<identifier>` positional accepts a vanity name, a numeric id, or a full URL on `facebook.com`/`www.facebook.com`/`m.facebook.com` — a `profile.php?id=<digits>` path only works *inside* that full URL, never as a bare string on its own (see SKILL.md's "The command" section); anything else is rejected before the browser opens.
- Always pass `--output` explicitly to `./.tmp/facebook-fetch/…` — the CLI's own default (a path in its platform data dir) is correct for a standalone user, wrong for this skill's read-then-gitignore flow (SKILL.md).
- Dedupe posts on `id`, never on `captured_at` (`captured_at` changes every run).
- `media`/`links` URLs (`fbcdn`/`scontent`) are signed, expiring, and viewer-scoped — treat as sensitive, never print unredacted.
- `shared_post` can nest deeper than one level (a share of a share) — don't assume the nesting stops after the first one.
- Pinned posts, and posts with a null `created_at`, bypass `--since`/`--until` filtering entirely and can appear outside the requested window.
- Never pass `--no-redact` — see SKILL.md's Critical traps.

## Exit codes, every subcommand

**`login`** — `0` confirmed login; `2` still sees a login wall after Enter was pressed (try again); `1` any other failure (browser crash, permissions, etc).

**`status`** — `0` `logged_in`; `2` `expired`; `3` `checkpoint`; `1` the check itself failed unexpectedly, which is a different thing from `expired` (something broke before a status could even be determined).

**`setup`** — `0` provisioned; `1` provisioning failed (network, disk space, unsupported platform).

**`doctor`** — `0` the full round trip succeeded; `1` anything in it failed (browser wouldn't launch/navigate, or navigated but never observed a matching GraphQL response).

**`fetch`** — `0` success (limit satisfied, since/until window covered, or feed genuinely exhausted); `1` invalid identifier / bad or missing flags / any other unexpected error; `2` login required or session expired; `3` account checkpoint mid-run, never auto-retried; `4` zero posts retrieved; `5` profile unavailable (memorialized, blocked, restricted, or nonexistent); `7` `--since` was requested but the run stopped for a reason that says nothing about whether it was reached. There is no exit 6.

**Argparse usage errors exit 1, not argparse's own default of 2.** `cli.py` deliberately overrides `ArgumentParser.error()` for exactly this reason: exit 2 already means "login required or session expired" in this CLI's contract, so a typo'd flag has to land somewhere else, or a caller branching on "exit 2 → run login" could be fooled by an unrelated usage mistake into thinking a live session had expired. `scrape-fb` with no subcommand, an unknown flag, or a missing required identifier — all exit 1, same as "unexpected error," on purpose.

**Reading exit 4 correctly.** The stderr line accompanying a zero-post result distinguishes two different situations: if the internal stop reason is `unknown_error`, scrolling was interrupted before a single post was captured — rerun with `-v` for the underlying cause. Otherwise, the message points at a possible Facebook response-shape change and the package's issue tracker — which in practice usually means the target wasn't actually a personal profile timeline (a Page/Group/album slipped through identifier validation because it superficially matched the accepted URL shapes). Check the identifier's actual target before assuming the parser broke.

**Reading exit 7 correctly.** `--limit` is checked before `--since` on every scroll batch, so `--limit N --since <date>` together will almost always stop on the limit — reported as ordinary success (exit 0) — with `--since` never independently verified; that is expected, not a bug, because the CLI treats "got exactly N posts, as asked" as a complete success regardless. Exit 7 is reserved for the narrower case where `--since` was requested *alone* (or the limit wasn't hit first) and the run stopped on `max_scrolls` (ran out of scroll budget) or `feed_stalled` (Facebook stopped returning new posts) — both genuinely inconclusive about whether the requested date was reached. The stderr summary always includes the real post count and observed date range either way, so a partial run is never silently indistinguishable from a complete one.

## Legal exposure beyond Meta's ToS

Posts captured this way belong to other people — authors, commenters, anyone tagged or mentioned — and collecting/storing identifiable personal data about other people can make the person running this tool a data controller under GDPR, CCPA, or similar law in some jurisdictions, with real obligations around lawful basis, honoring deletion/access requests, and limiting retention; "I did this for personal use" isn't automatically a lawful basis on its own. The practical takeaway is the same one already in play for PII generally: keep what's captured to what's actually needed, and delete it once it's served its purpose. See the `scraper-for-facebook` package's own `DISCLAIMER.md` (https://github.com/tjdwls101010/Scraper-for-Facebook/blob/main/DISCLAIMER.md) for the fuller discussion, including a note on maintainer identifiability that doesn't change how this skill should be used but is worth knowing about.

## Troubleshooting

**Checkpoint (exit 3).** Meta has flagged the session mid-run with a security checkpoint. Never retry automatically — that's the single behavior most likely to turn a checkpoint into an outright ban. The only fix is a real, headed `scrape-fb login` to resolve the challenge by hand.

**Session expiry (exit 2, after a profile that used to work).** A previously-good session can simply expire — this looks identical to never having logged in at all from the CLI's point of view, and the fix is the same either way: `scrape-fb login --profile <name>`.

**`SingletonLock` / `ProcessSingleton` error on `status`, `doctor`, or `fetch`.** Chromium refuses to open a second instance against the same profile directory at once. This almost always means a `scrape-fb login` for that same `--profile`/`--profile-dir` is still sitting open somewhere — most often stuck at the "press Enter here to continue" prompt in a terminal nobody returned to. Find and finish (or kill) that process before retrying; this is not a sign the profile itself is corrupted.

**The scroll-pause floor is silent unless you look at stderr.** `--scroll-pause` below 0.5 seconds on either end is raised automatically, with a one-line stderr note (`scrape-fb: --scroll-pause 0,0 raised to 0.5,0.5 (minimum is 0.5s)`) — the run still proceeds normally at the floor instead of erroring, so don't mistake the note for a failure, and don't read its absence as "the pause I asked for was honored" without checking whether it was already above the floor.

**A checkpoint or expiry can also masquerade as thin/zero content rather than the expected exit code**, if something upstream changed — when in doubt after any surprising `fetch` result, run `scrape-fb status` directly rather than inferring session health from `fetch`'s exit code alone.
