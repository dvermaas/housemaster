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

uv run housemaster search              # page 1 of the target search
uv run housemaster search --page 2 --url "<funda search url>"
uv run housemaster search --format json|csv
uv run housemaster search --all --max-pages N

uv run pytest                          # full suite, offline
uv run pytest tests/test_devalue.py::test_cycle_terminates   # a single test
uv run ruff check . && uv run ruff format .
```

The suite is **offline by default**: `tests/conftest.py` skips anything marked `network` unless `HOUSEMASTER_NETWORK_TESTS=1` is set. Run `HOUSEMASTER_NETWORK_TESTS=1 uv run pytest -m network` to exercise the live-site canary — that test is the early warning for funda changing its payload shape or Akamai tightening.

CloakBrowser's stealth Chromium is managed separately from pip packages:

```bash
uv run cloakbrowser info     # diagnostics: binary path, version, license, module status
uv run cloakbrowser install  # download the Chromium binary (already installed on this machine)
```

## Architecture

Funda is a server-rendered **Nuxt 3** app that embeds its entire page state as JSON in `<script id="__NUXT_DATA__">`. There is **no HTML parsing and no browser** in the pipeline:

```
curl_cffi (TLS impersonation)  →  __NUXT_DATA__  →  devalue decode  →  dataclasses
```

Src layout, hatchling build, `housemaster` console script via `[project.scripts]`. The layers are kept apart so each is testable alone — **preserve this when adding code**:

- `src/housemaster/devalue.py` — decoder for Nuxt's devalue format: a *flat array* where integers **inside a container** are references into that same array (an integer in the slot a reference lands on is a literal, not a further hop), and a list whose first element is a string is a tagged value. Handles cycles and the `Ref`/`Reactive` wrappers. Knows nothing about funda.
- `src/housemaster/models.py` — frozen `Listing` / `SearchPage` dataclasses, `PAGE_SIZE`.
- `src/housemaster/funda.py` — all funda-specific knowledge: `fetch_html` (bot-wall detection), `extract_state`, `to_listing`, `to_search_page`, `fetch_search_page`, `iter_all_listings`. Knows nothing about output formats.
- `src/housemaster/render.py` — text/json/csv formatting. Never touches the network.
- `src/housemaster/cli.py` — argparse entry point; the only module that prints.

Errors derive from `FundaError`: `BlockedError` (bot wall) and `PayloadError` (page loaded, shape unexpected). The CLI catches `FundaError` and returns exit 1.

Search state lives at `state["pinia"]["search"]` (`listings`, `totalListingsCount`, `criteria`, `aggregations`); detail-page state at `state["data"]["cachedListingData_nl"]`. Pagination is `&search_result=N`, 15 per page.

### The trap: blocks return HTTP 200

Akamai's interstitial answers **200** with a ~15 KB page. `response.raise_for_status()` will not catch it. Success is defined as `__NUXT_DATA__` being present in the body — `fetch_html` enforces this and raises `BlockedError`. Never relax that check.

Passing the wall depends only on the TLS/JA3 fingerprint, so every request must go through `curl_cffi` with `impersonate="chrome"`. Plain `requests`/`urllib` with a spoofed User-Agent gets the interstitial.

## Fetching strategy

1. **`curl_cffi`** — the whole pipeline. ~0.3 s/page.
2. **`cloakbrowser`** — fallback and investigation only, for when Akamai tightens or the payload shape changes. Drop-in Playwright API: `from cloakbrowser import launch` (plus `launch_async`, `launch_context`, `launch_persistent_context`, `*_async`). Useful kwargs: `proxy`, `humanize=True` + `human_preset`, `geoip=True`, `locale`/`timezone`, `stealth_args` (on by default — leave it on).
3. **`trafilatura`** — currently unused. Funda ships clean prose in `description.content`, so there is nothing to boilerplate-strip. Keep the dependency only if a non-funda source is added.

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

The user's global config (`~/.claude.json` → `mcpServers.cloakbrowser.env`) is **already set to `PLAYWRIGHT_MCP_HEADLESS=false`** so browser work is visible. Changes there need a session restart. If a run must be headless, flip that value rather than looking for a tool parameter.

Practical notes when driving the browser:

- A **Didomi consent overlay** intercepts every click on funda until dismissed ("Alles weigeren" / "Alles accepteren").
- The free CloakBrowser licence allows **one concurrent session** — close the page when done, and never design around parallel browser workers.
- `.playwright-mcp/` at the repo root is the MCP's output directory (snapshots, screenshots), not project source. Tool calls that take a `filename` write outside it, to the MCP server's own cwd.

## Other constraints

- Optional CloakBrowser extras are **not** installed: `geoip2`, `aiohttp`, `websockets`. `geoip=True` needs `uv add geoip2` plus a DB download first.
- `mcp__claude-in-chrome__*` may also be available; it drives the user's real Chrome and is not stealth — prefer the cloakbrowser server for funda.
- One request yields 15 fully-detailed listings, so the full Den Haag set is ~35 requests. Pace them, and cache fetched HTML while iterating on extractors rather than re-hitting funda every run.
- Windows consoles default to cp1252 and mangle `€`/`m²`/Dutch names. `cli._use_utf8_output()` reconfigures the streams; any new entry point needs the same.
- Ruff enables `T20` (no stray `print`) everywhere except `cli.py`. Library code returns data; the CLI prints it. Keep it that way rather than widening the ignore.
