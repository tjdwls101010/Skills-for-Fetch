# Logged-in sites (Tier-1) and generic XHR capture

Trigger: the task involves a saved login (`uf login`, `--profile`) or `--capture-xhr`/`--scroll`. Most fetches are anonymous and never load this file.

## What Tier-1 actually is

"Tier-1" means cookie-auth sites where a normal logged-in browser session is the whole barrier — Reddit, most forums, most news sites with a paywall-lite login. It does **not** mean Facebook or X: both fortify their logged-in surfaces (Meta session tokens, X's single-use transaction IDs) well beyond what a saved cookie profile can reliably parse, which is why those are separate skills over maintained CLIs rather than an extension of this mechanism. If a site turns out to need more than "log in once, reuse the cookies," it's out of scope here — don't force it.

## `uf login <site>` and reusing it

```
uf login reddit                     # opens a real, visible browser at a login page
uf login mysite --url https://example.org/login   # any other site: give the login URL
```

Log in by hand (2FA, captcha, whatever the site needs), then return to the terminal and press Enter — the browser closes and the session is saved to a profile dedicated to `<site>` (not your everyday Chrome profile; see G-keychain below). From then on:

```
uf fetch https://reddit.com/r/somewhere --profile reddit
```

`--profile` is a `uf fetch`-only mechanism — `uf crawl` has no `--profile` flag today, so a logged-in multi-page crawl isn't currently possible; only single-page fetches can reuse a saved session.

`--profile` pins the fetch to the browser rung and never escalates further — see G-profile-engine. It also acquires an exclusive lock on that profile for the duration of the call (G-cdp's neighbor: two concurrent uses of the same saved profile crash with an opaque browser error, not a clean message, unless something serializes them — that's this lock's whole job). If a run reports "profile in use," another `uf` command or `uf login` on the same site is genuinely still running; wait for it rather than force anything.

## `--capture-xhr` and `--scroll`

Some pages only put the content you want in a backend JSON response, not the rendered DOM — infinite-scroll feeds, paginated APIs, anything a SPA loads via `fetch()` after the initial page load. `--capture-xhr REGEX` matches those responses by URL and saves them to a `<slug>.captured.json` companion next to the page's markdown; `--scroll N` triggers the lazy-loading in the first place by scrolling N times before reading the page. Both force the browser rung for the same reason `--profile` does: the fast HTTP path never opens a real page, so it can neither scroll nor observe network traffic.

```
uf fetch https://example-spa.com/feed --capture-xhr "api/posts" --scroll 5
```

If `--capture-xhr` matches zero responses after scrolling, the fetch fails validation and escalates/errors rather than silently returning an empty capture — a pattern that never matches is exactly as informative as an error, so it's treated as one.

## Honest permission, and it's not just your data

`--profile`/`uf login` gets you exactly what your own logged-in session can see in a browser — nothing more. It doesn't defeat privacy settings or reach content your account isn't already entitled to. But what it *can* see usually includes other people's posts, names, and comments, not just yours — that's why captured output lives in the gitignored `./.tmp/` root, is personal-scale only, and is never shared or committed. Automating even your own account can still brush against a site's terms of service: keep volume low, use a throwaway/dedicated account when the content actually matters, and never leave this looping unattended.

## Gotchas

- **G-cdp.** Attaching to an *existing* browser via `cdp_url` does not inherit that browser's login — you get a fresh, logged-out context. `--profile`'s `user_data_dir` reuse is the only path that actually carries a saved session across runs; don't try to shortcut it by pointing at a browser that's already open.
- **G-profile-engine.** `--profile` is pinned to the browser rung (`DynamicFetcher`/`DynamicSession`, Playwright-managed Chromium) and never escalates to the stealth rung (`StealthyFetcher`, patchright-managed Chromium) even on `--mode auto`. Both are Chromium-family as of the currently pinned scrapling version, so this isn't a cross-browser-family incompatibility the way it would be with a Firefox-based stealth engine — it's simplicity: the profile/lock/capture path is proven on one engine, and there's no reason to also risk it on the other for a session that's carrying a live login. Re-check this reasoning if a scrapling upgrade ever changes what engine the stealth rung uses.
- **G-keychain.** Never point `--profile` at your everyday Chrome profile. macOS encrypts Chrome's cookie store with Keychain, so an external process reading it usually can't anyway — but even where it could, a dedicated `uf login` profile avoids the directory-lock conflict of your daily browser being open at the same time. `uf login` always creates a profile under `~/.ultra-fetch/profiles/`, never reuses your real browser's.
- **G-capture-body.** Captured responses live on the *returned* object's `.captured_xhr` list (a list of full response objects), each with a `.body` that is **raw bytes**, not text — decode it before doing any string work, and expect NDJSON (one JSON object per line) as often as a single JSON blob, since that's a common shape for paginated backend APIs.
- **G-spaidle.** Don't expect `--scroll`-triggered loads to be signaled by the network going idle — many SPAs poll continuously and never go idle at all. Scrolling is paired with a fixed wait after each step instead of waiting for a network signal that may simply never come.
- **G-expiry.** A saved profile's session can expire. When it does, a fetch that used to succeed suddenly looks like thin content or hits a login-wall marker — that's the signal to re-run `uf login <site>`, not a sign the site or the tool broke.
