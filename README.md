# HouseMaster

Automates building an overview of houses matching a client's criteria, by
extracting key information from listings on [funda.nl](https://www.funda.nl)
with no manual steps.

**Target search:**
`https://www.funda.nl/zoeken/koop?selected_area=den-haag&price=250000-350000&floor_area=60-`
(Den Haag, €250–350k, 60 m² and up — 521 results at time of writing.)

## Status

Research phase complete; a first working extractor exists. Funda turns out to be
far more tractable than expected — see [Findings](#findings-how-fundanl-works).

- [x] Map how funda.nl serves listing data
- [x] Confirm which HTTP client gets past the bot wall
- [x] Extract a full search page into structured records
- [ ] Extract detail pages (features, description, neighbourhood insights)
- [ ] Persist results + score against client criteria
- [ ] Export the client-facing overview

## Quick start

```bash
uv sync
uv run housemaster search                     # page 1 of the target search
uv run housemaster search --page 2
uv run housemaster search --url "<any funda search url>"
uv run housemaster search --format json       # or csv
uv run housemaster search --all --max-pages 3 # walk pages, capped
```

Text output per house: address, neighbourhood, price, price/m², living area,
rooms, bedrooms, energy label, agent, publication date, photo count and detail
URL, followed by price/area summary statistics. `--format json` and
`--format csv` emit the same records for downstream processing.

## Development

```bash
uv run pytest                                 # offline; the network test is skipped
uv run ruff check .                           # lint
uv run ruff format .                          # format
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

There is also an **unauthenticated Elasticsearch** endpoint at
`listing-search-wonen.funda.nl/geo-wonen-alias-prod/_search/template`, called
with `{"id":"geo-neighborhoods_20260227","params":{"city":"den-haag"}}`. The
browser only uses it for neighbourhood lookups — listing search happens
server-side — but it is a stored-template endpoint, so other template ids likely
query listings directly. Worth probing if more than 15/page is ever needed.

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

## Project layout

Standard src layout, built with hatchling, installed by `uv sync` as an editable
package exposing the `housemaster` console script.

```
src/housemaster/
  devalue.py     decoder for Nuxt's __NUXT_DATA__ flat-array format
  models.py      Listing / SearchPage records
  funda.py       fetching (with bot-wall detection) + field extraction
  render.py      text / json / csv output
  cli.py         argparse entry point -> `housemaster`
tests/           offline unit tests + one opt-in network canary
```

The layering is deliberate: `devalue` knows nothing about funda, `funda` knows
nothing about output formats, and `render` never touches the network — so each
is testable on its own.

## Tooling

Managed with [uv](https://docs.astral.sh/uv/); Python 3.14.

| Dependency | Role |
| --- | --- |
| `curl_cffi` | HTTP with browser TLS impersonation — the whole pipeline runs on this |
| `cloakbrowser` | stealth Chromium; fallback and investigation only |
| `trafilatura` | text extraction — currently unused, funda ships clean prose |
| `pytest` (dev) | test runner; strict markers/config, offline by default |
| `ruff` (dev) | lint + format; rules configured in `pyproject.toml` |

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
