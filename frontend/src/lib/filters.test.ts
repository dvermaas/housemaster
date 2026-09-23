/* The browser's filters replaced SQL, so they inherit its tests: every case
 * here used to be a test of `db._where`. */
import { describe, expect, it } from "vitest"

import type { House } from "@/lib/data"
import {
  applyFilters,
  hasFilters,
  parseSearch,
  sortHouses,
  stringifySearch,
  validateSearch,
} from "@/lib/filters"

const house = (id: number, over: Partial<House> = {}): House => {
  const h: House = {
    id,
    address: `Straat ${id}`,
    postalCode: "2500AA",
    city: "Den Haag",
    hood: "Centrum",
    price: 300_000,
    pricePerM2: 3_000,
    area: 100,
    rooms: 4,
    beds: 3,
    label: "C",
    status: "none",
    agent: "",
    published: 1_700_000_000 + id,
    firstSeen: 1_700_000_000,
    delisted: false,
    lat: 52,
    lng: 4.3,
    image: null,
    priceWas: null,
    haystack: "",
    ...over,
  }
  return { ...h, haystack: `${h.address}\n${h.hood}`.toLowerCase() }
}

const seeded = [
  house(1, {
    price: 260_000,
    area: 60,
    label: "E",
    hood: "Spoorwijk",
    rooms: 3,
    beds: 2,
    address: "Aaastraat 1",
  }),
  house(2, {
    price: 300_000,
    area: 100,
    label: "B",
    hood: "Centrum",
    rooms: 5,
    beds: 4,
    address: "Beeklaan 2",
  }),
  house(3, {
    price: 345_000,
    area: 80,
    label: "C",
    hood: "Spoorwijk",
    rooms: 4,
    beds: 3,
    address: "Cederlaan 3",
  }),
]

const ids = (houses: House[]) => houses.map((h) => h.id)

describe("applyFilters", () => {
  it.each([
    [{ price_max: 290_000 }, [1]],
    [{ price_min: 290_000 }, [2, 3]],
    [{ area_min: 80 }, [2, 3]],
    [{ rooms_min: 4 }, [2, 3]],
    [{ beds_min: 4 }, [2]],
    [{ label: ["B", "C"] }, [2, 3]],
    [{ hood: ["Spoorwijk"] }, [1, 3]],
    [{ q: "Beeklaan" }, [2]],
    [{ q: "spoorwijk" }, [1, 3]],
    [{ price_min: 290_000, area_min: 90 }, [2]],
  ])("%j matches %j", (search, expected) => {
    expect(ids(applyFilters(seeded, search))).toEqual(expected)
  })

  it("hides delisted houses unless asked", () => {
    const houses = [seeded[0], { ...seeded[1], delisted: true }]
    expect(ids(applyFilters(houses, {}))).toEqual([1])
    expect(ids(applyFilters(houses, { delisted: 1 }))).toEqual([1, 2])
  })

  it("never lets a missing value satisfy a bound", () => {
    // As in SQL: NULL >= 80 is not true.
    const houses = [house(1, { area: null }), house(2, { area: 90 })]
    expect(ids(applyFilters(houses, { area_min: 80 }))).toEqual([2])
    expect(ids(applyFilters(houses, { area_max: 100 }))).toEqual([2])
  })

  it("unions the server's description hits into text search", () => {
    expect(ids(applyFilters(seeded, { q: "tuin" }))).toEqual([])
    expect(ids(applyFilters(seeded, { q: "tuin" }, new Set([3])))).toEqual([3])
  })
})

describe("sortHouses", () => {
  it.each([
    ["price_asc", [1, 2, 3]],
    ["price_desc", [3, 2, 1]],
    ["area_desc", [2, 3, 1]],
  ] as const)("%s", (sort, expected) => {
    expect(ids(sortHouses(seeded, sort))).toEqual(expected)
  })

  it("puts houses without a value last in either direction", () => {
    const houses = [house(1, { price: null }), house(2, { price: 250_000 })]
    expect(ids(sortHouses(houses, "price_asc"))).toEqual([2, 1])
    expect(ids(sortHouses(houses, "price_desc"))).toEqual([2, 1])
  })

  it("breaks ties by id, so the order is total", () => {
    const houses = [3, 1, 2].map((id) => house(id, { published: 1 }))
    expect(ids(sortHouses(houses, "newest"))).toEqual([1, 2, 3])
  })
})

describe("the URL", () => {
  it("keeps repeated keys readable", () => {
    expect(stringifySearch({ label: ["A", "B"], view: "map" })).toBe(
      "?label=A&label=B&view=map"
    )
    expect(parseSearch("?label=A&label=B&q=x")).toEqual({
      label: ["A", "B"],
      q: "x",
    })
  })

  it("falls back on junk instead of crashing", () => {
    expect(
      validateSearch({
        price_min: "abc",
        sort: "'; DROP TABLE listings--",
        view: "3d",
      })
    ).toEqual({})
  })

  it("accepts Dutch thousands separators as typed", () => {
    expect(validateSearch({ price_max: "350.000" })).toEqual({
      price_max: 350_000,
    })
  })

  it("defaults to buy, and never stores the default sort", () => {
    expect(validateSearch({ offering: "lease", sort: "newest" })).toEqual({})
  })

  it("knows view and offering are not filters", () => {
    // Otherwise a bare `/?view=map` would never restore remembered filters.
    expect(hasFilters({ view: "map", offering: "rent" })).toBe(false)
    expect(hasFilters({ label: ["A"] })).toBe(true)
  })
})
