# CLAUDE.md

Guidance for Claude Code working in this repository.

## Commands

`uv` manages the project; Python is pinned to `>=3.14`.

```bash
uv sync
uv run housemaster add <url>      # track a search (koop or huur)
uv run housemaster fetch          # walks EVERY tracked search
uv run housemaster serve
uv run pytest                     # offline; -m network needs HOUSEMASTER_NETWORK_TESTS=1
uv run ruff check . && uv run ruff format .
```

`uv sync` fails on Windows while `serve`/`fetch` is running. Flask caches
templates unless `--debug`; restart `serve` after editing one.

## Architecture

```
devalue  <- funda
net      <- funda                 the one place an HTTP request is made
models   <- everything
funda    <- pipeline
db       <- pipeline, web, cli    no HTTP, no funda imports
photos   <- web                   imports nothing at all
schedule <- cli                   pure date arithmetic
pipeline <- cli                   the ONLY network + database module
render   <- cli, web
web      <- cli                   never fetches
```

Keep these apart. `cli.py` prints; library code returns data or calls a
`progress` callback (ruff `T20` enforces this everywhere but `cli.py`).

Search state lives at `state["pinia"]["search"]`; detail state at
`state["data"]["cachedListingData_nl"]`, with neighbourhood stats under a
`localInsights-<city>/<hood>` key found by prefix. Pagination is
`&search_result=N`, 15 per page.

## Invariants worth not breaking

- **Blocks return HTTP 200.** Success is `__NUXT_DATA__` being present in the
  body, not the status code. Every request needs `curl_cffi` with
  `impersonate="chrome"` — the image CDN too.
- **Every work queue is a SQL predicate**, never a set built during the run
  (`detail_fetched_at IS NULL`, `b.geometry IS NULL`). That is what makes an
  interrupted fetch resumable by running it again.
- **Delisting needs two consecutive misses and a complete sweep of every
  tracked search.** Reconcile against the *union* of everything seen, once, at
  the end. Per-search reconciliation would have each sweep delist every other
  search's houses. `report.complete` gates it; never weaken that gate.
- `delisted_at` means "stopped matching any tracked search", **not** "sold".
- **`price` is euros to buy and euros per month to rent.** Only comparable
  within one `offering_type`, which is why `Filters.offering_type` is mandatory
  and `_where` always emits it. Five helpers bypass `_where` and take it
  explicitly: `price_bounds`, `area_bounds`, `label_counts`,
  `distinct_neighbourhoods`, `counts`.
- **The search payload is the only writer of `price` and `status`.**
- `price_history` has a surrogate key — one run stamps every row with the same
  second, so a composite key would swallow a second change.
- Three id spaces per listing: `globalId` (the primary key), `tinyId` (the
  number in the detail URL), and one in `friendlyUrlSlug`. Key on `globalId`.
- `price_per_m2` is **STORED**, not virtual — virtual columns cannot be indexed.
- **Boundaries are keyed `(city, name)`.** Buurt names repeat across
  municipalities (`Bomenbuurt` in Den Haag and Rijswijk). A buurt slug only
  resolves under its own city.
- Migrations: append to `MIGRATIONS`, never edit an existing one.

## funda quirks

- Slugs delete periods rather than hyphenating: `Koningsplein e.o.` is
  `koningsplein-eo`. Cities follow the same rule: `Rijswijk (ZH)` is
  `rijswijk-zh`.
- `selected_area` takes a comma-separated list. Ypenburg and Leidschenveen *are*
  Den Haag (annexed 2002); Voorburg and Rijswijk are not.
- Default `availability` is `['available', 'negotiations']` — sold and withdrawn
  were never included.
- `localInsights` returns **purchase** €/m² even on a rental page, so the buurt
  choropleth is not a rent benchmark.
- Deep pagination is not capped.

## Photos

`photo_image_id` is a complete CDN path, so photos need no detail request:
`https://cloud.funda.nl/{id}?options=width={W}` (228/464/720/1080/1440, or 2160
with no `?options`).

**Photos are hotlinked, never downloaded.** Migration 002 removed the download
path. It cost ~2 600 requests per first run, and every bug in it — AVIF served
into a `.jpg` via `Vary: accept`, PNGs mixed in, 404s as `text/plain` — existed
only because we were the HTTP client. A browser negotiates correctly on its own.

## The web app

Flask + Jinja + htmx, no build step. htmx and MapLibre load from jsDelivr pinned
by version **and** SRI hash; version and hash are one fact, never bump one
alone. Fonts stay vendored (SRI does not cover `@font-face`).

- `/` returns the full page normally and the `_results.html` fragment when
  `HX-Request` is set — except on `HX-History-Restore-Request`.
- **htmx attributes inherit.** The load-more sentinel sits inside the filter
  form and must override `hx-target`, `hx-select`, `hx-swap`, `hx-push-url`.
- **The rail renders once, on a full page load.** Only `#results` is swapped, so
  anything view-dependent in the rail goes stale — `nav.js` fixes the reset link
  after each swap. Hidden inputs for `view` and `offering` live **inside**
  `#results` for the same reason.
- `[hidden] { display: none !important; }` is load-bearing: the UA rule loses to
  any author `display:`, so `hidden` on a flex element does nothing.
- Filters are remembered per offering type in `localStorage`. The URL stays the
  source of truth: restore only fires on a URL with no filter parameter, saving
  happens on swap and never on arrival, and clearing filters removes the stored
  entry. `views.FILTER_KEYS` is the single definition, rendered as
  `data-filter-keys`.
- Design is "Plattegrond" — drafting, mono tabular figures, real NEN energy
  colours. Light and dark are two halves of one idea, not an inverted palette.
  An inline script applies the stored theme before first paint.
- The favicon is an SVG with an embedded `prefers-color-scheme` block. **XML
  forbids `--` inside comments** — this codebase uses it as a dash everywhere
  else, and an invalid favicon fails silently.

### The map

- **The map instance is created once.** `#map` carries `hx-preserve`; a filter
  change only calls `source.setData()`. Switching to the grid removes the node,
  so `sync()` tears down the dead instance to free its WebGL context.
- **Theme changes rebuild the map rather than restyling it.** `setStyle()`
  discards our layers and MapLibre 5 gives no reliable moment to re-add them.
- `sort` is stripped from the GeoJSON URL: a map has no reading order, and
  leaving it in refetched the whole source on every sort change.
- Bounds come from `db.map_bounds`, not from the GeoJSON.
- **`/neighbourhoods.geojson` is deliberately unfiltered** — outlines are
  geography, not data.
- **Quantile bins, not a linear ramp.** Buurt prices are right-skewed; even
  spacing put 69 of 102 buurten in the bottom two colours.
- Fill alpha differs per theme (0.45 light, 0.32 dark): a bright wash over a
  dark basemap hides more than a dark wash over a pale one.
- Outlines are added **before** the house layers so markers stay on top. The
  toggle lives outside `#map`, so `map.js` rebinds it in `sync()` and restores
  state from `localStorage`; it is disabled server-side when no outlines exist.
- `MAX_MAP_POINTS` caps the payload; `.mapnote` says when the map is showing
  less than the filter count.

## Docker

`Dockerfile` (Alpine, multi-stage) + `compose.yaml` (`web`, `scheduler`, `cli`,
`tunnel`), ~140 MB.

- Alpine works only because `curl_cffi` ships musllinux wheels. `UV_NO_BUILD=1`
  on the dependency layer makes a missing wheel a loud failure; it cannot be
  global, since the project itself is built from source.
- **gunicorn with `--preload`.** Without preload a bad `HOUSEMASTER_DB` is an
  import error inside a worker, which gunicorn answers by respawning forever.
- `wsgi.py` creates the cache file if the volume is new; requests still open
  read-only.
- `scheduler` and `cli` need `entrypoint: [housemaster]` — the image CMD is
  gunicorn and there is no ENTRYPOINT.
- Host port is **8766**, not 8765, to avoid colliding with a local `serve`.
- `schedule.next_run` computes from the local *date*, never by adding 24 hours,
  or the run drifts an hour across a DST change.
- `cli._fetch_lock` stops two fetches overlapping; a killed run's lock expires.
- `tzdata` is a dependency **on Windows only** — Linux and Alpine already have a
  system zone database.

## Other constraints

- Runtime dependencies are `curl_cffi` + `flask` (plus `gunicorn` under the
  `serve` extra). A new one should have to justify itself.
- `cloakbrowser` is an optional extra (`uv sync --extra browser`), the fallback
  if Akamai tightens. Nothing imports it today. The `mcp__cloakbrowser__*` tools
  are a separate npm package and need none of it.
- Windows consoles default to cp1252 and mangle `€`/`m²`; `cli._use_utf8_output`
  handles it, and any new entry point needs the same.
- A funda page yields 15 fully-detailed listings. Pace them
  (`pipeline.DEFAULT_PACE`).
