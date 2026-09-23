// Generated from src/housemaster/web/api.py -- do not edit.
// Regenerate: uv run python -m housemaster.web.typescript frontend/src/lib/api.gen.ts

export type Offering =
  | "buy"
  | "rent"

export type IndexColumn =
  | "listing_id"
  | "address"
  | "postal_code"
  | "city"
  | "neighbourhood"
  | "price"
  | "price_per_m2"
  | "living_area"
  | "rooms"
  | "bedrooms"
  | "energy_label"
  | "status"
  | "agent"
  | "published"
  | "first_seen_at"
  | "delisted_at"
  | "lat"
  | "lng"
  | "first_image"
  | "price_was"

export type Counts = {
  total: number
  active: number
  enriched: number
  boundaries: number
}

export type IndexMeta = {
  counts: Counts
  last_run: number | null
  hood_scale: number[] | null
}

export type IndexPayload = {
  offering: Offering
  columns: IndexColumn[]
  rows: unknown[][]
  meta: IndexMeta
}

export type SearchPayload = {
  q: string
  ids: number[]
}

export type Listing = {
  listing_id: number
  tiny_id: string | null
  url: string
  address: string
  postal_code: string | null
  city: string | null
  neighbourhood: string | null
  price: number | null
  price_condition: string | null
  living_area: number | null
  rooms: number | null
  bedrooms: number | null
  energy_label: string | null
  object_type: string | null
  construction_type: string | null
  status: string | null
  published: string | null
  agent: string | null
  photo_count: number
  price_per_m2: number | null
  description: string | null
  lat: number | null
  lng: number | null
  neighbourhood_price_m2: number | null
  neighbourhood_inhabitants: number | null
  detail_fetched_at: string | null
  first_seen_at: string
  last_seen_at: string
  missed_runs: number
  delisted_at: string | null
  offering_type: Offering
  first_image: string | null
  price_was: number | null
}

export type Feature = {
  group: string
  label: string
  value: string
}

export type HistoryRow = {
  id: number
  observed_at: string
  price: number | null
  status: string | null
}

export type HousePayload = {
  listing: Listing
  features: Feature[]
  photos: string[]
  history: HistoryRow[]
}

export type ShapeProperties = {
  name: string
  price_m2: number | null
}

export type ShapeFeature = {
  type: "Feature"
  id: number
  geometry: Record<string, unknown>
  properties: ShapeProperties
}

export type ShapesPayload = {
  type: "FeatureCollection"
  features: ShapeFeature[]
}

export type ErrorPayload = {
  error: string
}
