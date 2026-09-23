/* The browse index: one offering type's every house, held in memory.
 *
 * This is what makes the app fast. The server hands over the whole index once
 * (a few thousand rows, ~450 KB gzipped); filtering, sorting and mapping then
 * run here, in a few milliseconds, with no request at all.
 *
 * It is also kept in IndexedDB, so a return visit renders from disk before the
 * network is even asked -- then revalidates with the stored ETag, which the
 * server almost always answers with an empty 304.
 */
import { get, set } from "idb-keyval"

import type {
  Counts,
  HousePayload,
  IndexColumn,
  IndexPayload,
  Offering as WireOffering,
  SearchPayload,
  ShapeProperties,
} from "@/lib/api.gen"

export const OFFERINGS = ["buy", "rent"] as const satisfies WireOffering[]
export type Offering = (typeof OFFERINGS)[number]

export type House = {
  id: number
  address: string
  postalCode: string
  city: string
  hood: string
  /** Euros to buy, euros per month to rent. Never compare across the two. */
  price: number | null
  pricePerM2: number | null
  area: number | null
  rooms: number | null
  beds: number | null
  label: string | null
  status: string
  agent: string
  /** Epoch seconds. */
  published: number | null
  firstSeen: number | null
  /** Stopped matching every tracked search. Not the same as sold. */
  delisted: boolean
  lat: number | null
  lng: number | null
  image: string | null
  /** The highest earlier price, when the house has come down since. */
  priceWas: number | null
  /** Lower-cased address and buurt: what text search matches locally. */
  haystack: string
}

export type IndexMeta = {
  counts: Counts
  lastRun: number | null
  /** Quantile edges for the buurt choropleth: (lo, b1..b4, hi). */
  hoodScale: number[] | null
}

export type Facets = {
  labelCounts: Map<string, number>
  hoods: string[]
  priceRange: [number, number]
  areaRange: [number, number]
}

export type BrowseIndex = {
  offering: Offering
  houses: House[]
  byId: Map<number, House>
  meta: IndexMeta
  facets: Facets
}

type Wire = IndexPayload

type Stored = { etag: string; wire: Wire }

const STORE_KEY = (offering: Offering) => `housemaster-index-v1:${offering}`

function decode(wire: Wire): BrowseIndex {
  const at = (name: IndexColumn) => {
    const index = wire.columns.indexOf(name)
    if (index < 0) throw new Error(`index is missing column ${name}`)
    return index
  }
  // Resolved once, not per row: 5 000 rows times 20 lookups adds up.
  const c = {
    id: at("listing_id"),
    address: at("address"),
    postalCode: at("postal_code"),
    city: at("city"),
    hood: at("neighbourhood"),
    price: at("price"),
    pricePerM2: at("price_per_m2"),
    area: at("living_area"),
    rooms: at("rooms"),
    beds: at("bedrooms"),
    label: at("energy_label"),
    status: at("status"),
    agent: at("agent"),
    published: at("published"),
    firstSeen: at("first_seen_at"),
    delisted: at("delisted_at"),
    lat: at("lat"),
    lng: at("lng"),
    image: at("first_image"),
    priceWas: at("price_was"),
  }

  const houses: House[] = wire.rows.map((r) => {
    const address = (r[c.address] as string) ?? ""
    const hood = (r[c.hood] as string) ?? ""
    return {
      id: r[c.id] as number,
      address,
      postalCode: (r[c.postalCode] as string) ?? "",
      city: (r[c.city] as string) ?? "",
      hood,
      price: r[c.price] as number | null,
      pricePerM2: r[c.pricePerM2] as number | null,
      area: r[c.area] as number | null,
      rooms: r[c.rooms] as number | null,
      beds: r[c.beds] as number | null,
      label: (r[c.label] as string | null) || null,
      status: (r[c.status] as string) ?? "",
      agent: (r[c.agent] as string) ?? "",
      published: r[c.published] as number | null,
      firstSeen: r[c.firstSeen] as number | null,
      delisted: r[c.delisted] === 1,
      lat: r[c.lat] as number | null,
      lng: r[c.lng] as number | null,
      image: r[c.image] as string | null,
      priceWas: r[c.priceWas] as number | null,
      haystack: `${address}\n${hood}`.toLowerCase(),
    }
  })

  return {
    offering: wire.offering,
    houses,
    byId: new Map(houses.map((h) => [h.id, h])),
    meta: {
      counts: wire.meta.counts,
      lastRun: wire.meta.last_run,
      hoodScale: wire.meta.hood_scale,
    },
    facets: facetsOf(houses),
  }
}

/** Ranges and counts for the filter rail, over houses still on the market --
 *  a delisted outlier should not stretch a slider. */
function facetsOf(houses: House[]): Facets {
  const labelCounts = new Map<string, number>()
  const hoods = new Set<string>()
  let priceLo = Infinity
  let priceHi = 0
  let areaLo = Infinity
  let areaHi = 0
  for (const h of houses) {
    if (h.delisted) continue
    if (h.label) labelCounts.set(h.label, (labelCounts.get(h.label) ?? 0) + 1)
    if (h.hood) hoods.add(h.hood)
    if (h.price && h.price > 0) {
      priceLo = Math.min(priceLo, h.price)
      priceHi = Math.max(priceHi, h.price)
    }
    if (h.area && h.area > 0) {
      areaLo = Math.min(areaLo, h.area)
      areaHi = Math.max(areaHi, h.area)
    }
  }
  return {
    labelCounts,
    hoods: [...hoods].toSorted((a, b) => a.localeCompare(b, "nl")),
    priceRange: [Number.isFinite(priceLo) ? priceLo : 0, priceHi],
    areaRange: [Number.isFinite(areaLo) ? areaLo : 0, areaHi],
  }
}

async function readStored(offering: Offering): Promise<Stored | undefined> {
  try {
    return await get<Stored>(STORE_KEY(offering))
  } catch {
    return undefined // private mode, or storage cleared: just go to the network
  }
}

/** The on-disk copy, if any, without touching the network. */
export async function loadCachedIndex(
  offering: Offering
): Promise<BrowseIndex | undefined> {
  const stored = await readStored(offering)
  return stored ? decode(stored.wire) : undefined
}

/** Revalidate against the server. A 304 reuses the stored copy. */
export async function fetchIndex(offering: Offering): Promise<BrowseIndex> {
  const stored = await readStored(offering)
  const response = await fetch(`/api/index/${offering}`, {
    headers: stored ? { "If-None-Match": `"${stored.etag}"` } : {},
  })
  if (response.status === 304 && stored) return decode(stored.wire)
  if (!response.ok) throw new Error(`index: HTTP ${response.status}`)

  const wire = (await response.json()) as Wire
  const etag = (response.headers.get("ETag") ?? "").replaceAll('"', "")
  // Not awaited: the page should not wait on a disk write it does not need.
  set(STORE_KEY(offering), { etag, wire } satisfies Stored).catch(() => {})
  return decode(wire)
}

export const indexQuery = (offering: Offering) => ({
  queryKey: ["index", offering] as const,
  queryFn: () => fetchIndex(offering),
  staleTime: 60_000,
})

// --- the two things the index cannot answer --------------------------------

export async function fetchSearch(
  offering: Offering,
  q: string
): Promise<Set<number>> {
  const response = await fetch(
    `/api/search/${offering}?q=${encodeURIComponent(q)}`
  )
  if (!response.ok) throw new Error(`search: HTTP ${response.status}`)
  const body = (await response.json()) as SearchPayload
  return new Set(body.ids)
}

/** Generated from the server's own definition; see `src/housemaster/web/api.py`. */
export type HouseDetail = HousePayload

export const houseQuery = (id: number) => ({
  queryKey: ["house", id] as const,
  queryFn: async (): Promise<HouseDetail> => {
    const response = await fetch(`/api/house/${id}`)
    if (response.status === 404) throw new NotFound()
    if (!response.ok) throw new Error(`house: HTTP ${response.status}`)
    return (await response.json()) as HouseDetail
  },
  staleTime: 5 * 60_000,
})

export class NotFound extends Error {}

export type Shapes = GeoJSON.FeatureCollection<
  GeoJSON.Geometry,
  ShapeProperties
>

export const shapesQuery = {
  queryKey: ["shapes"] as const,
  queryFn: async (): Promise<Shapes> => {
    const response = await fetch("/api/neighbourhoods.geojson")
    if (!response.ok) throw new Error(`shapes: HTTP ${response.status}`)
    return (await response.json()) as Shapes
  },
  staleTime: Infinity,
}

// --- photos ----------------------------------------------------------------

/** Funda's CDN width ladder. Anything else rounds up to one of these. */
export const PHOTO_WIDTHS = [228, 464, 720, 1080, 1440] as const

/** Photos are hotlinked, never downloaded: the id is a complete CDN path. */
export function photoUrl(
  imageId: string,
  width: (typeof PHOTO_WIDTHS)[number] | null = 720
) {
  const base = `https://cloud.funda.nl/${imageId.replace(/^\/+/, "")}`
  return width ? `${base}?options=width=${width}` : base
}

export function photoSrcSet(
  imageId: string,
  widths: readonly (typeof PHOTO_WIDTHS)[number][]
) {
  return widths.map((w) => `${photoUrl(imageId, w)} ${w}w`).join(", ")
}
