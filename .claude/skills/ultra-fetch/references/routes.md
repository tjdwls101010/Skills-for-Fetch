# Site routes — exact commands

Trigger: the URL is `github.com` or `youtube.com`/`youtu.be`. `engine/routes.py` only detects this and prints a heads-up — it never runs these commands itself. Run them directly; don't shell out to `uf` for these sites, and don't scrape what an API already serves structured. `gh` and `yt-dlp` return exactly the fields you want in one call, with no HTML boilerplate to strip and no anti-bot ladder to climb — scraping the rendered page is strictly worse here, not just slower.

## GitHub → `gh`

`gh` is optional and not guaranteed to be present — `uf setup` tries to install it (via Homebrew) but can't if Homebrew itself is missing, and a fresh machine may never have run `uf setup` at all (references/engines.md's G-prereq). Check first with `gh auth status`: authenticated output means real, generous API rate limits — far higher than an anonymous scrape would get before a 429 — while a "command not found" or an auth error means falling back to `uf fetch` for this URL instead of assuming `gh` works.

```bash
# A repo's overview
gh repo view {owner}/{repo} --json description,stargazerCount,createdAt,primaryLanguage

# A single file's raw content (no base64 decoding needed — the raw media type does it)
gh api repos/{owner}/{repo}/contents/{path} -H "Accept: application/vnd.github.raw"

# An issue, with its comment thread
gh issue view {number} --repo {owner}/{repo} --json title,body,state,comments

# A pull request: the diff itself
gh pr diff {number} --repo {owner}/{repo}

# A pull request: title/body/reviews without the diff
gh pr view {number} --repo {owner}/{repo} --json title,body,comments,reviews

# Code search across GitHub
gh api search/code -f q="{query}"
```

If a URL points at a specific file/blob (`github.com/{owner}/{repo}/blob/{ref}/{path}`), extract `{owner}/{repo}/{path}` and use the raw-content call above rather than fetching the rendered HTML page.

## YouTube → `yt-dlp`

```bash
# Metadata only — title, description, duration, view/like counts, upload date, etc.
yt-dlp --dump-json --skip-download "{url}"

# Transcript/captions (auto-generated captions exist for almost every video; manual captions are used instead when present)
yt-dlp --write-sub --write-auto-sub --sub-lang en --skip-download --convert-subs srt "{url}"
# → writes a .srt file next to where yt-dlp is run; read that file for the transcript text.

# A playlist's video list without touching each video
yt-dlp --flat-playlist --dump-json "{playlist_url}"
```

`yt-dlp` is health-checked by `uf doctor` (it's known to silently break after a Homebrew Python upgrade orphans its shebang — G-yt in references/engines.md). If a `yt-dlp` command fails outright, re-run `uf doctor` before assuming the video itself is the problem.

## Facebook / X

Not handled here — `engine/routes.py` detects these hosts too, but the reasoning and the pointer to `facebook-fetch`/`x-fetch` live inline in that module and in this skill's SKILL.md body (the breadcrumb needs to be read *before* `uf fetch` is invoked, not after — see SKILL.md).

## Future low-effort routes (no API keys, not yet wired into `routes.py`)

Small, keyless JSON/API endpoints that beat scraping whenever they apply:

- **Reddit** — append `.json` to almost any Reddit URL (`.../comments/{id}.json`) for the full thread as structured JSON, or `.rss` for a feed.
- **Hacker News** — the Firebase API, `https://hacker-news.firebaseio.com/v0/item/{id}.json`.
- **Wikipedia** — the REST summary endpoint, `https://en.wikipedia.org/api/rest_v1/page/summary/{title}`, or the fuller MediaWiki Action API for full article text.
- **arXiv** — `http://export.arxiv.org/api/query?id_list={id}` for abstract/metadata.

These aren't classified by `routes.py` yet (no dedicated skill claims them the way GitHub/YouTube are heading toward one) — but reach for the API before `uf fetch` if the URL matches one of these.

## Transitional note

This routing (and the GitHub/YouTube detection in `routes.py`) exists only until the dedicated `github-fetch`/`youtube-fetch` skills ship. When they do, this file and that detection shrink to a one-line breadcrumb matching the Facebook/X shape above — the isolation of routing logic in one small module is exactly what makes that swap a localized edit instead of a rewrite.
