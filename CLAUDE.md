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

uv run housemaster add <url>           # track a funda search (koop or huur)
uv run housemaster rm <id>             # stop tracking one (ids come from `status`)
uv run housemaster fetch               # scrape EVERY tracked search into ./data
uv run housemaster fetch --max-pages 2 --max-details 5
uv run housemaster fetch --url <url>   # ad-hoc, bypasses the tracked set
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
net      <- funda                 the one place an HTTP request is made
models   <- everything
funda    <- pipeline
db       <- pipeline, web, cli    no HTTP, no funda imports
photos   <- web                   imports nothing at all; a URL builder
pipeline <- cli                   the ONLY network + database module
render   <- cli, web (as filters)
web      <- cli                   never fetches
```

- `devalue.py` — decoder for Nuxt's devalue format: a *flat array* where integers **inside a container** are references into that same array (an integer in the slot a reference lands on is a literal, not a further hop), and a list whose first element is a string is a tagged value. Handles cycles and the `Ref`/`Reactive` wrappers. Knows nothing about funda.
- `models.py` — frozen `Listing` / `Detail` / `Feature` / `SearchPage`, plus the mutable `FetchReport`. `PAGE_SIZE = 15`.
- `net.py` — `get_text`, and nothing else. Every request impersonates Chrome here, so that guarantee is testable in one place.
- `funda.py` — all funda knowledge: `fetch_html`, `extract_state`, `to_listing`, `to_detail`, `fetch_search_page`, `fetch_detail`, plus `neighbourhood_slug` / `fetch_boundary` for buurt outlines. Knows nothing about storage or output.
- `db.py` — schema, `MIGRATIONS`, upserts, `Filters` + `query_listings`. Pure SQLite.
- `photos.py` — `photo_url` and the CDN width ladder. **Zero imports**, pinned by a test: the web app depends on it, so it must stay unable to reach anything.
- `pipeline.py` — `run_fetch`: sweep **every tracked search**, then `_enrich` (detail pages), then `_outline` (buurt boundaries). Never prints; takes a `progress` callback.
- `render.py`, `cli.py`, `web/` — output formatting, entry point, Flask app.

Search state lives at `state["pinia"]["search"]` (`listings`, `totalListingsCount`, `criteria`, `aggregations`); detail-page state at `state["data"]["cachedListingData_nl"]`, with neighbourhood stats under a `localInsights-<city>/<hood>` key that must be found by prefix. Pagination is `&search_result=N`, 15 per page.

Errors derive from `FundaError`: `BlockedError` (bot wall) and `PayloadError` (page loaded, shape unexpected). The CLI catches `FundaError` and returns exit 1.

### Buy and rent live in one table

`offering_type` (migration 005) is `buy` or `rent`. Only the price field differs between a rental and a purchase payload — address, features, photos, coordinates and every child table are identical — so this is one discriminator column, **not** a second table and not a second database. Keeping them together is what makes a rent-to-buy comparison per buurt a `GROUP BY` instead of a cross-file join.

**`price` means euros to buy and euros *per month* to rent**, with the unit carried by `price_condition` (`kosten_koper` / `per_month`). `price_per_m2` follows — €/m² purchase versus €/m² per month, both meaningful in their own right.

- **They are only comparable within one `offering_type`.** `Filters.offering_type` is therefore mandatory and `_where` always emits it, which scopes `query_listings`, `count_listings`, `query_map_points`, `count_map_points` and `map_bounds` for free. Five helpers do not go through `_where` and take it explicitly: `price_bounds`, `area_bounds`, `label_counts`, `distinct_neighbourhoods`, `counts` (which alone accepts `None`, for `status`).
- **A listing's offering type comes from its payload** (`offering_type: ['rent']`), never from the search URL. The same property can be listed both ways as two listings with two globalIds. The `searches` column is a label for `status` only.
- **Rent is normalised to monthly at extraction.** About one rental in sixty is quoted `per_year` (they are parking spaces); leaving both scales in one column would make every comparison silently wrong. funda's own wording survives verbatim in the kenmerken.
- **The UI never mixes them** — a Buy/Rent switch in `.results-head`, and `offering` is a hidden input **inside `#results`** for the same reason `view` is.
- **Switching mode drops `price_min`/`price_max`.** A 0–1800 rent range is meaningless for buying. Every other filter survives.
- Reconciliation deliberately spans both: a run may sweep buy and rent searches, and presence is a property of the union.
- The buurt choropleth is a **purchase** €/m² map — `localInsights` returns purchase prices even on a rental detail page. It is geography under a rental view, not a rent benchmark.

### Tracked searches

`searches` (migration 004) holds the set of funda search URLs the cache follows. `fetch` with no `--url` walks all of them; `--url` is an ad-hoc override that is *not* saved. `add` does one live request to confirm the URL resolves and report what it holds — `--no-check` skips it. `rm <id>` stops tracking but **keeps the houses**: they delist through the normal two-strike reconciliation, which is what `delisted_at` has always meant ("stopped matching any tracked search", not "sold"). Deleting listings on `rm` would throw away price history for houses that may still be live.

`status` is also the listing — `rm` takes the ids it prints.

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
- **Boundaries are keyed on `(city, name)`** (migration 006), because buurt names repeat across municipalities — there is a `Bomenbuurt` in both Den Haag and Rijswijk, and a `Kleurenbuurt` in both Rijswijk and Voorburg. Keyed on the name alone, the second fetch overwrites the first and the choropleth draws one city's outline over another city's houses. Both halves of the slug come from `neighbourhood_slug`: funda's city identifiers follow the same rule, so `Rijswijk (ZH)` is `rijswijk-zh`. **A buurt only resolves under its own city** — `den-haag/cromvliet` answers with the unfiltered search rather than a neighbourhood, which is why `fetch_boundary` takes the city and every boundary join matches on it.
- **Delisting reconciles against the UNION of every tracked search**, never per search. `run_fetch` accumulates one `seen` set across all of them and reconciles once at the end. Reconciling inside the per-search loop would have each sweep delist every *other* search's houses — a silent, total data-corruption bug. `report.complete` likewise means "every page of every search was walked"; one capped or blocked search makes the whole run partial.
- **Nothing records which search found a house.** Presence is a property of the union, which is exactly what reconciliation needs; anything finer would have to be maintained per search and would make "gone" ambiguous.

## Photos

The `photo_image_id` in every search result is already a complete CDN path, so photos need **no detail request**: `https://cloud.funda.nl/{photo_image_id}?options=width={W}`. Widths round *up* to 228/464/720/1080/1440, or the 2160 master with no `?options`.

**Photos are hotlinked, never downloaded — do not add a download path back without a reason that survives the argument below.** The pipeline used to cache five photos per house; migration 002 dropped the columns that described a file on disk. Removing it deleted the longest part of a first run (~2 600 requests), 40 MB of disk, and every bug in this list, because a browser negotiates content correctly on its own where a scripted client does not:

- `Vary: accept` — curl_cffi's Chrome impersonation sends Chrome's Accept header, so without an explicit `Accept: image/jpeg,*/*` the CDN returned **AVIF bytes into a `.jpg`**, silently.
- Funda genuinely serves some images as **PNG**, so a file extension could never be assumed.
- The CDN answers 403 without TLS impersonation, and reports a 404 as `text/plain`.

What is given up is the archive, if funda ever garbage-collects media for a sold listing — untested, and recorded as an open question in `FINDINGS.md`. The image ids are still stored, so an archival pass could be added back later for the houses that matter.

The templates ask for 464 on cards and 720/1440 on the detail page, and set `referrerpolicy="no-referrer"` — verified in a real browser to load fine.

## The web app

Flask + Jinja + htmx, no build step.

**htmx and MapLibre load from jsDelivr, pinned by version *and* SRI hash** (`base.html`). The browser refuses to execute a file whose digest does not match, so a compromised CDN cannot swap in different code. The hashes were computed from the exact bytes previously vendored here. **Version and hash are one fact — never bump one without recomputing the other:**

```bash
curl -sL <url> | openssl dgst -sha384 -binary | openssl base64 -A
```

Both woff2 fonts stay vendored in `web/static/fonts/`: SRI does not cover `@font-face`, so a CDN font would be the one unverifiable request on the page, and glyph data is not executable. Our own CSS and JS stay local too.

- `/` returns the **full page** normally and the `_results.html` **fragment** when `HX-Request` is set — except on `HX-History-Restore-Request`, which must get the full page or the back button lands on a bare fragment. This is why there is no separate `/partials/...` route: `hx-push-url` would otherwise push a URL that renders a fragment on reload.
- **htmx attributes inherit down the DOM.** The load-more sentinel sits inside the filter form, so it must explicitly override `hx-target`, `hx-select`, `hx-swap` and `hx-push-url` — without them it adopts the form's `#results` target, swaps nothing, and navigates the address bar to `/more`. A test pins this.
- `create_app` resolves the database path to absolute: Flask resolves a *relative* path against the package directory, not the cwd, so `data/housemaster.db` would be looked for in the wrong place entirely.
- The connection is per-request via `flask.g`, opened read-only. WAL lets `serve` read while `fetch` writes.
- Design direction is "Plattegrond" (architectural drafting; mono tabular figures; the real Dutch NEN energy colours). Tokens live at the top of `web/static/app.css`.
- **Light and dark are two halves of one idea**, not an inverted palette: a real blueprint is light lines on dark ground, so light mode is drafting vellum and dark mode is cyanotype. Three states — an explicit choice sets `data-theme` on `<html>` and wins; no attribute means the CSS follows `prefers-color-scheme`. An inline script in `<head>` applies a stored choice **before first paint**, or the wrong theme flashes. `--basemap` is a CSS token so the light/dark map style mapping lives in the stylesheet, not in JS.
- The photo viewer is always light-on-dark whatever the page theme: a photo wants a dark surround. Gallery links keep their `href` as the no-JS fallback; `lightbox.js` only intercepts the click.

### The map view (`?view=map`)

Swaps the card grid for a MapLibre map of the same filtered set; the rail is shared. Basemap is [OpenFreeMap](https://openfreemap.org/) — no key, no account, no limits, attribution added by MapLibre itself. Markers come from `/houses.geojson` (same filters); clicking one fetches `/house/<id>/card`, so the popup markup is Jinja like every other card rather than HTML assembled in JS.

Three things that will break if disturbed:

- **The map instance is created once.** `#map` carries `hx-preserve` so a filter change only calls `source.setData(newUrl)` — rebuilding it on every swap would throw away the viewport the user panned to. Switching to the grid *does* remove the node (hx-preserve needs it in both old and new markup), so `sync()` calls `dropIfDetached()` to tear down the dead instance and free its WebGL context before making a new one.
- **Filters are remembered per offering type** in `localStorage`, keyed `housemaster-filters-v1:{buy,rent}`. Three rules make it safe: the **URL stays the source of truth**, so a restore only fires on a URL carrying *no* filter parameter and a shared link always wins; saving happens on `htmx:afterSwap` only, never on arrival, so opening someone else's link cannot overwrite your own set; and **"Clear all filters" removes the stored entry**, or the next bare URL puts them straight back and the button looks broken. The restore runs **pre-paint** in an inline `{% block head %}` script with `location.replace` — rendering 3 000 houses and then swapping would flash, and `assign` would leave a history entry that Back bounces off. A loop is impossible because the replacement URL always carries a filter.
- **`views.FILTER_KEYS` is the single definition of "what is a filter"**, rendered as `data-filter-keys` for `nav.js` rather than restated in JS. `view`, `offering` and `page` are deliberately absent — they are properties of the page, and storing `page` would restore someone to page 5 of a search they left. Two tests pin it: every key must actually change `filters_from_args`, and the count must match `db.Filters`' field count.
- **The rail is rendered once, on a full page load.** Only `#results` is swapped, so anything in the rail that depends on the current view goes stale after a view switch. `reset_url` is rendered server-side (correct on load and without JS) *and* corrected by `nav.js` on `htmx:afterSwap` — otherwise "Clear all filters" on the map drops you back to the grid. Clearing filters keeps the view: it is a property of the page, not of the filter set.
- **`<input type="hidden" name="view">` lives inside `#results`, not the rail.** The filter form serialises its whole subtree and `#results` is reserialised on every swap; move it to the rail and it goes stale, and filtering on the map drops you back to the grid.
- **Bounds come from `db.map_bounds`, not from the GeoJSON.** Fitting the view by downloading the feature collection a second time both wasted a request and raced the source load.

**Theme changes rebuild the map rather than restyling it.** `setStyle()` is the obvious call and it is a trap: it discards our layers, and there is no reliable moment to put them back — MapLibre 5 does not emit `style.load`, and `styledata` also fires for the *outgoing* style, so the re-add lands on a style about to be thrown away and the houses silently vanish. `retheme()` tears the map down and rebuilds it at the same camera, reusing the first-paint code path. Theme switching is rare and deliberate; a subtler mechanism is not worth the failure mode.

`MAX_MAP_POINTS` caps the GeoJSON so a pathological filter cannot ship an unbounded payload. The map's tiles are the one thing that cannot be vendored — OSM's tile policy forbids pre-downloading them.

### The buurt overlay

A toggleable choropleth of the 105 Den Haag neighbourhoods, shaded by funda's own `neighbourhood_price_m2` for that buurt. Four things that are easy to get wrong:

- **`/neighbourhoods.geojson` is deliberately unfiltered.** The outlines are geography, not data. Making them respond to the filter rail would have buurten blink out as you move a price slider, which reads as a bug. It is also why the map never reloads that source.
- **Quantile bins, not a linear ramp** (`db.neighbourhood_price_scale`). Den Haag's buurt prices are strongly right-skewed — 3 533 to 7 641 with the mass under 5 500 — so even spacing put 69 of 102 buurten in the bottom two colours and drew as one flat wash. Equal-count edges put ~20 in each. The JS uses a `step` expression over those edges; the legend bar uses hard stops for the same reason.
- **The fill's alpha differs per theme** (`--choro-fill`: 0.45 light, 0.32 dark). A translucent fill composites toward the basemap, so a bright wash over a dark basemap hides far more than a dark wash over a pale one. The ramp itself spans a wide lightness range because alpha compresses the visible spread to less than half of it.
- **Outlines are added *before* the house layers** so markers always sit on top, and the toggle lives **outside** `#map`, so htmx replaces the button on every swap — `map.js` rebinds it in `sync()` and restores state from `localStorage`, exactly like the theme.
- The toggle is **disabled server-side when no outlines are cached** (`fetch` collects them in its last pass, so a fresh cache has none for a while). `sync()` honours `disabled` over the stored preference, or a remembered "on" would turn on an empty layer.

### What the map admits it is not showing

Two ways the map legitimately draws less than the filter count, both surfaced in `.mapnote` rather than left silent:

- **Coordinates arrive only with the detail page.** Mid-enrichment the map can plot a fraction of its houses; `db.count_map_points` vs `db.count_listings` is the comparison.
- `MAX_MAP_POINTS` caps the payload, so a pathological filter cannot ship an unbounded one.

**`sort` is stripped from the map's GeoJSON URL** alongside `page` and `view`. A map has no reading order, and leaving it in changed the URL on every sort change, which made MapLibre refetch and redraw the whole source for nothing — during an active fetch that read as houses randomly appearing and vanishing. The sort control itself is `hidden` in map view for the same reason; the choice still round-trips in the URL so the grid keeps it.

**`[hidden] { display: none !important; }` is load-bearing.** The UA rule for `[hidden]` is a plain `display: none`, which any author `display: flex` beats — so `hidden` on the sort control and the ramp legend did nothing while `element.hidden` still reported `true`. Every toggle in the map chrome relies on the attribute; a test pins the rule.

The outline pass (`pipeline._outline`) is backlog-driven like every other queue: it drains to nothing after one run and costs zero requests thereafter, because boundaries do not move.

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

## Docker

`Dockerfile` (Alpine, multi-stage) + `compose.yaml` (`web`, `scheduler`, `cli`, `tunnel`). ~140 MB.

- **Alpine only works because `curl_cffi` ships musllinux wheels for cp314.** Without them the build would compile curl-impersonate from source. `UV_NO_BUILD=1` is set **on the dependency layer only** so a missing wheel fails loudly; the project itself has no wheel and is built in the next layer, which is why the guard cannot be global.
- **gunicorn, not `housemaster serve`.** Werkzeug's server is a development server and would be facing the internet. It is an optional extra (`--extra serve`) so the core runtime surface stays `curl_cffi` + `flask`. Entry point is `housemaster.web.wsgi:app`, which reads `$HOUSEMASTER_DB` and **raises at import** if the cache is missing.
- **`--preload` is load-bearing.** Without it that import error happens inside a worker, and gunicorn answers by respawning forever — the container never exits and just spins. Nothing is opened at import (connections are per-request via `flask.g`), so preloading costs nothing.
- **`wsgi.py` creates the cache file if the volume is new**, which is the web app's only write and happens once at start-up; requests still open read-only. Without it a first `up` crash-loops on a database that is not there.
- **The host port is 8766**, deliberately not 8765 — that is `housemaster serve`'s own default, and a dev server on the host makes compose fail with "ports are not available". The tunnel does not use the published port at all.
- `HOUSEMASTER_DB` is set in the Dockerfile, so compose does not repeat it.
- **`scheduler` and `cli` need `entrypoint: [housemaster]`** — the image's CMD is gunicorn and there is no ENTRYPOINT, so a bare `command: [schedule]` looks for an executable called `schedule`.

### The daily fetch

`housemaster schedule` sleeps until the next `--at` in `--tz`, fetches, repeats.

- **`schedule.next_run` computes from the local *date*, never by adding 24 hours.** Adding a day drifts the run an hour across a daylight-saving change, which nobody notices until the March run lands at 05:00. Tested against both clock-change nights.
- The wait is recomputed from the clock each cycle rather than counted down, so a suspended machine or a slow fetch cannot make the schedule drift.
- **A failed fetch is logged, never fatal** — a nightly job has to still be there tomorrow.
- **`cli._fetch_lock` stops two fetches overlapping.** They would not corrupt anything (SQLite serialises writes) but they contend for the writer lock until one gives up mid-run, and ask funda for the same pages twice. A killed run's lock expires after `STALE_LOCK_AFTER` rather than wedging the schedule forever.
- **`tzdata` is a dependency on Windows only** (`sys_platform == 'win32'`). Windows ships no system time-zone database, so `zoneinfo` cannot resolve `Europe/Amsterdam`; Linux and the Alpine image already have one, so the container's runtime surface is still exactly curl_cffi + flask.
- **`cloudflared` dials out**; no port is published to the internet. Its Public Hostname points at `http://web:8765` over the compose network. The token lives in `.env`, which is gitignored.
- `data/` is in `.dockerignore` — it is a volume, and baking tens of MB that change every fetch would bust the layer cache on every build.

## Other constraints

- Optional CloakBrowser extras are **not** installed: `geoip2`, `aiohttp`, `websockets`. `geoip=True` needs `uv add geoip2` plus a DB download first.
- `mcp__claude-in-chrome__*` may also be available; it drives the user's real Chrome and is not stealth — prefer the cloakbrowser server for funda.
- One request yields 15 fully-detailed listings. Pace them (`pipeline.DEFAULT_PACE`), and remember a detail page is fetched once per house ever, a buurt outline once per buurt ever, and photos cost no requests at all.
- **funda's slug rule deletes periods rather than hyphenating them**: `Koningsplein e.o.` is `koningsplein-eo`. Three Den Haag buurten are named `... e.o.` and all three fail under a naive slugify.
- **`selected_area` takes a comma-separated list**: `den-haag,rijswijk-zh,voorburg` resolves to three `city` areas in one search. Ypenburg and Leidschenveen (postcodes 2492–2498) *are* Den Haag — annexed in 2002 — while Voorburg and Rijswijk are separate municipalities, which is why the map's eastern lobe is detached.
- Windows consoles default to cp1252 and mangle `€`/`m²`/Dutch names. `cli._use_utf8_output()` reconfigures the streams; any new entry point needs the same.
- Ruff enables `T20` (no stray `print`) everywhere except `cli.py`. Library code returns data or calls a `progress` callback; the CLI prints. Keep it that way rather than widening the ignore.
