# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

HouseMaster automates building an overview of houses matching a client's criteria, extracting key data fully automatically. Target site:

```
https://www.funda.nl/zoeken/koop?selected_area=den-haag&price=250000-350000&floor_area=60-
```

`README.md` holds the full findings on how funda.nl serves its data — **read it before touching the extraction path**, it is the record of what was already investigated.

## Commands

`uv` (0.12.x) manages the project; Python is pinned to `>=3.14` (venv runs 3.14.6).

```bash
uv sync                                # install/refresh .venv; installs the package editable
uv add <pkg>                           # runtime dependency
uv add --dev <pkg>                     # dev dependency (goes to [dependency-groups].dev)

uv run housemaster fetch               # scrape into ./data (first run ~15 min)
uv run housemaster fetch --max-pages 2 --max-details 5 --photos-per-house 2
uv run housemaster serve               # web UI on http://127.0.0.1:8765
uv run housemaster status              # what the cache holds
uv run housemaster search              # live, no database -- the original probe

uv run pytest                          # full suite, offline
uv run pytest tests/test_devalue.py::test_cycle_terminates   # a single test
uv run ruff check . && uv run ruff format .
```

**`uv sync` / `uv add` fail while `serve` or `fetch` is running** — Windows will not let uv replace `housemaster.exe` while it is executing. Stop the process first, or use `.venv/Scripts/python.exe` directly for one-off scripts.

**Flask caches templates unless `--debug` is passed.** After editing anything in `web/templates/`, restart `serve` or the change will not appear.

The suite is **offline by default**: `tests/conftest.py` skips anything marked `network` unless `HOUSEMASTER_NETWORK_TESTS=1` is set. Run `HOUSEMASTER_NETWORK_TESTS=1 uv run pytest -m network` to exercise the live-site canary — that test is the early warning for funda changing its payload shape or Akamai tightening.

`cloakbrowser` is an **optional extra**, not installed by a plain `uv sync`. Only
add it when you actually need the Python stealth-browser fallback:

```bash
uv sync --extra browser      # installs cloakbrowser (~110 MB via playwright)
uv run cloakbrowser info     # then: binary path, version, licence, module status
uv run cloakbrowser install  # download the Chromium binary (already on this machine)
```

The `mcp__cloakbrowser__*` browser tools do **not** need this — they run off a
separate npm package and their Chromium lives in `~/.cloakbrowser/`.

## Architecture

Funda is a server-rendered **Nuxt 3** app that embeds its entire page state as JSON in `<script id="__NUXT_DATA__">`. There is **no HTML parsing and no browser** in the pipeline:

```
curl_cffi (TLS impersonation)  ->  __NUXT_DATA__  ->  devalue decode  ->  dataclasses  ->  SQLite
```

Src layout, hatchling build, `housemaster` console script via `[project.scripts]`. The dependency graph is acyclic and the layers are kept apart so each is testable alone — **preserve this when adding code**:

```
devalue  <- funda
net      <- funda, media          the one place an HTTP request is made
models   <- everything
funda    <- pipeline
db       <- pipeline, web, cli    no HTTP, no funda imports
media    <- pipeline
pipeline <- cli                   the ONLY network + database module
render   <- cli, web (as filters)
web      <- cli                   never fetches
```

- `devalue.py` — decoder for Nuxt's devalue format: a *flat array* where integers **inside a container** are references into that same array (an integer in the slot a reference lands on is a literal, not a further hop), and a list whose first element is a string is a tagged value. Handles cycles and the `Ref`/`Reactive` wrappers. Knows nothing about funda.
- `models.py` — frozen `Listing` / `Detail` / `Feature` / `SearchPage` / `StoredPhoto`, plus the mutable `FetchReport`. `PAGE_SIZE = 15`.
- `net.py` — `get_text` / `get_bytes`. Every request impersonates Chrome here, so that guarantee is testable in one place.
- `funda.py` — all funda knowledge: `fetch_html`, `extract_state`, `to_listing`, `to_detail`, `fetch_search_page`, `fetch_detail`. Knows nothing about storage or output.
- `db.py` — schema, `MIGRATIONS`, upserts, `Filters` + `query_listings`. Pure SQLite.
- `media.py` — `photo_url`, `download_photo`, format detection. Takes an injectable `fetch` callable, which is the whole testing story for it.
- `pipeline.py` — `run_fetch`. Never prints; takes a `progress` callback.
- `render.py`, `cli.py`, `web/` — output formatting, entry point, Flask app.

Search state lives at `state["pinia"]["search"]` (`listings`, `totalListingsCount`, `criteria`, `aggregations`); detail-page state at `state["data"]["cachedListingData_nl"]`, with neighbourhood stats under a `localInsights-<city>/<hood>` key that must be found by prefix. Pagination is `&search_result=N`, 15 per page.

Errors derive from `FundaError`: `BlockedError` (bot wall) and `PayloadError` (page loaded, shape unexpected). The CLI catches `FundaError` and returns exit 1.

### The trap: blocks return HTTP 200

Akamai's interstitial answers **200** with a ~15 KB page. `response.raise_for_status()` will not catch it. Success is defined as `__NUXT_DATA__` being present in the body — `fetch_html` enforces this and raises `BlockedError`. Never relax that check.

Passing the wall depends only on the TLS/JA3 fingerprint, so every request must go through `curl_cffi` with `impersonate="chrome"`. Plain `requests`/`urllib` with a spoofed User-Agent gets the interstitial. **This applies to the image CDN too** — it answers 403 without impersonation.

### Invariants worth not breaking

- **Every work queue is a SQL predicate**, never a set built during the run: `detail_fetched_at IS NULL`, `local_path IS NULL`. This is what makes an interrupted fetch resumable by just running it again. Do not "optimise" it into an in-memory set of ids seen this run — a crash between the sweep and the detail pass would then lose that house's detail data permanently.
- **Delisting needs two consecutive misses** (`missed_runs >= 2`), and only after a *complete* page walk. Funda pages a live result set, so one absence can be paging jitter rather than a real delisting; and marking after a partial sweep would flag every unread page's houses as gone. `delisted_at` means "stopped matching this search", **not** "sold" — a price rise past the search ceiling triggers it too, so the UI must not say sold.
- **The search payload is the only writer of `price` and `status`.** The detail page carries a price too; letting both write invites a race where the fresher value loses.
- `price_history` has a surrogate key, not `(listing_id, observed_at)` — timestamps are second-granular and one run stamps every row identically, so a composite key silently swallows a second change in the same second.
- Three id spaces on one listing: `globalId` (the primary key, and what search results call `id`), `tinyId` (the number in the detail URL), and a third in `friendlyUrlSlug`. Key on `globalId`; never join on the URL number.
- `price_per_m2` is a **STORED** generated column — virtual ones cannot be indexed, and it is a sort key.

## Photos

The `photo_image_id` in every search result is already a complete CDN path, so photos need **no detail request**: `https://cloud.funda.nl/{photo_image_id}?options=width={W}`. Widths round *up* to 228/464/720/1080/1440, or the 2160 master with no `?options`.

- `Vary: accept` — curl_cffi's Chrome impersonation sends Chrome's Accept header, so without an explicit `Accept: image/jpeg,*/*` the CDN returns **AVIF bytes into a `.jpg`**, silently. `media.ACCEPT` handles it; `media.validate` checks the magic bytes as a backstop.
- Funda genuinely serves some images as **PNG**. The file extension comes from `detect_format(body)`, never from an assumption.
- URLs are content-addressed and immutable, and conditional requests never return 304 — so a cached photo is skipped, never revalidated.
- Downloads write a `.part` then `Path.replace`, so a killed run cannot leave a truncated file that later looks cached.

## The web app

Flask + Jinja + htmx, no build step. htmx and both woff2 fonts are vendored into `web/static/`, so it works offline.

- `/` returns the **full page** normally and the `_results.html` **fragment** when `HX-Request` is set — except on `HX-History-Restore-Request`, which must get the full page or the back button lands on a bare fragment. This is why there is no separate `/partials/...` route: `hx-push-url` would otherwise push a URL that renders a fragment on reload.
- **htmx attributes inherit down the DOM.** The load-more sentinel sits inside the filter form, so it must explicitly override `hx-target`, `hx-select`, `hx-swap` and `hx-push-url` — without them it adopts the form's `#results` target, swaps nothing, and navigates the address bar to `/more`. A test pins this.
- `create_app` resolves both paths to absolute: `send_from_directory` resolves a *relative* root against the Flask package directory, not the cwd, so a relative media root silently 404s every photo.
- The connection is per-request via `flask.g`, opened read-only. WAL lets `serve` read while `fetch` writes.
- Design direction is "Plattegrond" (architectural drafting; mono tabular figures; the real Dutch NEN energy colours). Tokens live at the top of `web/static/app.css`.

## Fetching strategy

The runtime dependency surface is exactly **`curl_cffi` + `flask`** — verified by an AST scan of every import in `src/` and `tests/`. Keep it that way; a new runtime dependency should have to justify itself.

1. **`curl_cffi`** — the whole pipeline, through the single call site in `net.py`. ~0.3 s/page.
2. **`cloakbrowser`** — an *optional extra* (`uv sync --extra browser`), for when Akamai tightens or the payload shape changes. Nothing imports it today. Drop-in Playwright API: `from cloakbrowser import launch` (plus `launch_async`, `launch_context`, `launch_persistent_context`, `*_async`). Useful kwargs: `proxy`, `humanize=True` + `human_preset`, `geoip=True`, `locale`/`timezone`, `stealth_args` (on by default — leave it on).
3. **`trafilatura`** — removed. Funda ships clean prose in `description.content`, so there was nothing to boilerplate-strip.

## Browser MCP (`mcp__cloakbrowser__*`)

[cloakbrowser-mcp](https://github.com/swimmwatch/cloakbrowser-mcp) 1.12.0 bridges `@playwright/mcp` 0.0.79 onto CloakBrowser's patched Chromium. It exposes the 24 upstream Playwright MCP tools plus `cloakbrowser_bridge_info` / `cloakbrowser_binary_info`.

**Headless is not a per-call argument** — no browser tool takes a `headless` parameter, and the CLI rejects `--headless` (the bridge does not forward unknown flags to upstream). It is read from the environment at server start:

| Env var | Default | Effect |
| --- | --- | --- |
| `PLAYWRIGHT_MCP_HEADLESS` | `true` | `false` → visible browser window |
| `CLOAK_PLAYWRIGHT_MCP_HUMANIZE` | `false` | human-like mouse/keyboard/scroll |
| `CLOAK_PLAYWRIGHT_MCP_HUMAN_PRESET` | `default` | `default` \| `careful` |
| `CLOAK_PLAYWRIGHT_MCP_GEOIP_PROXY_MATCH` | `false` | match timezone/locale to proxy GeoIP |
| `CLOAK_PLAYWRIGHT_MCP_RELEASE_CHANNEL` | `stable` | `stable` \| `preview` |

Truthy values are `1`/`true`/`yes`/`on` (case-insensitive); anything else is false.

The user's global config (`~/.claude.json` → `mcpServers.cloakbrowser.env`) is **already set to `PLAYWRIGHT_MCP_HEADLESS=false`** so browser work is visible. Changes there need a session restart.

Practical notes when driving the browser:

- A **Didomi consent overlay** intercepts every click on funda until dismissed ("Alles weigeren" / "Alles accepteren").
- The free CloakBrowser licence allows **one concurrent session** — close the page when done, and never design around parallel browser workers.
- `.playwright-mcp/` at the repo root is the MCP's output directory (snapshots, screenshots), not project source. Tool calls that take a `filename` write to the MCP server's own cwd — which is the repo root, so clean up stray screenshots.

## Other constraints

- Optional CloakBrowser extras are **not** installed: `geoip2`, `aiohttp`, `websockets`. `geoip=True` needs `uv add geoip2` plus a DB download first.
- `mcp__claude-in-chrome__*` may also be available; it drives the user's real Chrome and is not stealth — prefer the cloakbrowser server for funda.
- One request yields 15 fully-detailed listings, so the full Den Haag sweep is ~35 requests. Pace them (`pipeline.DEFAULT_PACE`), and remember detail pages and photos are fetched once per house ever.
- Windows consoles default to cp1252 and mangle `€`/`m²`/Dutch names. `cli._use_utf8_output()` reconfigures the streams; any new entry point needs the same.
- Ruff enables `T20` (no stray `print`) everywhere except `cli.py`. Library code returns data or calls a `progress` callback; the CLI prints. Keep it that way rather than widening the ignore.
