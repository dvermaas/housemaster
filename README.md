# HouseMaster

Automates building an overview of houses matching a client's criteria, by
extracting key information from listings on [funda.nl](https://www.funda.nl)
with no manual steps.

**Target search:**
`https://www.funda.nl/zoeken/koop?selected_area=den-haag&price=250000-350000&floor_area=60-`
(Den Haag, €250–350k, 60 m² and up — 521 results at time of writing.)

## Status

Working end to end: scrape into a local cache, then browse and filter it.

- [x] Map how funda.nl serves listing data
- [x] Confirm which HTTP client gets past the bot wall
- [x] Extract search pages into structured records
- [x] Extract detail pages (kenmerken, description, coordinates, buurt stats)
- [x] SQLite cache with dedupe and price/status history
- [x] Web UI with filtering
- [x] Map view, filtered live
- [x] Light/dark themes and a full-screen photo viewer
- [ ] Score against explicit client criteria
- [ ] Export the client-facing overview

## Quick start

```bash
uv sync
uv run housemaster fetch            # scrape the target search into ./data
uv run housemaster serve            # browse it at http://127.0.0.1:8765
```

The first `fetch` takes a few minutes (35 search pages plus ~520 detail pages)
and leaves a cache of a couple of megabytes. Every run after that takes about
30 seconds, because a detail page is fetched **once per house, ever**. Photos
are never fetched at all — they are hotlinked from funda's CDN. To try it
without the wait:

```bash
uv run housemaster fetch --max-pages 2 --max-details 5
```

### Commands

```bash
housemaster fetch [--url URL] [--db PATH] [--max-pages N] [--max-details N]
                  [--no-detail] [--no-boundaries] [--pace 0.4] [--quiet]
housemaster serve [--db PATH] [--host 127.0.0.1] [--port 8765] [--debug]
housemaster status [--db PATH]      # what the cache holds, and the last run
housemaster search [...]            # live, no database -- the original probe
```

`search` is unchanged and still hits funda directly, printing text, JSON or CSV.
It is the quick way to check the extraction path without touching the cache.

Data lives in `./data/housemaster.db`, overridable with `--db` or
`$HOUSEMASTER_DB`. That one file is the whole cache — there is no media store
to keep beside it.

### What `fetch` does

1. Walks the search pages, upserting every house it sees. **Price and status are
   refreshed on every run**, and any change appends a row to `price_history` —
   so price drops and "under offer" transitions become visible.
2. Fetches a detail page **only for houses it has never enriched**, adding the
   description, all nine kenmerken groups, coordinates and neighbourhood stats.

3. Fetches the outline of any buurt it does not have one for yet. Boundaries do
   not move, so this drains to nothing after the first run.

Photo ids arrive free with step 1 and are stored as they come; nothing is
downloaded. Steps 2 and 3 are driven by what the *database* lacks, not by what
the run happened to see, so an interrupted fetch is resumed by simply running it
again. There is no checkpoint state and no `--resume` flag.

## Development

```bash
uv run pytest                                 # 231 tests, offline
uv run ruff check . && uv run ruff format .   # lint + format
HOUSEMASTER_NETWORK_TESTS=1 uv run pytest -m network   # canary against the live site
```

The suite is offline by default. One test is marked `network` and hits funda
for real — it is the canary for funda changing its payload shape or Akamai
tightening, and it only runs when `HOUSEMASTER_NETWORK_TESTS` is set.

## Findings: how funda.nl works

The headline: **funda needs neither HTML parsing nor a browser.** It is a
server-rendered Nuxt 3 app that embeds its complete state as JSON in every page.

### Bot protection is the only real obstacle

Funda sits behind **Akamai Bot Manager** (`/akam/13/pixel_*` plus an obfuscated
sensor POST). What gets you through is purely the **TLS/JA3 fingerprint** — no
cookies, no tokens, no JS execution required:

| Client | Result |
| --- | --- |
| `curl_cffi` with `impersonate="chrome"` | **200, 393 KB — the real page** |
| `curl_cffi` without impersonation | 200, 15 KB — Akamai interstitial |
| stdlib `urllib` + Chrome UA header | 200, 15 KB — Akamai interstitial |

> **The soft block returns HTTP 200**, titled *"Je bent bijna op de pagina die je
> zoekt"*. The status code proves nothing — always assert that `__NUXT_DATA__` is
> present in the body. `housemaster.funda.fetch_html` raises `BlockedError` when
> it is not.

This is the fragile seam of the whole approach: it works today, and it is what
would break first if Akamai tightens.

### Search pages

`<script id="__NUXT_DATA__">` carries ~107 KB in Nuxt's **devalue** format — a
flat array where integers are references into that same array. It needs a small
decoder (`housemaster/devalue.py`), not an HTML parser. Under `pinia.search`:

- **`listings[15]`** — price, floor area, rooms/bedrooms, energy label, object
  type, construction type, publish date, status, full address (street, postal
  code, neighbourhood, wijk, municipality), agent, every photo id, detail URL
- **`totalListingsCount`** — 521 for the target search
- **`criteria`** — the full filter vocabulary (~45 filters)
- **`aggregations`** — facet counts for every filter value, so the market can be
  profiled without fetching a single listing

Pagination is `&search_result=N`, 15 per page. Page 35 returned 11 items, so
34×15+11 = 521, exactly matching `totalListingsCount`. At **~0.3 s per page**,
the entire result set is ~35 requests, roughly 10 seconds.

A `<script type="application/ld+json">` `ItemList` also carries the 15 detail
URLs — a cheap fallback if the devalue format ever shifts.

### Clicking a house is a full page load

Funda runs **separate Nuxt apps per section** (assets move from
`nuxt.fstatic.nl/…/zoeken/` to `…/detail/`), so a click is a real document
navigation, not an SPA route. There is no client-side JSON route to intercept —
just fetch the detail URL directly.

The detail page's `__NUXT_DATA__` (~20 KB) holds everything in
`data.cachedListingData_nl`:

- Full `description.content` as clean prose — **trafilatura is unnecessary**
- `features[9]` — the *kenmerken* tables as structured `{Id, Label, Value}`
  groups: Overdracht, Bouw, Oppervlakten, Indeling, Energie, Kadastrale gegevens,
  Buitenruimte, Parkeergelegenheid, VvE checklist (includes VvE contribution,
  price per m², acceptance date)
- `coordinates` (lat/lng), `media` (photos, floor plans, 360s)
- `objectInsights` (views/saves)
- `localInsights-<city>/<neighbourhood>` — inhabitants, % families with children,
  **average asking price per m² for the neighbourhood**

### Internal JSON APIs

All open, no auth, all reachable with `curl_cffi`. Keyed by internal `globalId`,
**not** the number in the URL:

```
GET https://listing-detail-summary.funda.io/api/v1/listing/nl/{globalId}
GET https://www.funda.nl/objectenhancements/enhancements/?GlobalId={globalId}
GET https://contacts-flows-bff.funda.io/api/v1/contacts-flows/listings/{globalId}/contact-block
```

Mind the **three id spaces** on one listing: `globalId` (8116828), `tinyId` / URL
id (44561281), and a third id in `friendlyUrlSlug` (17298554).

These are mostly redundant with the SSR payload, so the pipeline skips them.

### How funda's own map works

Worth knowing, because it validates this project's architecture. Funda's map
view (`/zoeken/kaart/koop`) runs **Google Maps JS API v3.66.1a**, but Google
supplies *only the basemap tiles* — every pin is funda's own data, fetched from
funda's backend and painted by funda's code on a canvas overlay (2 canvases,
zero DOM markers). Google never sees a listing.

The pin data comes from an **Elasticsearch** call made straight from the
browser:

```
POST https://listing-search-wonen.funda.nl/_msearch/template
{"index": "listings-wonen-searcher-alias-prod"}
{"id": "map_result_20260227", "params": {
   ...the same ~45 filters as the list search...,
   "map_result": {"top_left_lat": …, "bottom_right_lng": …,
                  "precision": 15, "number_of_globalids": 13,
                  "include_listing_details": false}}}
```

Elasticsearch does the clustering server-side with a `geotile_grid`
aggregation, returning ~500 buckets keyed `"15/16809/10822"` (zoom/x/y), each
with a `doc_count`, a centroid, and up to 13 listing ids. With 89,630 listings
matching nationally, they never ship individual pins — the orange bubbles *are*
the bucket counts.

**These XHR endpoints are gated more tightly than the HTML pages.** `curl_cffi`
gets `403 Access Denied` even with byte-identical headers: they need Akamai Bot
Manager cookies (`bm_sv`, `_abck`) that a browser earns by executing Akamai's
sensor JS, which TLS impersonation alone cannot supply. Not a problem here — we
already hold every listing locally, with coordinates.

## What this means for the design

The original plan assumed Playwright/CloakBrowser would drive the extraction.
Based on the above, **the browser tier is dead weight for the main pipeline**:
`curl_cffi` plus a devalue decoder yields richer data than scraping the rendered
DOM, at ~0.3 s per request instead of seconds per page.

CloakBrowser stays as the fallback for when Akamai tightens, and as the tool for
re-investigating the site when something changes.

Two gotchas for the browser tier when it is used:

- A **Didomi consent overlay** intercepts every click until dismissed.
- The free CloakBrowser licence allows **one concurrent browser session**, so any
  parallelism belongs in the `curl_cffi` tier.

Since one request yields 15 fully-detailed listings, the whole Den Haag overview
is ~35 polite requests. Keep the pacing modest, and cache fetched HTML while
iterating on extractors.

## The photo CDN

Photos need no detail request: the `photo_image_id` already in every search
result is a complete CDN path.

```
https://cloud.funda.nl/{photo_image_id}?options=width={W}
```

Widths round **up** to a fixed ladder — 228, 464, 720, 1080, 1440, and the
2160px master when `?options` is omitted. At width 720 a photo is 30–60 KB.

**Photos are hotlinked, never downloaded.** The browser requests them straight
from funda, which is why `photos.py` is thirty lines with no imports: it builds
a URL and that is all.

This was a deliberate reversal. An earlier version cached the first five photos
of every house to disk, and the case for it was archival — being able to look
back at a house that had since sold. What it actually cost was the largest part
of a first run (~2 600 requests), 40 MB of disk, and a whole class of bugs that
only exist when *we* are the HTTP client:

- `Vary: accept` — curl_cffi's Chrome impersonation sends Chrome's own Accept
  header, so without an override you get **AVIF bytes in a `.jpg` file**.
- **TLS impersonation is required for the CDN too** — plain requests get 403.
- A 404 comes back as `text/plain`, so the status code alone proves nothing,
  and funda genuinely mixes **PNG** in among the JPEGs, so the file extension
  had to be derived from magic bytes rather than assumed.

A browser negotiates content correctly on its own, so hotlinking deletes all
four problems at once. The URLs are content-addressed and immutable
(`Cache-Control: max-age=31536000, immutable`), so the browser caches them for a
year and the second visit is free.

What it gives up is the archive: if funda ever garbage-collects media for a
sold listing, that house's photos are gone. Whether it does is
[still untested](FINDINGS.md). The ids are still stored, so nothing stops a
future archival pass from being added back for the houses that matter.

## Project layout

Standard src layout, built with hatchling, installed by `uv sync` as an editable
package exposing the `housemaster` console script.

```
src/housemaster/
  devalue.py     decoder for Nuxt's __NUXT_DATA__ flat-array format
  models.py      Listing / Detail / Feature / SearchPage records
  net.py         the one place an HTTP request is made (TLS impersonation)
  funda.py       search + detail extraction, with bot-wall detection
  db.py          SQLite cache: schema, migrations, upserts, queries
  photos.py      CDN photo URLs -- a pure builder, importing nothing
  pipeline.py    the fetch orchestration -- the only network+database module
  render.py      text / json / csv output
  cli.py         argparse entry point -> `housemaster`
  web/           Flask app: views, filters, templates, map.js, fonts
tests/           offline unit tests + one opt-in network canary
```

The layering is deliberate and worth preserving: `devalue` knows nothing about
funda, `funda` knows nothing about storage, `db` knows nothing about HTTP,
`render` and `web` never fetch, and only `cli.py` prints. `pipeline.py` is the
single module allowed to touch both the network and the database.

## The web UI

Flask + Jinja + htmx. No build step, no bundler, no JavaScript framework.

htmx and MapLibre load from **jsDelivr, pinned by exact version and by
Subresource Integrity**. The browser hashes each file as it arrives and refuses
to execute it unless the digest matches, so a compromised CDN cannot quietly
swap in different code — the hashes in `base.html` were computed from the exact
bytes that used to be vendored in the repository. Version and hash are one
fact: never bump one without recomputing the other.

```bash
curl -sL <url> | openssl dgst -sha384 -binary | openssl base64 -A
```

The two webfonts stay vendored in `web/static/fonts/`. SRI does not cover
`@font-face`, so a CDN font would be the one unverifiable request on the page,
and glyph data is not executable anyway. Our own CSS and JS are served locally
for the same reason there is no build step: they are the app.

The filter rail GETs back to `/`, which returns the results fragment when htmx
asks for it and the full page otherwise. That keeps the pushed URL shareable —
reloading or bookmarking a filtered view renders properly instead of showing a
bare fragment. Without JavaScript the same form still works as a plain GET.

### The map

The **Map** toggle beside the sort control swaps the card grid for a map of the
same filtered set — the filter rail stays put, so narrowing the price range
makes markers disappear in place. Markers are coloured by the NEN energy scale;
clicking one opens a card with the photo, address, price and €/m².

Basemap tiles come from [OpenFreeMap](https://openfreemap.org/) — **no API key,
no account, no usage limits**, attribution rendered automatically. Google and
Apple were both ruled out: Apple's MapKit JS needs a $99/year developer
membership, Google's free Embed API can only show a *single* place, and its
JavaScript API demands a billing account with a card on file.

Two implementation details that are load-bearing:

- The MapLibre instance is created **once** and kept alive. The map node carries
  `hx-preserve`, and a filter change only pushes a new GeoJSON URL into the
  existing source — so the viewport you panned to survives. Switching to the
  grid genuinely removes the node, so `map.js` detects the detached container
  and rebuilds (also freeing the WebGL context).
- The view is a hidden field **inside** `#results`, not in the rail. The filter
  form serialises everything it contains, and it is reserialised on every swap;
  without it, changing a filter on the map silently drops you back to the grid.

The map's tiles cannot be vendored the way the fonts are: the OSM tile policy
forbids pre-downloading them.

### The buurt overlay

**Buurten** at the bottom left draws all 105 Den Haag neighbourhoods as a
translucent choropleth, shaded by funda's own average asking price per m² for
that buurt. Hovering one names it and gives its price level; the fill stays
see-through so streets, water and the house markers all read straight through
it. The choice is remembered between visits.

It answers the question the €/m² column cannot: *is this house cheap, or is it
just in a cheap part of town?* Laakkwartier at €3 675/m² and Vogelwijk at
€6 504/m² are a 1.8× difference in the same city.

Two decisions worth knowing about:

- **The outlines ignore the filter rail.** They are geography, not data —
  buurten that vanished as you moved a price slider would read as a bug.
- **The colour steps are quantiles, not an even split of the range.** Buurt
  prices are strongly right-skewed, so even spacing put 69 of 102 buurten in the
  bottom two colours and drew as one flat wash. Equal-count bins put about a
  fifth of the city in each step, which is what makes the map legible at all.

funda hands the outlines over itself: a search scoped to
`selected_area=den-haag/<buurt>` echoes the resolved area back with its polygon.
`fetch` collects them once — they never move — and skips the pass thereafter.

### Themes and the photo viewer

The toggle at top right switches **light and dark**, and the two are the same
idea rather than an inverted palette: a real blueprint is light lines on dark
ground, so light mode is drafting vellum and dark mode is cyanotype. The map's
basemap follows. A choice is remembered; with no choice made the app follows
your operating system, and a small inline script applies it before first paint
so the wrong theme never flashes.

Clicking any photo on a house page opens it **full screen** rather than in a new
tab — arrow keys or the on-screen buttons move through the set, a counter shows
where you are, and Escape, the ✕ or a click on the backdrop dismisses it. The
gallery links keep their `href`, so with JavaScript off they still open the
photo directly.

Design direction is "Plattegrond": architectural drafting, hairline rules, and
every figure set in a monospace face with tabular numerals so prices and €/m²
align into columns you can scan straight down a grid of 500 houses. Energy
labels use the real Dutch NEN colour scale, and each card carries a small scale
bar under its area figure showing that house's size within the search's range.

## Tooling

Managed with [uv](https://docs.astral.sh/uv/); Python 3.14.

The runtime surface is deliberately two packages. An AST scan of every import in
`src/` and `tests/` confirms nothing else is reached:

| Dependency | Role |
| --- | --- |
| `curl_cffi` | TLS-impersonating HTTP — **one import, in `net.py`**, and the whole pipeline runs through it |
| `flask` | the `serve` web app (Jinja comes with it; htmx and MapLibre come from a CDN, pinned by SRI) |
| `pytest` (dev) | test runner; strict markers/config, offline by default |
| `ruff` (dev) | lint + format; rules configured in `pyproject.toml` |

**`cloakbrowser` is an optional extra**, not a default dependency:

```bash
uv sync --extra browser     # only when you need the Python stealth-browser fallback
```

No shipped code imports it, and it costs ~110 MB (playwright) plus a ~200 MB
Chromium binary. It stays declared because it is the deliberate escape hatch for
when Akamai tightens or the payload shape changes. The browser MCP below is
unaffected either way — it depends on a *separate npm package* of the same name
and manages its own Chromium in `~/.cloakbrowser/`.

**`trafilatura` was removed.** Funda ships clean prose in `description.content`,
so there was nothing to boilerplate-strip; it was 47 MB (31 MB of it `babel`)
for zero imports. Dropping both took the venv from ~205 MB to 48 MB.

### Browser automation via MCP

We drive the browser through [cloakbrowser-mcp](https://github.com/swimmwatch/cloakbrowser-mcp),
a bridge over [playwright-mcp](https://github.com/microsoft/playwright-mcp)
(v1.12.0 wraps `@playwright/mcp` 0.0.79) backed by CloakBrowser's patched
Chromium.

The bridge defaults to **headless**. To watch the browser work, set
`PLAYWRIGHT_MCP_HEADLESS=false` in the server's `env` block (already applied to
this machine's user config):

```json
"cloakbrowser": {
  "type": "stdio",
  "command": "npx",
  "args": ["-y", "cloakbrowser-mcp@1.12.0"],
  "env": { "PLAYWRIGHT_MCP_HEADLESS": "false" }
}
```

Restart the session for the change to take effect. See `CLAUDE.md` for the other
bridge options.
