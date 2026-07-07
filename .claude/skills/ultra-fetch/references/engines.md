# Engine internals

Trigger: `uf setup` failed, a fetch's rung behavior looks wrong, or you're changing a library version, a validation threshold, or how a rung works. Steady-state fetching never needs this file.

## The two venvs, and why they can't merge

`~/.ultra-fetch/venvs/fetch/` carries `scrapling[fetchers]` + `trafilatura` + `rank_bm25` (used by `fetch.py`; the Tier-1 `--profile`/`--capture-xhr` logic in `engine/session.py` is fetch-only — `crawl.py` has no `--profile` flag). `~/.ultra-fetch/venvs/crawl/` carries `crawl4ai` alone (used by `crawl.py`). They're separate because `crawl4ai` pins `lxml~=5.3` while `scrapling>=0.4.9` needs `lxml>=6.1.1` — co-installing either backtracks the other to a broken/ancient version rather than erroring loudly (G-lxml). `engine/save.py` and `engine/routes.py` are stdlib-only and safely imported from either venv; nothing else crosses the boundary.

## scrapling (fetch venv) — verified against installed 0.4.10

Three fetchers, one per rung:

- **Fast** — `from scrapling.fetchers import Fetcher; Fetcher.get(url, impersonate="chrome")`. Returns a `Response` (`.status` int, `.html_content` str, `.body` bytes, `.url` the final URL after redirects).
- **Browser** — `DynamicFetcher.fetch(url, **kwargs)`, a classmethod that internally does `with DynamicSession(**kwargs) as session: return session.fetch(url)` — it opens and closes its own browser per call, so `engine/session.py` never manages a `DynamicSession` object directly for one-shot fetches. Key kwargs: `headless`, `user_data_dir` (Tier-1 profile reuse), `capture_xhr` (a plain regex string matched against XHR/fetch response URLs), `page_action` (a callable run after navigation, signature `def action(page): ...`), `timeout`.
- **Stealth** — `StealthyFetcher.fetch(url, **kwargs)`, same shape, plus `solve_cloudflare`. As of 0.4.10 this is **patchright-managed Chromium**, not Firefox/camoufox — see G-profile-engine in `references/logged-in.md` for why that matters for `--profile` handling. Re-verify this if `versions.scrapling` in `config.json` ever moves past 0.4.10; scrapling's own docs/tutorials elsewhere still describe an older camoufox-based stealth engine, so don't assume either direction without checking installed source.

Captured XHR responses live on `response.captured_xhr` (a list of full `Response` objects, **not** `page.captured_xhr`) — `engine/session.py::read_captured_xhr` is the one place that reads this; don't reimplement it elsewhere.

`uf setup` provisions browsers via the `scrapling` console script (`<fetch_venv>/bin/scrapling install`) — not `python -m scrapling`, which fails outright (scrapling ships a Click CLI with no `__main__.py`).

## crawl4ai (crawl venv) — verified against installed 0.9.0

`async with AsyncWebCrawler() as crawler: results = await crawler.arun(url, config=CrawlerRunConfig(...))`. With a `deep_crawl_strategy` set and the default `stream=False`, `arun` returns a plain `list[CrawlResult]` — one entry per page, each with `.url`, `.metadata["title"]`, and `.markdown` (a str-subclass with `.raw_markdown` as the field `crawl.py` actually saves).

Deep-crawl strategies live at `crawl4ai.deep_crawling.{BFSDeepCrawlStrategy, BestFirstCrawlingStrategy}` — note the "Crawling" spelling on the second one. `crawl.py` uses `BFSDeepCrawlStrategy(max_depth=..., max_pages=...)`. Concurrency/delay (`CrawlerRunConfig`'s `mean_delay`/`max_range`/`semaphore_count`) do take effect during a deep crawl even though the top-level `arun()` bypasses the dispatcher when a strategy is set — internally, `BFSDeepCrawlStrategy._arun_batch` calls `crawler.arun_many()` once per BFS depth level, and `arun_many` is exactly the dispatcher-based path those fields configure. `check_robots_txt: bool` (default `False`) is the `--respect-robots` flag's underlying field.

`crawl.py` dedupes by URL across the whole run: `BFSDeepCrawlStrategy` can rediscover the seed (or another already-saved page) via a nav link before its own depth-based dedup catches it, and without an explicit `seen_urls` guard this writes the same page twice under two different ordinals — confirmed live against a real site during this skill's build.

`uf setup` runs `<crawl_venv>/bin/crawl4ai-setup` for post-install browser provisioning — it has no `--help`/dry-run; invoking it always attempts the real install (idempotent — a second run just hits the cache in seconds). `crawl4ai-doctor` is also installed as a console script and does its own live health check (fetches `crawl4ai.com`) if you want a second opinion beyond `uf doctor`'s own smoke test.

## trafilatura + rank_bm25 (fetch venv) — verified against installed 2.1.0 / 0.2.2

`trafilatura.extract(html, output_format=..., include_tables=True, include_comments=False)` — `output_format` accepts `csv`/`html`/`json`/`markdown`/`txt`/`xml`/`xmltei`. Note the CLI's user-facing `--format text` maps to trafilatura's `txt`, not `text` — that translation lives in `fetch.py`'s `FORMAT_TO_TRAFILATURA` dict; passing `"text"` straight through crashes with `AttributeError: Cannot set format`. `extract()` never raises on bad/thin/empty input — it returns `None`, which `engine/escalate.py` treats as "no extractable content" (escalate), not a crash.

`trafilatura.extract_metadata(html).title` is what `fetch.py` uses for the result's `title` field — more robust than a hand-rolled `<title>` regex since it falls back to OpenGraph/meta tags.

`rank_bm25.BM25Okapi` has no built-in tokenizer — `engine/markdown.py::query_filter` tokenizes both the corpus (markdown split into paragraphs) and the query with a plain `[a-z0-9]+` regex over the lowercased text; passing raw untokenized strings to `BM25Okapi` would iterate characters instead of words. The distribution name is `rank-bm25` (hyphen) even though the import name is `rank_bm25` (underscore) — `setup.py`'s version lookup tries both spellings.

## Where the validation gate lives

`engine/escalate.py` owns every threshold and marker as module-level constants — `MIN_CONTENT_CHARS`, `CHALLENGE_MARKERS`, `LOGIN_WALL_MARKERS` — edit them there, not at call sites. If a real anti-bot challenge page ever slips past validation (accepted as real content), the fix is almost always adding its telltale phrase to `CHALLENGE_MARKERS`, not touching the escalation control flow. The `--query` check is intentionally loose — it passes if *any* real word (length > 2) from the query appears anywhere in the markdown, not the whole query phrase verbatim; a real page's prose scatters topic words across separate sentences rather than repeating a phrase intact, and an exact-phrase requirement was confirmed live to reject perfectly good matches during this skill's build.

`_classify()` splits rejection into `blocked` (a challenge/login-wall marker, a capture-xhr miss, a query miss — high-confidence signals something is actually wrong) versus `thin` (just under `MIN_CONTENT_CHARS`, on its own not evidence of anything). When every rung exhausts with only `thin` ever true and nothing ever `blocked`, `run_ladder` returns the best (longest) attempt anyway with `soft_thin=True` rather than raising — confirmed live that a real public tweet's genuine, correctly-extracted text (~150 characters) was being discarded entirely before this existed, indistinguishable from an actual anti-bot wall. If you need to make the gate stricter again for some case, tighten `is_soft_thin_pass`'s `MIN_SOFT_PASS_CHARS` floor or add a new `blocked` signal — don't just raise `MIN_CONTENT_CHARS`, which would only make more real short content look "thin" without helping tell it apart from a wall.

## Adding a rung, or changing the ladder order

`engine/escalate.py::run_ladder` decides which rungs to attempt from `mode` and whether `profile`/`capture_xhr`/`scroll` are set (`forced_browser`) — read the three `try_*` booleans together before changing any one of them; they encode "an explicit `--mode X` tries only X" and "a forced-browser fetch skips fast and never reaches stealth" as one small piece of boolean algebra, not as separate special cases scattered through the function.

## Gotchas

- **G-lxml.** crawl4ai pins `lxml~=5.3`; scrapling needs `lxml>=6.1.1`. Never let anything import both in one venv — the whole two-venv architecture exists solely to keep this from happening.
- **G-extras.** Bare `pip install scrapling` omits the fetchers/CLI/curl_cffi extras. Always install `scrapling[fetchers]>=0.4.9` — the version floor matters too; a looser one can backtrack to an ancient scrapling release with none of the APIs this skill depends on.
- **G-sharedcache.** Playwright/patchright browser binaries live in shared caches (`~/Library/Caches/ms-playwright`, no separate patchright cache dir — it reuses the same convention) across whatever venvs use them, but a venv's installed *client* version has to match the cached build. Re-run that venv's own install step (`scrapling install` / `crawl4ai-setup`) after any scrapling/crawl4ai upgrade rather than assuming the existing cache still matches. A corrupted or partially-downloaded browser binary in this same shared cache (e.g. two installs racing on it at once) surfaces as a raw, uncaught Playwright/dlopen crash straight out of `uf fetch` — a big native traceback instead of the usual clean `uf fetch: ...` message or `EscalationExhausted` JSON — confirmed live as a real, if rare and self-resolving, failure mode; a plain retry or `uf doctor` (which re-verifies both venvs end to end) is the right first response, not a sign the URL or the skill itself is broken.
- **G-os.** v1 is macOS-only. Hardcoded cache paths (`~/Library/Caches/...`) and the bash launcher live in one place each so a later port is a localized change, not a rewrite.
- **G-prereq.** `uf setup` assumes nothing is pre-installed except a bootstrap `python3` (macOS ships one via the Xcode Command Line Tools) — `uv` is the one true prerequisite it installs itself if missing, and `uv` in turn supplies Python 3.12 for both venvs, so a missing/wrong system Python is never a failure mode. `gh`/`yt-dlp` are optional route helpers; their absence degrades `references/routes.md` gracefully without touching core fetch/crawl.
- **G-yt.** `yt-dlp` can be installed-but-broken (a Homebrew Python upgrade orphans its shebang, so it exists on disk but crashes on every invocation) — `uf setup`/`uf doctor` health-check it by *executing* `yt-dlp --version`, never by `which`, and reinstall via `uv tool install yt-dlp` when it's broken (confirmed reproducible on the build machine, and confirmed self-healing).
