# HouseMaster

Track houses on [funda.nl](https://www.funda.nl) without checking it every day.

Point it at one or more funda searches. It caches everything they return in
SQLite, records price and status changes over time, and serves a fast,
filterable web app with a map. No HTML parsing and no browser: funda is a server-rendered Nuxt
app that ships its full state as JSON in every page.

![status](https://img.shields.io/badge/tests-298-brightgreen)
![python](https://img.shields.io/badge/python-3.14-blue)
![licence](https://img.shields.io/badge/licence-MIT-blue)

## Quick start

```bash
uv sync
uv run housemaster add 'https://www.funda.nl/zoeken/koop?selected_area=den-haag&floor_area=50-'
uv run housemaster fetch     # first run takes a few minutes
(cd frontend && npm ci && npm run build)
uv run housemaster serve     # http://127.0.0.1:8765
```

## Commands

```
add URL [--name N] [--no-check]   track a search (koop or huur)
rm ID                             stop tracking; keeps the houses
fetch [--max-pages N] [--max-details N] [--no-detail] [--no-boundaries]
serve [--host H] [--port P] [--debug]
status                            cache contents, tracked searches, last run
schedule [--at 06:00] [--tz ZONE] fetch daily, forever
search [--url U] [--all] [--format text|json|csv]    live, no database
```

All accept `--db PATH`; otherwise `$HOUSEMASTER_DB`, else `./data/housemaster.db`.

`fetch` walks every tracked search and reconciles against the union, so one
search never delists another's houses. A house needs two consecutive misses
before it counts as gone — funda pages a live result set, so a single absence
is usually paging jitter.

`selected_area` takes a comma-separated list: `den-haag,rijswijk-zh,voorburg`.

## Buy and rent

Both live in one cache and are never mixed in one view — a Buy/Rent switch
changes mode. `price` means euros to buy and euros per month to rent, so
switching drops the price range and keeps every other filter.

Your filters are remembered per side, so a €350k buy ceiling and a €1 800 rent
ceiling cannot contaminate each other. A shared link always wins over what you
had saved.

## Docker

```bash
cp .env.example .env      # Cloudflare tunnel token
docker compose up -d
docker compose run --rm cli add '<funda search url>'
docker compose run --rm cli fetch
```

Four services: `web` (gunicorn), `scheduler` (fetches at 06:00 daily), `cli`
(any command, on demand), `tunnel` (cloudflared). Nothing is published to the
internet — cloudflared dials out and reaches `web` over the compose network.
Point the tunnel's Public Hostname at `http://web:8765`.

The image is ~140 MB on Alpine. `curl_cffi` ships musllinux wheels, so
curl-impersonate never has to be compiled.

## How it works

```
curl_cffi (TLS impersonation) -> __NUXT_DATA__ -> devalue decode -> SQLite
```

Akamai gates funda on the TLS fingerprint, so every request goes through
`curl_cffi` with `impersonate="chrome"`. **A block returns HTTP 200**, not 4xx,
so success is defined as `__NUXT_DATA__` being present in the body.

```
src/housemaster/
  devalue.py   decoder for Nuxt's flat-array state format
  models.py    Listing / Detail / Feature / Boundary records
  net.py       the one place an HTTP request is made
  funda.py     search + detail extraction, bot-wall detection
  db.py        SQLite: schema, migrations, upserts, queries
  pipeline.py  the fetch orchestration
  schedule.py  when the next daily run is due
  render.py    text / json / csv output
  cli.py       argparse entry point
  web/         Flask: a JSON API, and the built SPA from frontend/

frontend/      React + Vite + TanStack Router/Query + shadcn/ui (Base UI)
  src/lib/       the index, filters, formatting -- no React
  src/components/  pages; ui/ is the shadcn design system
```

## The web app

The browser does the browsing. It downloads one side's whole index once
(~5 000 houses, ~450 KB gzipped), keeps it in IndexedDB, and filters, sorts and
maps it in memory: a filter change never touches the network, and a return
visit paints from disk before revalidating with an ETag. The server answers
the two things the index cannot — text search over descriptions, and one
house's detail — and the detail is prefetched on hover.

Keyboard: `⌘K` jump to a house, `/` search, `M` grid/map, `J`/`K` next/previous
house, `Esc` back, `D` dark mode.

`devalue`, `funda`, `db` and `web` each know nothing about the others' concerns;
`pipeline` is the only module that touches both the network and the database.
See `CLAUDE.md` for the invariants worth not breaking.

## Development

```bash
uv run pytest                              # 269 tests, offline
uv run ruff check . && uv run ruff format .
HOUSEMASTER_NETWORK_TESTS=1 uv run pytest -m network   # live-site canary

cd frontend
npm run dev        # Vite on :5173, proxying /api to `housemaster serve` on :8765
npm test           # 29 tests
npm run lint       # oxlint, with @shadcn/lint's design-system rules
npm run build      # into src/housemaster/web/dist, which `serve` picks up
```

`uv sync` fails on Windows while `serve` or `fetch` is running — stop them
first.

## Before you point this at funda

funda has no public API, so this reads the same pages a browser does.

- The default pace is one request every 0.4 s; a full sweep is a few hundred
  requests once a day.
- A detail page is fetched once per house ever, a buurt outline once per buurt
  ever, and photos cost nothing because they are hotlinked.
- Read funda's terms and decide whether your use fits. This is a personal
  research tool; redistributing the data is a different thing.
- The cached data is theirs. No scraped data ships here, and `data/` is
  gitignored.

## Licence

MIT — see [LICENSE](LICENSE). That covers the code, not the listing data.
