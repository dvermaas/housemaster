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
uv run mypy                       # strict, over src and tests
uv run python -m housemaster.web.typescript frontend/src/lib/api.gen.ts

cd frontend                       # React SPA; npm, not pnpm
npm run dev                       # :5173, proxies /api to `serve` on :8765
npm run lint && npm test && npm run build
```

**After changing anything in `frontend/`, run `npm run lint` and fix every
error.** It is Oxlint plus `@shadcn/lint`, and its messages say what to use
instead. `npm run build` writes `src/housemaster/web/dist/`, which `serve`
reads; it is gitignored and shipped in the wheel via hatch `artifacts`.

`uv sync` fails on Windows while `serve`/`fetch` is running.

## Architecture

```
devalue  <- funda
net      <- funda                 the one place an HTTP request is made
models   <- everything
funda    <- pipeline
db       <- pipeline, web, cli    no HTTP, no funda imports
schedule <- cli                   pure date arithmetic
pipeline <- cli                   the ONLY network + database module
render   <- cli
web      <- cli                   never fetches; JSON API + the built SPA
web.api  <- web, web.typescript   the API's response shapes (TypedDicts)
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
  within one `offering_type`, so every web query takes it explicitly:
  `browse_index`, `search_ids`, `counts`. The browser holds one side's index at
  a time and never merges them.
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
- **`web/api.py` is the API contract.** Every JSON response is typed with its
  `TypedDict`s; `frontend/src/lib/api.gen.ts` is generated from them and never
  edited by hand (prettier ignores it). `tests/test_api_contract.py` checks real
  responses against the shapes key for key, `Listing` against the table's
  columns, and fails when the generated file is stale. Change a response:
  edit `api.py`, regenerate, and let `tsc` find the readers.

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
with no `?options`). `photoUrl` in `frontend/src/lib/data.ts` is the one
builder; the API ships bare ids. The CDN blocks headless Chrome's default user
agent (`ERR_BLOCKED_BY_ORB`) -- set a normal one when screenshotting.

**Photos are hotlinked, never downloaded.** Migration 002 removed the download
path. It cost ~2 600 requests per first run, and every bug in it — AVIF served
into a `.jpg` via `Vary: accept`, PNGs mixed in, 404s as `text/plain` — existed
only because we were the HTTP client. A browser negotiates correctly on its own.

## The web app

React 19 + Vite + TanStack Router/Query/Virtual + shadcn/ui (`base-nova`
style, on Base UI -- not Radix). MapLibre from npm, lazy-loaded. Flask serves
`/api/*` and, for every other path, the built shell (hashed `/assets/*` are
`immutable`; the shell is `no-cache`). No SSR.

**Speed is the point of the stack.** Keep these:

- **The browser filters, not the server.** `/api/index/<offering>` ships that
  side's every house, columnar (`db.INDEX_COLUMNS`), gzipped, ETagged, built
  once per data version per process (`_data_version` stats the DB and WAL). The
  client keeps it in IndexedDB, paints from it before the first request, then
  revalidates -- usually a 304. Filters, sorts, facets, the map's GeoJSON and
  its bounds are all computed in memory (`lib/filters.ts`, `lib/data.ts`).
  This holds for thousands of houses, not hundreds of thousands.
- **Descriptions are not in the index** (~4 KB each). Text search matches
  address and buurt locally at once and unions `/api/search` hits in after a
  180 ms debounce. Both sides mirror `LIKE %q%`.
- `applyFilters` is `db._where`'s successor: a missing value never satisfies a
  bound, delisted houses are out unless `delisted=1`. Its tests are the old SQL
  tests; keep them in step.
- **The detail page renders at once from the index row**; photos, kenmerken
  and history stream in. It is prefetched on hover/focus (router
  `defaultPreload: "intent"`), and j/k prefetches the neighbours.
- The grid is **virtualised against the window** and grows in batches of 48
  as you near the end (`house-grid.tsx`), so the scrollbar reflects what you
  have seen, not the whole result. The batch count is module state keyed on
  the search, so Back from a detail page lands on a page tall enough to
  restore the scroll position.
  `HouseCard` and the buurt list are memoised; structural sharing keeps
  unchanged search params identity-stable so they skip re-renders. Check a
  change with a filter toggle before and after -- ~80 ms to paint on the Pi.
- Filter changes **replace** history; view/offering changes carry filters.

State and memory:

- **The URL is the source of truth.** `validateSearch` is tolerant (junk falls
  back, Dutch `350.000` parses) and custom `parseSearch`/`stringifySearch` keep
  `?label=A&label=B` rather than TanStack's JSON encoding.
- Filters are remembered per offering type in `localStorage`. Restore happens
  in the `/` route's `beforeLoad` and only on a URL with no filter parameter;
  saving happens on interaction, never on arrival; clearing removes the entry.
  `FILTER_KEYS` in `lib/filters.ts` is the single definition.
- Switching offering drops `price_min`/`price_max` and keeps the rest.
- `lib/session.ts` holds the last list's order (for j/k) and filters (for
  "All houses"). Module state on purpose: a shared detail link must not carry
  a stranger's filters.

The design system:

- Stock shadcn `base-nova`, Geist + Geist Mono via fontsource (bundled, so
  fonts stay self-hosted), tabular figures everywhere.
- **Colours are tokens in `src/index.css`**: `energy-*` (real NEN certificate
  colours, the same in both themes), `choro-*` (the buurt ramp, per theme),
  `--choro-fill` alpha (0.45 light, 0.32 dark). `@shadcn/lint` rejects palette
  colours, arbitrary values, inline styles and restyling a component through
  `className`. Dynamic values go through CSS custom properties
  (`style={{"--row-y": ...}}` + `translate-y-(--row-y)`); new looks go in as a
  variant in `components/ui/` (see `energy-label.tsx`, `dialog.tsx`'s
  `fullscreen`), which is exempt from the restyle rules.
- The theme is `light`/`dark`/`system`, class-based. A pre-paint script in
  `index.html` applies it; its storage key must match `ThemeProvider`'s.
- The favicon is an SVG in `frontend/public/` with an embedded
  `prefers-color-scheme` block. **XML forbids `--` inside comments** -- this
  codebase uses it as a dash everywhere else, and an invalid favicon fails
  silently. A pytest checks it.
- Keyboard: ⌘K palette (its own search -- cmdk's filtering is off, it cannot
  score 5 000 items), `/`, `M`, `[` (filter rail), `J`/`K`, `Esc`, `D`.
  `useHotkeys` stands aside while typing.
- The shadcn MCP server is configured in `.mcp.json`; use it to add registry
  components (`npx shadcn@latest add <name>` in `frontend/` does the same).

### The map

- **The instance lives as long as the map view.** A filter change only calls
  `source.setData()` with in-memory GeoJSON. Leaving the view calls
  `map.remove()`, freeing the WebGL context; the camera is kept in module state
  and restored on return, and only a first visit fits the bounds.
- **Theme changes rebuild the map rather than restyling it.** `setStyle()`
  discards our layers and MapLibre gives no reliable moment to re-add them.
- **MapLibre 6 needs `setWorkerUrl`.** Its worker is found relative to its own
  module, which Vite renames; `?worker&url` plus `worker.format: "es"` fixes
  it. MapLibre also sets `position: relative` on its container, so absolute
  positioning goes on a wrapper.
- MapLibre does not parse oklch; `tokenColour` converts theme tokens through a
  one-pixel canvas.
- **`/api/neighbourhoods.geojson` is deliberately unfiltered** -- outlines are
  geography, not data.
- **Quantile bins, not a linear ramp.** Buurt prices are right-skewed; even
  spacing put 69 of 102 buurten in the bottom two colours.
- Outlines are added **before** the house layers so markers stay on top. The
  toggle is disabled when no outlines exist, whatever `localStorage` says.
- A badge says when fewer houses are mapped than matched (coordinates arrive
  with the detail page).

## Docker

`Dockerfile` (Alpine, multi-stage: `node:22-alpine` builds the SPA, then the
Python stages copy `dist/` in) + `compose.yaml` (`web`, `scheduler`, `cli`,
`tunnel`). No Node in the runtime image.

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
  `serve` extra). A new one should have to justify itself -- in `frontend/`
  too, where every package is bytes on the first load.
- `cloakbrowser` is an optional extra (`uv sync --extra browser`), the fallback
  if Akamai tightens. Nothing imports it today. The `mcp__cloakbrowser__*` tools
  are a separate npm package and need none of it.
- Windows consoles default to cp1252 and mangle `€`/`m²`; `cli._use_utf8_output`
  handles it, and any new entry point needs the same.
- A funda page yields 15 fully-detailed listings. Pace them
  (`pipeline.DEFAULT_PACE`).

## Git

- Commit as the identity in the previous commits, exactly as it appears there
  (`git log -3 --format='%an <%ae>'`). Never invent or change it.
- Keep commit messages concise. No Claude Code link, no `Co-Authored-By: Claude`
  line, no other trailers.
