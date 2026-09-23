import { describe, expect, it } from "vitest"

import {
  euro,
  localDate,
  perM2,
  publishedLabel,
  since,
  statusLabel,
} from "@/lib/format"

describe("format", () => {
  it("writes euros the way the CLI does", () => {
    expect(euro(289_500)).toBe("€ 289.500")
    expect(euro(null)).toBe("n/a")
  })

  it("says a rental's price per m² is monthly", () => {
    expect(perM2("rent")).toBe("/m² p/mnd")
    expect(perM2("buy")).toBe("/m²")
  })

  it("shows dates in Dutch local time", () => {
    // 06:30 UTC is 08:30 in Amsterdam in September.
    expect(localDate(Date.parse("2026-09-04T06:30:00Z") / 1000, true)).toBe(
      "2026-09-04 08:30"
    )
    expect(publishedLabel("2026-09-04T06:30:00+00:00")).toBe("2026-09-04 08:30")
  })

  it("keeps a bare date a date", () => {
    expect(publishedLabel("2026-09-04")).toBe("2026-09-04")
  })

  it("speaks recency", () => {
    const now = Date.parse("2026-09-23T12:00:00Z")
    expect(since(now / 1000 - 3600, now)).toBe("today")
    expect(since(now / 1000 - 86_400, now)).toBe("yesterday")
    expect(since(now / 1000 - 3 * 86_400, now)).toBe("3 days ago")
  })

  it("shows an unknown status rather than hiding it", () => {
    expect(statusLabel("under_bid")).toBe("Under offer")
    expect(statusLabel("none")).toBe("")
    expect(statusLabel("new_status")).toBe("New status")
  })
})
