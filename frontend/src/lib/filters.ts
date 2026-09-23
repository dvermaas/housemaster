/* Filters: the URL schema, and the in-memory query that replaces SQL.
 *
 * The URL is the source of truth. Every filter lives in the query string, so a
 * link carries its filters, reload keeps them and Back works. The shape stays
 * the plain `?label=A&label=B` the server-rendered app used, so old links still
 * open on the same set.
 */
import type { House, Offering } from "@/lib/data"

export const SORTS = {
  newest: "Newest on funda",
  added: "Recently cached",
  price_asc: "Price, low to high",
  price_desc: "Price, high to low",
  ppm2_asc: "€/m², low to high",
  area_desc: "Largest first",
} as const
export type Sort = keyof typeof SORTS
export const DEFAULT_SORT: Sort = "newest"

export const STATUSES = {
  none: "Still available",
  under_bid: "Under offer",
  sold_under_reservation: "Sold under reservation",
} as const

export type View = "grid" | "map"

/** Everything `/` understands. Omitted means default, so a clean URL is bare. */
export type BrowseSearch = {
  offering?: Offering
  view?: View
  q?: string
  price_min?: number
  price_max?: number
  area_min?: number
  area_max?: number
  rooms_min?: number
  beds_min?: number
  label?: string[]
  hood?: string[]
  status?: string
  delisted?: 1
  sort?: Sort
}

/** Parameters that narrow the set, as opposed to `view` and `offering`, which
 *  are properties of the page. Only these are remembered between visits. */
export const FILTER_KEYS = [
  "q", "price_min", "price_max", "area_min", "area_max", "rooms_min",
  "beds_min", "label", "hood", "status", "delisted", "sort",
] as const satisfies readonly (keyof BrowseSearch)[] // prettier-ignore

const NUMBER_KEYS = [
  "price_min", "price_max", "area_min", "area_max", "rooms_min", "beds_min",
] as const // prettier-ignore
const LIST_KEYS = ["label", "hood"] as const

/** Tolerant: a hand-edited `?price_min=abc` or `?sort=';DROP` must fall back,
 *  never crash. Dutch thousands separators are accepted, as typed. */
function toInt(value: unknown): number | undefined {
  if (typeof value === "number")
    return Number.isFinite(value) ? Math.trunc(value) : undefined
  if (typeof value !== "string") return undefined
  const cleaned = value.trim().replaceAll(".", "").replaceAll(",", "")
  if (!/^\d+$/.test(cleaned)) return undefined
  return Number.parseInt(cleaned, 10)
}

const toList = (value: unknown): string[] =>
  (Array.isArray(value) ? value : value == null ? [] : [value])
    .map(String)
    .filter(Boolean)

export function validateSearch(raw: Record<string, unknown>): BrowseSearch {
  const search: BrowseSearch = {}
  if (raw.offering === "rent") search.offering = "rent"
  if (raw.view === "map") search.view = "map"
  const q = typeof raw.q === "string" ? raw.q.trim() : ""
  if (q) search.q = q
  for (const key of NUMBER_KEYS) {
    const n = toInt(raw[key])
    if (n !== undefined) search[key] = n
  }
  for (const key of LIST_KEYS) {
    const list = toList(raw[key])
    if (list.length) search[key] = [...new Set(list)]
  }
  if (typeof raw.status === "string" && raw.status in STATUSES)
    search.status = raw.status
  if (raw.delisted === 1 || raw.delisted === "1") search.delisted = 1
  if (
    typeof raw.sort === "string" &&
    raw.sort in SORTS &&
    raw.sort !== DEFAULT_SORT
  ) {
    search.sort = raw.sort as Sort
  }
  return search
}

// --- the query string ------------------------------------------------------
// TanStack Router's default serialises to JSON (`?label=%5B%22A%22%5D`). These
// keep the repeated-key form a person can read and edit.

export function parseSearch(searchString: string): Record<string, unknown> {
  const params = new URLSearchParams(searchString)
  const out: Record<string, unknown> = {}
  for (const key of new Set(params.keys())) {
    const values = params.getAll(key)
    out[key] = (LIST_KEYS as readonly string[]).includes(key)
      ? values
      : values[0]
  }
  return out
}

export function stringifySearch(search: Record<string, unknown>): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(search)) {
    if (value === undefined || value === null || value === "") continue
    if (Array.isArray(value))
      for (const v of value) params.append(key, String(v))
    else params.set(key, String(value))
  }
  const query = params.toString()
  return query ? `?${query}` : ""
}

export const hasFilters = (search: BrowseSearch) =>
  FILTER_KEYS.some((key) => search[key] !== undefined)

// --- the query -------------------------------------------------------------

/** A missing value never satisfies a bound -- as in SQL, where NULL >= 3 is
 *  not true. A house with no known area is not "at least 80 m²". */
const atLeast = (v: number | null, bound: number | undefined) =>
  bound === undefined || (v !== null && v >= bound)
const atMost = (v: number | null, bound: number | undefined) =>
  bound === undefined || (v !== null && v <= bound)

/** The filtered set, in index order. Mirrors what `db._where` used to do in
 *  SQL: a missing value never satisfies a bound, and delisted houses are out
 *  unless asked for.
 *
 *  `extra` is the server's text-search hits (descriptions), unioned with the
 *  local address/buurt match so typing is instant and the rest arrive after. */
export function applyFilters(
  houses: readonly House[],
  search: BrowseSearch,
  extra?: ReadonlySet<number>
): House[] {
  const q = search.q?.toLowerCase()
  const labels = search.label ? new Set(search.label) : null
  const hoods = search.hood ? new Set(search.hood) : null
  return houses.filter(
    (h) =>
      (search.delisted === 1 || !h.delisted) &&
      (!q || h.haystack.includes(q) || (extra?.has(h.id) ?? false)) &&
      atLeast(h.price, search.price_min) &&
      atMost(h.price, search.price_max) &&
      atLeast(h.area, search.area_min) &&
      atMost(h.area, search.area_max) &&
      atLeast(h.rooms, search.rooms_min) &&
      atLeast(h.beds, search.beds_min) &&
      (!labels || (h.label !== null && labels.has(h.label))) &&
      (!hoods || hoods.has(h.hood)) &&
      (!search.status || h.status === search.status)
  )
}

const SORT_KEYS: Record<Sort, [(h: House) => number | null, 1 | -1]> = {
  // Funda's publication date, not first-seen: every house in a freshly built
  // cache shares one first-seen time, so that would order them randomly.
  newest: [(h) => h.published, -1],
  added: [(h) => h.firstSeen, -1],
  price_asc: [(h) => h.price, 1],
  price_desc: [(h) => h.price, -1],
  ppm2_asc: [(h) => h.pricePerM2, 1],
  area_desc: [(h) => h.area, -1],
}

/** Houses missing the sort value go last in either direction; the id breaks
 *  ties, so the order is total and a reload never reshuffles. */
export function sortHouses(
  houses: readonly House[],
  sort: Sort = DEFAULT_SORT
): House[] {
  const [key, direction] = SORT_KEYS[sort] ?? SORT_KEYS[DEFAULT_SORT]
  return houses.toSorted((a, b) => {
    const x = key(a)
    const y = key(b)
    if (x === null || y === null) {
      if (x !== y) return x === null ? 1 : -1
    } else if (x !== y) {
      return (x - y) * direction
    }
    return a.id - b.id
  })
}

// --- memory ----------------------------------------------------------------
// Filters are remembered per offering type: a €350 000 ceiling and a €1 800
// one are the same parameter meaning different things, so crossing them would
// silently empty the result set.

const MEMORY_KEY = (offering: Offering) => `housemaster-filters-v1:${offering}`

export function rememberFilters(search: BrowseSearch) {
  const keep: Record<string, unknown> = {}
  for (const key of FILTER_KEYS) keep[key] = search[key]
  try {
    const query = stringifySearch(keep)
    const key = MEMORY_KEY(search.offering ?? "buy")
    if (query) localStorage.setItem(key, query)
    else localStorage.removeItem(key)
  } catch {
    /* private mode: this visit is simply not remembered */
  }
}

export function recallFilters(offering: Offering): BrowseSearch | null {
  try {
    const stored = localStorage.getItem(MEMORY_KEY(offering))
    if (!stored) return null
    const search = validateSearch(parseSearch(stored))
    return hasFilters(search) ? search : null
  } catch {
    return null
  }
}
