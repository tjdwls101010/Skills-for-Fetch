# facebook-fetch — Implementation Plan

> Build spec for the `facebook-fetch` skill. Read [OVERVIEW.md](OVERVIEW.md) first, then this file. This is a **thin, near-code-free skill**: its entire job is to teach Claude *when* and *how* to drive the already-published `scrape-fb` CLI correctly, handle its exit codes, read its output, and surface the account-safety framing. The scraping itself — login, scroll, parse, save — is done by the CLI, not the skill. Do not reimplement any of it.
>
> **Ground truth:** every command, flag, exit code, and output-shape fact in this plan was read directly from the shipped source of `scraper-for-facebook` v0.1.0 (`package/scraper-for-facebook/`, CLI `scrape-fb`, on PyPI). The package's own internal design lives in [SCRAPER-FOR-FACEBOOK-PLAN.md](SCRAPER-FOR-FACEBOOK-PLAN.md) — that is NOT this skill; do not conflate them. If the CLI's contract has changed since (check `scrape-fb --help` and its `CHANGELOG.md`/`wiki/` at build time), reconcile before shipping.
>
> **Target OS: macOS only for v1** (the CLI's first-class platform). Login is a headed, by-hand GUI step — not a headless-server scenario.

---

## 1. Purpose & trigger

A user wants the posts from a **personal Facebook profile timeline** they can see while logged in — their own feed-visible profile or someone else's public/visible profile — as structured data saved to a file, not a screenshot or a copy-paste. Built-in `WebFetch` cannot do this: Facebook is a login-walled, Meta-token-fortified SPA. `scrape-fb` can, by reusing a real logged-in browser session and observing the timeline's GraphQL responses.

**Triggers (tune the `description` toward these, even when the user never says "scrape"):** "get posts from my Facebook", "pull <person>'s Facebook timeline", "save this facebook.com/<profile> feed", "what has <profile> posted lately", a bare `facebook.com/<profile>` URL paired with an intent to read *their posts*.

**Not this skill:** anonymous one-off fetch of a public facebook.com page with no login (that's `ultra-fetch`); Pages, Groups, photo albums, Marketplace, Instagram, or Threads (the CLI supports **personal profiles only** — §5 trap 1); logged-in *X/Twitter* (that's `x-fetch`).

---

## 2. The skill is thin by design (what it does and doesn't hold)

Per [OVERVIEW.md](OVERVIEW.md) §3, `scrape-fb` already logs in, scrolls with a human-like pacing floor, parses the timeline, redacts diagnostics, and writes a JSON/NDJSON file to a PII-safe location. So the skill holds **none** of that. It holds only what Claude can't derive from `scrape-fb --help` alone and would otherwise get wrong:

- **When** to reach for it vs `ultra-fetch` (the trigger carving above).
- The **account-ban reality** it must state before the first logged-in use (§4).
- The **one-time setup** sequence and the fact that `--version` proves nothing — `doctor` is the real readiness check, and `doctor` passing still doesn't mean *logged in* (§3, §5 trap 2).
- The **exit-code contract**, which is the skill's core competence: a naive caller treats any nonzero as "it failed", but here 5 and 7 are "worked, here's the nuance" and 2/3 have specific recoveries (§6).
- The **output contract**: the file is a bare JSON array; all run metadata is on **stderr**; the file is unredacted third-party PII (§7).
- The traps that make a naive invocation silently wrong (§5).

**File structure (decided):** `SKILL.md` + one shallow reference.
```
.claude/skills/facebook-fetch/
├── SKILL.md                 # every-run backbone: trigger, ban warning, the command, critical traps, compact exit-code table, PII rule
└── references/
    └── scrape-fb.md         # loaded when running a real fetch / handling an error / first-time setup:
                             #   full one-time-setup walkthrough, full fetch flag reference,
                             #   exhaustive per-command exit-code table, output field schema, troubleshooting
```
Why one reference, not more: the deep material (setup walkthrough, full flags, full exit table, field schema, troubleshooting) shares a **single load trigger** — "I'm actually running this / something went wrong / I'm setting it up the first time." Splitting it finer would make Claude guess which file holds the exit-4 remedy. Why not fold it all into SKILL.md: the field schema and setup walkthrough are dead weight on the many turns that just decide *whether* to use the skill or fire a routine fetch — keeping them out of the body keeps the load-bearing lines (ban warning, the command, the traps) unburied. State this tradeoff in the plan; the implementer should re-check it against the actual drafted length.

---

## 3. One-time setup (documented steps the user runs; the skill guides)

The skill does not automate this — login is a by-hand GUI step and browser provisioning is a hundreds-of-MB download. The skill *recognizes* an un-ready install and walks the user through it. The sequence (all from the verified contract):

```bash
uv tool install scraper-for-facebook    # isolated install into a private venv (see OVERVIEW §4 "Install & isolation")
scrape-fb setup                          # provisions an ISOLATED Chromium (~300MB, one time)
scrape-fb login                          # opens a REAL headed browser; user logs in by hand (incl. 2FA),
                                         #   then presses Enter in the terminal to persist the session
scrape-fb doctor                         # health check: launches headless, confirms a graphql XHR round-trips
```

- **Isolation is mandatory** (OVERVIEW §4): `scrape-fb`'s `scrapling[fetchers]` pins exact Playwright/patchright versions; a shared `pip install` can silently break another Playwright tool. `uv tool install` (or `pipx`) gives it a private venv.
- **`setup` is separate from install** — installing the wheel does NOT download the browser. `--force` reinstalls after a corrupt/partial provision.
- **`login` blocks on a terminal `input()` prompt** ("…press Enter here to continue…") while the headed window is open — the user must finish login by hand *then* return to the terminal. It is one-time per `--profile` (default profile name `default`).
- **`doctor` verifies the browser/capture pipeline, NOT login state** — it passes even against Facebook's logged-out page. So readiness has two independent axes: *pipeline healthy* (`doctor`, exit 0) and *session live* (`status`, exit 0, or just attempt the fetch and branch on exit 2). The skill must not treat a green `doctor` as "logged in."
- **`--version` attests nothing operational** — it only proves the entry point imports. Never use it as a readiness gate.

The skill's readiness logic: try the fetch; on exit 2 → guide `scrape-fb login`; on a "command not found"/import failure → guide `uv tool install` + `scrape-fb setup`. Or proactively run `scrape-fb doctor` + `scrape-fb status` when unsure. Keep the full walkthrough in the reference; the body carries only "first use needs a one-time login — see reference."

---

## 4. Account-safety framing (must be prominent in SKILL.md, stated before first use)

Automating a logged-in **Meta** account is **against Facebook's Terms of Service** and can get the account **flagged, checkpointed, or permanently banned** — losing photos, messages, and linked logins. This is real, irreversible harm, not a disclaimer to skip. The skill must state it plainly the first time the user reaches for a logged-in fetch, and reflect these facts (all enforced or advised by the CLI itself):

- **Recommend a dedicated/throwaway account**, never the user's primary.
- **The CLI enforces a 0.5s scroll-pause floor that cannot be bypassed** (`--scroll-pause 0,0` is silently raised to `0.5,0.5` with a stderr note). The skill must not try to defeat pacing, and should not raise `--max-scrolls` casually — deeper history means more scrolling means more ban risk.
- **Never loop it unattended.** There is no batch mode, scheduler, or daemon, by design. One profile per run; do not run overlapping `scrape-fb` commands on the same profile (Chromium locks the profile dir → opaque `SingletonLock` crash).
- **A checkpoint (exit 3) is never auto-retried and the skill must not retry it either** — hammering a flagged session raises ban risk. Send the user to log in via a real headed browser and resolve the challenge.

Keep this framing in the body (it gates behavior on the path that matters); the deeper "why" and the GDPR/data-controller note go in the reference or a short DISCLAIMER pointer.

---

## 5. Critical traps (the highest-signal lines — put each in the body)

These are the mistakes a competent-but-uninformed caller makes precisely because the CLI's behavior is non-obvious. Each belongs in SKILL.md.

1. **Personal profile timelines ONLY.** Accepted identifier forms: a bare vanity (`zuck`), a numeric id, `profile.php?id=<digits>`, or a full `https://facebook.com/<...>` URL (hosts `facebook.com`/`www.`/`m.` only). A Page, Group, album, Instagram, or Threads URL is **out of scope** — not "supported but flaky." It surfaces as exit 4 (zero posts) or exit 5 (unavailable). Don't send those here.
2. **`doctor` green ≠ logged in.** `doctor` checks the capture pipeline (works logged-out); session health is a separate check (`status`, or branch on the fetch's exit 2). Don't conflate.
3. **The output file is a bare JSON array with NO metadata** — count, date range, stop reason, and saved-path all go to **stderr only**. To learn what happened (did `--since` fully reach? how many posts? why did it stop?), the skill must **read stderr**, not just the file.
4. **Always pass an explicit `--output ./.tmp/facebook-fetch/<slug>.json`.** Without `--output`, the file lands in the tool's platformdirs data dir (safe from git, but hard to locate and it accumulates). The explicit path puts it in the family-wide gitignored `./.tmp/` root (OVERVIEW §4) where Claude can Read it — and it's still unredacted PII, so never commit or print it verbatim.
5. **Argparse usage errors exit `1`, not `2`.** This CLI deliberately overrides argparse (default 2 → 1) so that exit 2 means *only* "login required/expired." So a typo'd flag also exits 1 — exit 1 is overloaded (invalid identifier, bad flags, unexpected error). Never read exit 2 loosely.
6. **`--since` is best-effort; exit 7 is success-with-nuance, not failure.** `--limit` is checked before `--since` each batch, so `--limit N --since <old>` usually stops on the limit (exit 0) with `--since` never verified. Exit 7 fires only when `--since` was set *and* the run hit the scroll ceiling / a stalled feed first. The partial file is valid data. For deep history, the skill must inspect the stderr summary, not assume completeness.
7. **`--max-scrolls` (default 40) is a hard ceiling overriding `--limit`/`--since`.** Deep `--since` needs it raised — which raises ban risk (tie back to §4). Don't raise it silently.
8. **Never `--no-redact`.** `--raw` writes the raw captured node into the output file, **redacted by default**; `--no-redact` disables that scrubbing and prints a warning every time. The skill should essentially never use `--no-redact`, and rarely needs `--raw` at all (the parsed fields cover normal use).

---

## 6. Exit-code contract (the skill's core competence)

Body carries a **compact** version of this table (code → one-line meaning → what to tell the user / do next); the reference carries the full per-command breakdown (fetch + login/status/setup/doctor). Verified from source:

| Exit | Meaning | Skill's response |
|---|---|---|
| **0** | Success (limit/since/until satisfied, or feed exhausted) | Read the `--output` file; summarize from it + the stderr line. |
| **1** | Invalid identifier, bad flags/usage, or unexpected error | Fix the identifier/flags. With `-v`, rerun for scrubbed detail. **Not** a session problem. |
| **2** | Login required / session expired | Guide `scrape-fb login --profile <name>`, then retry. (The message already says so.) |
| **3** | Checkpoint — Meta flagged the session mid-run | Tell the user to log in via a real headed browser and resolve the challenge. **Never auto-retry.** |
| **4** | Zero posts retrieved | If stderr stop-reason is `unknown_error`: capture was interrupted — rerun with `-v`. Else: likely a Page/Group/unsupported target, or parser drift vs a FB response-shape change → check the identifier is a personal profile; if it is, it's worth a package issue. |
| **5** | Profile unavailable (memorialized, blocked, restricted, nonexistent) | Confirmed "nothing to see," not a bug. Have the user verify the identifier and that they can view it themselves while logged in. |
| **7** | Partial `--since` (requested but not confirmed reached within the scroll budget) | **Not an error** — posts were still written. Offer: raise `--max-scrolls` (with the ban-risk caveat), narrow `--since`, or accept the partial. |

There is no exit 6. The run outcome is always reported on **stderr** (never in the file) — but *what* is there depends on the exit: on a **successful** run (exit 0/7) it's the full one-line summary — post count, observed date range, stop reason (`limit_reached`/`since_crossed`/`feed_exhausted`/`max_scrolls`/`feed_stalled`/`unknown_error`), and saved path; on **exit 4** it's just the stop reason (no count/range); on **exit 1/2/3/5** it's only a short error string. So read stderr for the outcome, but don't expect the count+range+stop-reason triple after a failed run — the exit code itself is the primary signal there.

---

## 7. Output contract & handoff

The canonical invocation the skill teaches:
```bash
scrape-fb fetch <identifier> --profile <name> --limit <N> [--since YYYY-MM-DD] \
    --format json --output ./.tmp/facebook-fetch/<slug>.json
```
Then: **Read the file, render a short human summary/preview, never dump the raw array to context.**

- **`<slug>` (define it inline — there's no engine module):** sanitize the identifier to `[A-Za-z0-9-]` and append a UTC timestamp, e.g. `zuck-20260705T1430Z.json`, so two runs of the same profile never collide and silently overwrite a prior capture (each is PII you don't want to lose unknowingly). This mirrors the CLI's own default-filename scheme; the skill just constructs the string.
- **File shape:** `--format json` → a single top-level JSON **array** of post objects (pretty-printed); `--format ndjson` → one object per line. No wrapper object, no `posts` key, no metadata in the file.
- **A post object's fields** (for the skill to know what it can surface): `id` (stable dedup key — dedupe on this, never on `captured_at`), `url`, `type` (`status`/`photo`/`video`/`shared`/`link`/`reel`/`life_event`/`unknown`), `is_pinned`, `author_name`/`author_url`/`author_id`, `created_at` (ISO-8601 UTC `Z`, may be null), `edited_at`, `text` (+ `text_truncated`, `text_resolved`), `media` (`{kind,url,width,height}` — URLs are signed/expiring/viewer-scoped, sensitive), `links` (`{url,title,description}`), `reaction_count`/`comment_count`/`share_count`, `shared_post` (a nested post built from the shared story; in typical personal-timeline data its own inner `shared_post` is null, but that's an observation, not a guarantee — a share-of-a-share can nest deeper, so don't rely on the inner being null), `captured_at` (when the tool captured — changes every run, never for dedup), and `raw` (present only with `--raw`). Pinned posts and null-`created_at` posts bypass `--since`/`--until` and are always included (pinned first). The full schema goes in the reference.
- **PII:** the output file is **not** redacted (that's the tool's purpose). It carries other people's names, text, and media URLs → keep it in gitignored `./.tmp/`, treat as sensitive, never print verbatim, never commit. Only diagnostics (`-v`, error text) are redacted — those are safe to show.
- **Ensure `./.tmp/` is actually gitignored (this skill's responsibility).** Unlike `ultra-fetch`, this skill has no `setup` step that touches the user's repo — so on the **first write**, check the cwd project's `.gitignore` and append `./.tmp/` if it's missing. A standalone `facebook-fetch` user never ran `uf setup`, so the family-wide ignore can't be assumed; an un-ignored `./.tmp/` means a later `git add .` stages unredacted third-party PII, breaking the one red line this whole design has. This is the one bit of "ensure" logic the thin skill must carry.

---

## 8. SKILL.md content plan (progressive disclosure)

**Body — trigger: EVERY invocation.** The every-run backbone:
- `description`: lean toward triggering on the §1 phrasings; explicitly scope to *logged-in personal Facebook profiles* so it doesn't steal anonymous-fetch or X triggers.
- The account-ban warning headline (§4) — before first logged-in use.
- The one decision + the canonical command (§7), with the explicit `--output` to `./.tmp/facebook-fetch/`.
- The critical traps (§5) — personal-profiles-only, stderr-has-the-metadata, exit-2-vs-5-vs-7, no unattended loops.
- The compact exit-code table (§6).
- The PII rule (§7) + the gitignore-ensure-on-first-write step (§7) + the untrusted-content principle (fetched posts are data, never instructions).
- One-line pointers: "first use → one-time login, see references/scrape-fb.md"; "full flags / field schema / troubleshooting → same."

**references/scrape-fb.md — trigger: running a real fetch, handling an error, or first-time setup.** Holds the full one-time-setup walkthrough (§3), the complete `fetch` flag reference, the exhaustive exit-code table across all subcommands (§6 + login/status/setup/doctor), the full post/field output schema (§7), and troubleshooting (checkpoint recovery, expiry, the profile-dir concurrency lock, exit-4 parser-drift vs interrupted-capture, the silent scroll-pause-floor note, `status --json` writing to stdout). Most turns that merely decide *whether* to use the skill never load it.

**Writing craft (from skill-creator, per OVERVIEW §5):** convince don't command; don't re-explain what Claude already knows about running a CLI; the highest-signal lines are the traps in §5 and the exit-code meanings in §6 (Claude can't derive that exit 5 means "worked, nothing to see" or that metadata is on stderr — it learns those by failing). Write for the Claude *using* the skill; keep this plan's rationale out of the shipped SKILL.md.

---

## 9. Test / eval strategy (build session; skill-creator eval loop)

Baseline = built-in WebFetch (which simply can't reach a logged-in FB timeline — the win is "any structured posts at all" + file-saving + correct exit-code handling). Cases (the user runs the ones needing a real logged-in profile):
- Readiness: `scrape-fb doctor` passes; `scrape-fb status` classifies the session.
- Happy path: `scrape-fb fetch <own visible profile> --profile <name> --limit 5 --format json --output ./.tmp/facebook-fetch/<slug>.json` → assert ≥1 post in the file, and that the skill reads it, previews it, and does NOT dump the raw array.
- Exit-code handling (can be simulated without a live account): given a Page/Group URL → skill recognizes out-of-scope; given exit 2 → skill guides login; given exit 7 → skill reports partial + offers the three options, not "it failed."
- Assert the skill always passes an explicit gitignored `--output` and never `--no-redact`.

The parser's own regression suite lives in the `scraper-for-facebook` package (SCRAPER-FOR-FACEBOOK-PLAN §13), not here — the skill trusts the CLI's output shape.

---

## 10. Open questions / future
- Whether to proactively run `scrape-fb doctor`+`status` before every fetch (safer, ~2 browser launches) vs try-fetch-and-branch-on-exit-code (cheaper, one launch) — lean try-first; revisit after the eval loop.
- Threads/Instagram: deferred to the `scraper-for-facebook` package roadmap (SCRAPER §19). If the CLI later grows those, this skill extends; no skill-side capture code either way.
- Multi-profile UX (the CLI supports `--profile <name>`): how the skill should name/track more than one logged-in FB identity, if the user has several. Likely a reference note, not v1 body.

---

## 11. Delegate the mechanical surface to `scrape-fb` ≥ 0.2.0 (shipped 2026-07-07, same session as the v0.2.0 release)

The v1 skill (shipped 2026-07-05) hand-copied the full flag table and the `Post`/`Media`/`LinkAttachment` field enumeration into `references/scrape-fb.md`. That mechanical layer drifted whenever the CLI changed and forced a skill edit for every such change — the maintenance/synchronization worry that motivated this revision. Now that `scrape-fb` v0.2.0 ships CLI self-description (SCRAPER-FOR-FACEBOOK-PLAN §10a: enriched `--help` + a `schema` subcommand, released to PyPI), the skill delegates that layer to the installed binary — version-matched and drift-free — and keeps only what the CLI can't emit. This is a deliberately narrow win: it removes drift on the *cheapest* layer (mechanical flags + field names), not on the layer that carries the skill's actual value (exit-code meaning, traps, safety) — that stays hand-maintained no matter what, because the CLI cannot express it.

What the reference **drops** (delegated to the CLI):
- The mechanical flag rows → an instruction to run `scrape-fb fetch --help` for the authoritative, version-matched flag list, defaults, and CLI-intrinsic semantics (the 0.5s floor, the `--max-scrolls` ceiling, the `--since` best-effort caveat now live in the help text itself).
- The `Post`/`Media`/`LinkAttachment` field enumeration → an instruction to run `scrape-fb schema` (offline, exit 0) for the authoritative field list.

What the reference **keeps** (the CLI cannot emit these — they are the skill's actual value):
- The exit-code *meanings* and the compact table (exit 5 = answer not error, exit 7 = not-a-failure, argparse usage errors = exit 1 not 2) — §6, unchanged.
- The cross-command traps (doctor ≠ logged-in, metadata-on-stderr-not-the-file, `--max-scrolls` silently caps a `--limit`-only run at exit 0, `SingletonLock` on concurrent same-profile use) — unchanged.
- The account-safety / ban framing and the GDPR/data-controller note — unchanged.
- Skill-*context* flag advice that would be wrong to put in the CLI's own `--help`: "always pass `--output ./.tmp/facebook-fetch/…` explicitly" (the CLI's platform-dir default is deliberately right for a standalone user, wrong for the skill's read-it-back-then-gitignore flow) and "never `--no-redact`".
- Field *usage* semantics `schema` can't carry: dedup on `id` never `captured_at`; `media`/`links` URLs are signed/expiring/sensitive; `shared_post` can nest deeper than one level; pinned / null-`created_at` posts bypass `--since`/`--until`.

New dependency this introduces (handle it, don't ignore it): the skill now requires `scrape-fb` ≥ 0.2.0 (it calls `schema`, absent before). State the minimum version in the skill, and make the readiness path treat an unknown `schema` subcommand (argparse exit 1) or a `--version` below 0.2.0 as "upgrade: `uv tool install --upgrade scraper-for-facebook`", distinct from an exit-2 login problem. This is a genuinely new failure mode the delegation buys — the honest tradeoff for dropping drift on the mechanical layer.

Expected size effect: the reference loses the 13-row flag table and the one-paragraph field enumeration (~25–30% of the file) while keeping every load-bearing line. Modest, but drift drops to near-zero *on the skill's copy* of exactly the parts that were most tedious to keep in sync. Be precise about that scope: this removes drift for the skill only — the `scrape-fb` package still hand-maintains the same flag/field surface in its own `README.md` and `wiki/` pages, a separate copy-count the package plan owns (SCRAPER-FOR-FACEBOOK-PLAN §10a's "scope the drift claim honestly" note), not something this skill revision fixes.

The `--max-scrolls` trap wording flagged by the plan review was also tightened in the same edit: SKILL.md previously said it "overrides `--limit` and `--since` regardless", which overstated it; it now reads "a ceiling that can cut a run short before `--limit`/`--since` is met", matching `scroll.py`'s actual per-iteration check order.

**Re-ran the E2E skill-loading test after the change**, per this section's own requirement — 4 fresh subagents (command construction via `--help` delegation; a schema/field question; two reasoning-only exit-code/version-mismatch scenarios), each given only the skill's file location, told to read SKILL.md first and open the reference only if directed there, and forbidden from reading CLI source. All 4 succeeded: the delegation didn't go too far, and no mechanical detail needed to move back into the skill. One agent surfaced a real, unplanned-for finding — this machine's actual `uv tool install`ed `scrape-fb` was still 0.1.0 (a leftover from before this session's release), so its `scrape-fb schema` call hit exit 1 for real; the agent correctly diagnosed this from the reference's version-check paragraph alone as "upgrade, not login" without being told the answer, which is exactly the behavior SCRAPER-FOR-FACEBOOK-PLAN §10a's version-mismatch mitigation was designed to produce. The local install was then upgraded to 0.2.0 (`uv tool upgrade scraper-for-facebook`) so the skill is genuinely functional on this machine, not just correct in the abstract.
