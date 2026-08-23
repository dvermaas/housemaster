# Findings

How funda behaves, what we decided because of it, and what we still do not know.

`README.md` holds the settled facts as documentation. `CLAUDE.md` holds the
rules for changing the code. **This file holds the reasoning** — why a decision
went the way it did, what was measured, and which threads are still open — so a
later session can pick up mid-thought instead of re-deriving it.

Organised by topic, not by date. Dated entries live in [Decision log](#decision-log)
and dead reasoning is kept in [Superseded](#superseded) rather than deleted, so
nobody re-argues a settled point from stale premises.

1. [Verified facts about funda](#1-verified-facts-about-funda)
2. [Fetch strategy](#2-fetch-strategy)
3. [Scoring houses](#3-scoring-houses)
4. [The map and neighbourhood boundaries](#4-the-map-and-neighbourhood-boundaries)
5. [Open questions](#5-open-questions)
6. [Decision log](#6-decision-log)
7. [Superseded](#7-superseded)

---

## 1. Verified facts about funda

Everything in this section has been observed directly, not inferred.

### The bot wall

- **Blocks return HTTP 200**, not 4xx — a ~15 KB Akamai interstitial.
  `raise_for_status()` will not catch it. Presence of `__NUXT_DATA__` is the
  only reliable success test.
- Passing depends **only on the TLS/JA3 fingerprint**. `curl_cffi` with
  `impersonate="chrome"` gets through; plain `requests`/`urllib` with a spoofed
  User-Agent does not.
- **The image CDN enforces the same check** — plain `curl` gets `403`. A real
  browser is fine. Consequence: a shell test against `cloud.funda.nl` proves
  nothing about whether hotlinking works; only a browser does.
- The **XHR** endpoints are gated harder still: `403` even with byte-identical
  headers, because they need Bot Manager cookies (`bm_sv`, `_abck`) earned by
  executing Akamai's sensor JS. Server-rendered pages are the way in.

### Search vocabulary

| Parameter | Values observed |
| --- | --- |
| `availability` | `available`, `negotiations`, `unavailable` |
| `sort` | `publish_date_utc_desc` (echoed as `{field: 'publish_date_utc', order: 'desc'}`); default is `relevancy_sort_order` |
| `selected_area` | `den-haag` (city), `gemeente-den-haag` (municipality), `den-haag/<buurt-slug>` (neighborhood) |
| pagination | `&search_result=N`, 15 per page |

- **The default `availability` is `['available', 'negotiations']`.** Omitting the
  parameter does *not* mean "everything" — sold and withdrawn listings were never
  in our results. Adding `availability=available` removes exactly one thing: the
  under-offer houses.
- **`gemeente-den-haag` and `den-haag` return the same set.** "All of The Hague"
  is already what `selected_area=den-haag` asks for.
- **Deep pagination is not capped.** Elasticsearch-backed search usually caps it;
  funda does not. On the 185-page search, page 100 returns 15, page 185 returns
  the last 13 (184 × 15 + 13 = 2 773), and page 186 returns 0 without erroring.

### Set sizes (Den Haag, measured 2026-08-23)

| Search | Listings | Pages |
| --- | --- | --- |
| `price=250000-350000&floor_area=60-` — the original target | 520 | 35 |
| `floor_area=50-` (no price ceiling) | **2 773** | **185** |
| `floor_area=50-&availability=available` | **1 726** | **116** |
| `price=250000-350000&floor_area=60-&availability=available` | 268 | 18 |

- **Under-offer is a huge share of the market**: 252 of the original 520
  (**48%**) and 1 047 of the widened 2 773 (**38%**). Not a rare state.
- **The head moves 11–50 listings/day** — 11 published 2026-08-22, 49 published
  2026-08-21 across the four newest pages. That is 1–4 pages/day of movement.
- **The tail is old**: the last page's listings were published **2024-05-04**,
  over two years on the market.

### What the payload carries

- **Every photo id, in order** — `photo_image_id` is a complete CDN path, so
  photos need no detail request. See [Decision log](#photos-are-hotlinked-2026-08-23).
- **`localInsights` gives a per-buurt €/m²** and inhabitant count, stored as
  `neighbourhood_price_m2` / `neighbourhood_inhabitants`. This is the single most
  useful field we get for free — see [Scoring](#3-scoring-houses).
- **`aggregations` is a free filter vocabulary we are not using.** It arrives
  with every search request and includes facets that map directly onto things a
  buyer cares about:

  | Facet | Values worth having |
  | --- | --- |
  | `surrounding` | `on_quiet_road`, `unobstructed_view`, `overlooking_park`, `in_center` |
  | `amenities` | `fixer_upper`, `fireplace`, `renewable_energy`, `bathtub` |
  | `exterior_space_type` | `balcony`, `garden`, `terrace` |
  | `accessibility` | `lift`, `ground_floor`, `single_storey` |
  | `construction_period` | `before_1906` … `after_2020` |
  | `zoning` | `residential`, `recreational` |

- **The kenmerken carry per-house location quality.** `Buitenruimte | Ligging`
  is present on **2 092 of 2 773** houses and already lands in our `features`
  table: *"Aan rustige weg en in woonwijk"* (522), *"In woonwijk"* (361),
  *"In woonwijk en vrij uitzicht"* (109), *"Aan rustige weg, in woonwijk en vrij
  uitzicht"* (104), plus busy-road and centre variants.
- **`cachedListingData_nl` carries `isSoldOrRented`** plus a `salesHistory`
  field (null on live listings). This is what makes the resolution pass possible.
- **Search `criteria` carries the area polygon** — `geographicalArea`, a
  GeoJSON-style ring. See [The map](#4-the-map-and-neighbourhood-boundaries).

### Their own map

Funda's map runs Google Maps for tiles only; every pin is their data from an
Elasticsearch `geotile_grid` aggregation, drawn on a canvas overlay. Not
something we can read directly.

---

## 2. Fetch strategy

### Cost model

| Pass | Cost | Frequency |
| --- | --- | --- |
| Sweep (complete walk) | 116–185 requests | per complete run |
| Detail | 1 per *new* house, ~0.3 s | once per house, ever |
| Resolution | 1 per *departed* house | once per departure |
| ~~Photos~~ | ~~5 per new house~~ | **removed** — hotlinked |

Every queue is a SQL predicate over stored state (`detail_fetched_at IS NULL`),
never a set built during the run. That is what makes an interrupted fetch
resumable by running it again, with no checkpoint and no `--resume` flag.

### Discovery vs reconciliation is one flag, not two pipelines

They are the same sweep with one option, and **the gate that makes it safe
already exists**:

```python
# pipeline.py:71
# Only a complete sweep is evidence of absence. After a partial one, every
# unread page's listings would look missing.
if report.complete and options.max_pages is None:
    report.delisted = db.reconcile_presence(conn, seen, now=now)
elif not report.complete:
    progress("partial sweep -- skipping presence check")
```

An early-stopped sweep returns with `report.complete = False`
(`pipeline.py:124`) and reconciliation is skipped automatically. The invariant
*"reconcile only after a complete walk"* — written for `--max-pages` — is
exactly what makes early-stop safe. The change is
`FetchOptions(stop_when_known=True)`, not a second pipeline.

> **Never let early-stop and delisting run in the same pass.** Stopping at page 2
> of 116 leaves ~1 700 houses unseen; delisting them would wipe the database
> every run. The gate above is the whole defence — do not weaken it.

Why delisting also needs **two consecutive misses** (`missed_runs >= 2`): funda
pages a *live* result set, so a listing inserted mid-walk shifts everything down
and one house per page boundary can be skipped entirely. A single absence is not
evidence. `sort=publish_date_utc_desc` reduces this to head-insertion only — the
mildest possible jitter — which is why it is worth adopting for **stability**,
not for speed.

### Resolving the departed

`delisted_at` means "stopped matching this search", **not** "sold". With
`availability` filtered, a sold house never changes status — it drops out
entirely, and absence is ambiguous: sold, withdrawn, under offer, or jitter.

One detail re-fetch per departure disambiguates it:

| The departed house's detail page says | Means |
| --- | --- |
| `isSoldOrRented: true` | actually sold — record it, plus `salesHistory` price |
| still live, not sold | **under offer** (left `available`, still on funda) |
| 404 / gone | withdrawn |

This is the highest-value pending change: it turns `delisted_at` from "dunno"
into a real outcome, for roughly 30 requests/day.

### Why `availability=available` is the right filter

The old objection was that an under-offer house vanishes and cannot come back.
Both halves have answers:

- **Coming back is handled by the complete sweep.** A bounced-back house
  reappears at its original publish date, deep in the pages, and a daily
  *complete* walk visits that page. Both `db.upsert_listing` (sets
  `delisted_at = NULL` on any update) and `db.reconcile_presence` (resets
  `missed_runs = 0, delisted_at = NULL` for seen ids) already un-delist it.
  No new code. **This leans on an untested assumption** — see
  [open questions](#does-a-bounced-back-house-keep-its-original-publish-date).
- **The status is reconstructed** by the resolution pass above, more accurately
  than the `status` column ever gave it.

Keeping `negotiations` to preserve the status signal would cost **69 extra pages
on every complete sweep** (185 vs 116). Reconstructing it costs one request per
departure. The filtered search is both cheaper and more informative.

**Schema consequence:** the search payload's `status` becomes dead weight — with
the filter on, everything returned is `available` by construction, so it carries
no funda information that `delisted_at IS NULL` does not already say. But do
**not** collapse the whole idea to a boolean: the *derived* state has four values
worth storing — active, sold, under offer, withdrawn — and they come from
resolution, not from the sweep.

### The cadence frontier

For the 116-page `available` search, at the measured listing rate:

| Cadence | Requests/day | New-listing latency |
| --- | --- | --- |
| Daily complete sweep only | 116 | up to 24 h |
| Hourly early-stop + daily complete sweep | ~140 | ~1 h |
| 15-min early-stop + daily complete sweep | ~210 | ~15 min |
| Hourly complete sweep | 2 784 | ~1 h |

Early-stop buys **24× better latency for +21% requests**, and it is only safe
*because* the daily complete sweep is what does the reconciling.

### On being polite to funda

Volume is not the risk. 116–210 requests/day against a site of funda's size is
nothing, and the detail backlog is a one-off. The risk is **pattern**: a fixed
0.4 s pace from one IP firing at exactly `:00` every hour is a more legible
signature than the request count suggests. If cadence goes up, jitter the pace
and the start minute. That is worth more than shaving requests.

---

## 3. Scoring houses

Goal: one number per house combining location, price, size and energy label.

### Location is already priced, and funda hands it to us

The hard part of the problem — *"how do we algorithmically know Laakkwartier is
worse than Bezuidenhout?"* — is already answered by `neighbourhood_price_m2`.
It is the market's own valuation of the location, and it matches local knowledge
exactly:

| Buurt | funda €/m² |
| --- | --- |
| Kijkduin (top of 85) | 7 641 |
| Arendsdorp | 6 964 |
| Zorgvliet | 6 918 |
| Vogelwijk | 6 504 |
| Archipelbuurt | 5 968 |
| Bezuidenhout-Oost / Midden / West | 5 064 / 4 972 / 4 953 |
| Laakkwartier-Oost / West | 3 697 / 3 675 |
| Noordpolderbuurt | 3 606 |
| Transvaalkwartier-Zuid (bottom of 85) | 3 533 |

A 2.2× spread across 85 buurten with ≥8 listings (105 distinct buurten overall).
No extra requests — it arrives with every detail page.

The figure is a **per-buurt constant**, not an average of our sample: it did not
move at all between a 1 300-house and a 2 773-house cache. That makes it a
stable anchor to score against, and it also means a buurt needs only *one*
enriched house for us to know its price level.

### Three orthogonal axes

The trap: if location score = buurt €/m² *and* value score = price/m², the two
fight each other and mostly reproduce "price". Decompose instead into axes that
do not overlap, so the weights actually mean something:

| Axis | Built from |
| --- | --- |
| **Location** | buurt €/m², plus the `Ligging` tags (quiet vs busy road, unobstructed view, centre) |
| **Value** | this house's €/m² **relative to its own buurt** |
| **Intrinsic** | energy label, living area, rooms, construction period, outdoor space, lift |

The value axis has real spread: **27% to 231%** of buurt average across 2 724
houses, with **352** more than 15% under.

### Two checks worth knowing about

Both were run against the cache; one confirmed a worry, one killed it.

- **The value ratio *is* confounded by size, in a U-shape.** Mean ratio by band
  on the complete cache:

  | Living area | n | mean % of buurt €/m² |
  | --- | --- | --- |
  | `<70 m²` | 434 | **111%** |
  | `70–89` | 722 | 106% |
  | `90–109` | 411 | 104% |
  | `110–139` | 449 | **102%** |
  | `140+ m²` | 708 | **113%** |

  Both tails carry a per-m² premium and mid-size sits cheapest — plausibly
  because small flats and large detached houses are each priced against a
  different sub-market than the buurt's terraced-apartment average. **A raw
  ratio therefore flags mid-size houses as bargains simply for being mid-size.**
  Normalise within size band before scoring on it.

  Worth flagging *how* this was caught: at ~1 300 enriched houses the same query
  showed a flat 101–104% above 70 m², and the conclusion drawn was "not
  confounded by size". The completed 2 773-house cache reversed it. Enrichment
  runs in insertion order, which under a date-sorted sweep means newest-first —
  so **partial-enrichment statistics are biased toward recent listings**, not a
  random sample. Do not draw conclusions from a running fetch.
- **Naive text search does not explain the discounts.** Renovation words
  (`renoveren`/`opknappen`/`renovatie`) appear in 7% of the under-85% group vs
  5% of the rest — not a real difference. And `erfpacht` does not appear in our
  stored feature *values* at all, which suggests leasehold lives under a
  `Kadastrale gegevens` label we are not reading correctly.

So a house at 55% of its buurt average is a **"why is this cheap?" flag**, not a
proven bargain. Surface it to a human rather than ranking on it blindly.

### Where an LLM fits

Numbers for the score, LLM for the prose. We already store `description.content`
— clean Dutch text on every enriched house. An LLM reading those and emitting
*structured columns* (needs full renovation, erfpacht until 2041, recently
renovated, no outdoor space) produces exactly the fields that would explain the
discounts above. It is a one-time pass over cached text with no scraping.

Asking an LLM for a score directly is the worse option: unreproducible,
unexplainable, and it would largely re-derive price from the same inputs.

---

## 4. The map and neighbourhood boundaries

**Built 2026-08-23.** All 105 Den Haag buurten are stored and drawn.

### funda hands out the polygons

`selected_area=den-haag/<buurt-slug>` returns `areaType: 'neighborhood'` with
real geometry in `criteria.selected_area[0].geographicalArea` — a map of rings,
one entry for a simple buurt and several where water splits it. Cost: one
request per buurt, once, forever. Boundaries do not move.

### The slug rule deletes periods, it does not hyphenate them

A naive ASCII-fold-and-hyphenate slug resolved 102 of 105. All three failures
were buurten named `... e.o.` (*en omgeving*), because funda writes
`Koningsplein e.o.` as **`koningsplein-eo`** — the periods are deleted before
the remaining non-alphanumerics become hyphens, so `e.o.` collapses to one word.

Deleting `.` and `'` before hyphenating takes it to 105 of 105. Worth
remembering that `koningsplein-en-omgeving` and `koningsplein` both fail, so
this is a real rule rather than a lucky guess.

A miss is a **normal outcome, not an error**: funda answers an unresolvable
buurt slug with the unfiltered *city* search rather than a 404, so `areaType` is
the only way to tell. `MAX_BOUNDARY_ATTEMPTS` stops an unresolvable buurt being
re-requested every run — and because a success clears `attempts`, fixing the
slug rule let the next run pick up exactly the three that had failed.

### Quantiles, not a linear ramp — this is what makes it legible

The first working version was near-unreadable: every buurt the same pale wash.
Buurt prices are strongly right-skewed, so splitting 3 533–7 641 into five even
bands gives:

| bins | counts across 102 buurten |
| --- | --- |
| even split of the range | `[32, 37, 26, 4, 3]` |
| equal-count (quantile) | `[20, 20, 21, 20, 21]` |

Seven buurten in the top two colours draws as one flat colour. The quantile
edges (4 105 / 4 516 / 5 017 / 5 552) put about a fifth of the city in each
step. The layer uses a `step` expression over those edges, and the legend bar
uses hard stops rather than a gradient because a smooth bar would promise a
precision the bins do not have.

### Translucency compresses the ramp

Second legibility trap, found the same way. A translucent fill composites toward
the basemap, so at 45% alpha the *visible* spread is less than half the ramp's
own — a tight ramp disappears. The ramp therefore spans a deliberately wide
lightness range.

The alpha also has to differ per theme: **0.45 light, 0.32 dark**. A bright wash
over a dark basemap hides far more than a dark wash over a pale one; at parity
the dark map lost its streets entirely under solid blue shapes.

### Decisions baked in

- **The outline feed is unfiltered.** Geography, not data — buurten blinking out
  as a price slider moves reads as a bug. It also means the map never reloads
  that source.
- **Outlines are added before the house layers**, so markers are never washed
  over.
- **The toggle is a button outside `#map`**, not a form control: it changes what
  the map draws, not what gets submitted. htmx replaces it on every swap, so
  `map.js` rebinds it and restores state from `localStorage`, like the theme.

### Still open

Clicking a buurt could check its box in the filter rail — the obvious next
gesture, and cheap now that the polygons are there. Not built: it crosses from
"map draws" into "form submits", which is the one boundary this feature was
careful to keep.

## 5. Open questions

### Do CDN images survive a listing being removed?

Photo URLs are content-addressed and immutable
(`cloud.funda.nl/tiara-media/{uuid}/{uuid}`,
`Cache-Control: max-age=31536000, immutable`). Whether funda garbage-collects
media for sold or withdrawn listings is untested.

This used to decide whether to download photos at all. That decision has since
gone the other way, so it is now only a question about the *archive*: if funda
does collect them, a delisted house's photos will eventually stop rendering.
The ids are still stored, so an archival pass could be added back for the houses
that matter.

**About to answer itself.** With 2 773 houses cached instead of 150 and ~30
departures a day, real delistings arrive within days — re-request a departed
house's `photo_image_id`. Or force it now: `availability=unavailable` returns
sold/withdrawn listings, so take a `photo_image_id` from one and try it.

### Does a bounced-back house keep its original publish date?

**This decides how much work the daily complete sweep is doing.**

The argument that a complete sweep re-finds a bounced-back house assumes funda
preserves `publish_date_utc` when a listing returns from under-offer to
available, so it reappears deep in the ordering rather than at the head.

If funda instead *bumps* the date on re-entry, the house comes back at the head,
early-stop catches it, and the complete sweep matters much less than assumed.
Cheap to settle: record a negotiating house's `published`, check it when the
status flips back.

### Does time-on-site predict *why* a house left?

The intuition: a listing that disappears within days probably did not *sell* —
too fast for a Dutch sale to complete — so it is more likely withdrawn,
re-listed by another agent, or an input error. One that sits for weeks and then
goes is far more likely a real sale.

If it holds, `first_seen_at → delisted_at` becomes a cheap prior on the outcome,
and the resolution pass *confirms* rather than being the only source. It also
suggests a genuinely useful client-facing statistic: **how long comparable houses
in this buurt last before they go under offer.** For a tool whose point is
reacting fast, "houses like this are gone in 6 days" beats any single listing.

Needs `price_history` plus `delisted_at` across a few weeks. Nothing to build now
— just do not throw away the timestamps that make it answerable later.

### What is actually in `Kadastrale gegevens`, and where is erfpacht?

Leasehold is one of the biggest reasons a Dutch house is cheap per m², and it
does not appear in our stored feature values. Either we are not capturing the
group correctly, or funda puts it under a label we are not matching. One house's
raw kenmerken would settle it, and it feeds straight into the scoring work.

### Why *are* the 52%-of-buurt houses cheap?

Neither size nor renovation language explains them. Until something does, the
value axis is a flag for human attention rather than a rankable score.

---

## 6. Decision log

### Photos are hotlinked (2026-08-23)

The photo-download pass is **removed**. `photos.py` is a URL builder with no
imports, migration 002 drops every column that described a file on disk, and
40 MB of `data/media/` is gone.

What it bought:

- The longest part of a first run disappears — ~2 600 requests.
- The `Vary: accept` AVIF trap, PNG extension detection, the `text/plain` 404,
  `.part`-file atomicity and the retry/`attempts` bookkeeping all go with it.
  **Every one of those bugs existed only because we were the HTTP client.**
  A browser negotiates content correctly on its own.
- Widening the search became affordable, which is what made the 2 773-house
  cache possible at all.

What it costs: the archive property, gated on the open question above.

Verified in a real browser after the change: cards, gallery and map popup all
render straight from `cloud.funda.nl` with `referrerpolicy="no-referrer"`, zero
broken images, across both the `tiara-media/` and `valentina_media/` id spaces.

### Vendored JS moved to a CDN with SRI (2026-08-23)

htmx 2.0.4 and MapLibre 5.6.0 load from jsDelivr with `integrity` hashes rather
than being vendored. The hashes were computed from the exact bytes that had been
vendored, after confirming jsDelivr serves those files byte-for-byte identically
— so the pin is to audited code, not to whatever the CDN happens to hold.

The two woff2 fonts stay vendored: SRI does not cover `@font-face`, so a CDN font
would be the one unverifiable request on the page, and glyph data is not
executable anyway.

### The widened search was fetched (2026-08-23)

`selected_area=den-haag&floor_area=50-&sort=publish_date_utc_desc` — 185 pages,
2 773 listings, 2 623 new, **0 delisted**, confirming the new URL is a clean
superset of the old 520-listing search. All 2 773 are now enriched with detail
pages. 100 318 photo ids stored and **zero image bytes downloaded**.

Note this URL keeps `negotiations` (the default). Switching to
`availability=available` is still a pending decision.

---

### The buurt overlay was built (2026-08-23)

105 outlines fetched once from funda and stored in a new `boundaries` table
(migration 003), drawn as a toggleable choropleth shaded by buurt €/m².

Two findings came out of building it rather than planning it: the `e.o.` slug
rule, and that **legibility was entirely a scale problem, not a colour problem**
— the first version was correct and unreadable. Quantile bins and a wide,
per-theme alpha fixed it. Both are written up in [§4](#4-the-map-and-neighbourhood-boundaries).

---

### Searches became a tracked set, and the cache was reset (2026-08-23)

`housemaster add <url>` / `rm <id>` maintain a `searches` table (migration 004);
`fetch` walks all of them. `--url` survives as an ad-hoc override that is not
saved.

**The one thing that had to be right: reconciliation takes the union.** A house
is present if *any* tracked search still returns it, so `run_fetch` accumulates
one `seen` set across every search and reconciles once at the end. Reconciling
inside the per-search loop would have each sweep decide every *other* search's
houses were absent — they would delist while being perfectly live, and nothing
would look broken until someone noticed the whole cache greying out. Four tests
pin it, including one where a search is blocked midway (a partial walk of the
union is not evidence of absence for anyone).

Corollary: **nothing records which search found a house.** Presence is a
property of the union, which is exactly what reconciliation needs. Per-search
provenance would have to be maintained on every sweep and would make "gone"
ambiguous.

`rm` keeps the houses rather than deleting them. They delist through the normal
two-strike rule, which is what `delisted_at` has always meant — and it preserves
price history for a house that may still be live and merely out of scope.

The cache was then reset and refetched against
`?selected_area=den-haag&floor_area=50-`, dropping the leftovers from the
original 250–350k / 60m² search that no longer matched anything tracked. The old
36 MB database is in the scratchpad backup directory.

Note this URL omits `sort=publish_date_utc_desc`. It does not change *which*
listings come back, only their order, so it costs nothing today — but the sweep
stability argument in [§2](#2-fetch-strategy) still applies if early-stop is
built.

---

### Rentals joined the same table (2026-08-23)

Measured before deciding: between a rental and a purchase search result,
**zero keys differ** except the price field. `rent_price` / `rent_price_condition`
against `selling_price` / `selling_price_condition`, plus `offering_type`. The
detail page, kenmerken, coordinates and photos all extract unchanged.

So: one table, one `offering_type` column (migration 005), one `price` column
whose unit lives in the `price_condition` field that already carried
`kosten_koper`. Not a second table — it would duplicate ~20 identical columns
and the foreign keys from `features`, `photos` and `price_history`. Not a second
database — that would make rent-to-buy per buurt a cross-file join instead of a
`GROUP BY`, and that ratio is the main prize of having both.

Two columns (`selling_price` + `rent_price`) was the considered alternative, and
its argument is real: an unscoped query returns NULL rather than nonsense. It
lost because `Filters.offering_type` being mandatory removes most of that risk,
and because `price_condition` already models the unit — extending a vocabulary
beats adding a parallel one.

Facts that shaped it:

- **`price` was silently `None` for every rental** before this, because
  `to_listing` only ever read `selling_price`.
- **1 rental in 60 is quoted `per_year`** (parking spaces). Normalised to
  monthly at extraction; funda's wording survives verbatim in the kenmerken.
- **`localInsights` is purchase-only.** A rental in Willemspark returns
  `neighbourhood_price_m2 = 5915`, which is that buurt's *purchase* €/m². funda
  gives us no rent benchmark at all, so the value axis in
  [§3](#3-scoring-houses) does not transfer and any rental scoring needs its own
  benchmark built from our data.
- **Service costs are prose, not a field** — `€ 1.725 per maand (servicekosten
  onbekend)`. Rental comparisons are inherently noisier than purchase ones.
- **Rental searches return junk object types** (`parking`, `storage_space`,
  `berth`); an `object_type` filter is worth adding if the noise matters.
- Scope is small: **284 rentals at 50m²+** against 2 773 for sale.

---

### The tracked area widened to three municipalities (2026-08-23)

`selected_area=den-haag,rijswijk-zh,voorburg` — funda takes a comma-separated
list and resolves each to a `city` area. 3 377 for sale and 327 to rent at
50m²+, replacing the Den-Haag-only pair. The new searches are supersets, so
nothing delisted.

`rijswijk-zh`, not `rijswijk`: the plain slug is ambiguous with Rijswijk in
Gelderland and resolves to nothing.

**Why the map's eastern lobe looked wrong.** Listings around Ypenburg and
Leidschenveen (postcodes 2492–2498, buurten *De Vissen*, *De Lanen*, *De Bras*)
are genuinely Den Haag — annexed into the municipality in 2002 — while Voorburg
and Rijswijk sit between them and the city centre as *separate* municipalities.
So the boundary really is a main body plus a detached eastern chunk. Not bad
coordinates: every one of those buurten has its own funda outline, and all
3 046 carried `city = "Den Haag"`.

Consequence worth remembering: **"in Den Haag" is a poor proxy for "near the
centre"** — Ypenburg is ~10 km out, Voorburg ~4 km.

**Two bugs this surfaced immediately**, both fixed:

- **Buurt names repeat across cities.** `Bomenbuurt` exists in Den Haag *and*
  Rijswijk; `Kleurenbuurt` in Rijswijk *and* Voorburg. The `boundaries` table
  was keyed on the name alone, so the second fetch would have overwritten the
  first and the choropleth would have drawn one city's outline over another's
  houses. Migration 006 rebuilds it on `(city, name)`.
- **A buurt slug only resolves under its own city.** `den-haag/cromvliet`
  returns the unfiltered search, not a neighbourhood, so the outline pass would
  have failed for every Rijswijk and Voorburg buurt. `fetch_boundary` now takes
  the city, slugified by the same rule — funda's city identifiers match it, so
  `Rijswijk (ZH)` becomes `rijswijk-zh` with no special case.

Caught by checking the data before the fetch finished rather than after, which
is the only reason the boundaries table never got polluted.

---

## 7. Superseded

Kept so nobody re-argues these from the original premises.

- **"Filtering to `availability=available` loses information."** The objection
  was that an under-offer house vanishes and cannot come back. Superseded by
  [§2](#why-availabilityavailable-is-the-right-filter): a complete sweep re-finds
  it, and the resolution pass reconstructs the status more accurately than the
  `status` field did.
- **"Skip early-stop until the search is wide enough to justify it."** The search
  is now wide enough — 116–185 pages instead of 35.
- **"Three jobs, not one"** (discovery / reconciliation / enrichment as separate
  pipelines). The right framing is *one sweep with one flag*, because
  `report.complete` already gates the dangerous half. The three *questions* are
  still the right way to think about cadence; the three *pipelines* were not.
- **"The sweep is already cheap, optimising it saves ~22 seconds."** True at 35
  pages, false at 185. The argument for early-stop was never run duration — it is
  that early-stop makes a high *cadence* affordable.
- **"The photo pass is what explodes if the search widens."** It no longer
  exists. Widening now costs sweep pages plus one detail request per new house.
