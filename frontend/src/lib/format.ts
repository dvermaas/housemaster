/* Formatting, matching the CLI's `render.euro` so both say the same thing. */
import type { Offering } from "@/lib/data"

const thousands = new Intl.NumberFormat("nl-NL", { maximumFractionDigits: 0 })

/** `€ 289.500`, or `n/a` -- a missing price is information, not zero. */
export const euro = (amount: number | null | undefined) =>
  amount ? `€ ${thousands.format(amount)}` : "n/a"

/** Thousands separator without the currency, for axis-style figures. */
export const compact = (value: number | null | undefined) =>
  value ? thousands.format(value) : "–"

/** Rent is stored monthly, so an unqualified "€ 19/m²" next to a purchase's
 *  "€ 5.629/m²" would read as an absurd bargain. The unit travels with it. */
export const perM2 = (offering: Offering) =>
  offering === "rent" ? "/m² p/mnd" : "/m²"

const STATUS_LABELS: Record<string, string> = {
  none: "",
  under_bid: "Under offer",
  sold_under_reservation: "Sold under reservation",
}

/** Human wording for funda's status vocabulary. Unseen values are shown, not
 *  hidden: the vocabulary is open, and dropping one would hide a real signal. */
export function statusLabel(status: string | null | undefined) {
  const key = (status ?? "").trim()
  if (key in STATUS_LABELS) return STATUS_LABELS[key]
  const words = key.replaceAll("_", " ")
  return words.charAt(0).toUpperCase() + words.slice(1)
}

// Dutch local time: a listing published at 08:30 in Amsterdam should say
// 08:30 wherever the viewer happens to be.
const dateFormat = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Europe/Amsterdam",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
})
const timeFormat = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Europe/Amsterdam",
  hour: "2-digit",
  minute: "2-digit",
})

/** `2026-09-04`, or with `withTime` `2026-09-04 08:30`. */
export function localDate(
  epochSeconds: number | null | undefined,
  withTime = false
) {
  if (!epochSeconds) return ""
  const moment = new Date(epochSeconds * 1000)
  return withTime
    ? `${dateFormat.format(moment)} ${timeFormat.format(moment)}`
    : dateFormat.format(moment)
}

/** ISO string from the detail API, as epoch seconds. A bare date (a row stored
 *  before clock times were kept) stays a date rather than gaining a `00:00`. */
export function publishedLabel(iso: string | null | undefined) {
  if (!iso) return ""
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? iso : localDate(ms / 1000, true)
}

const RECENT_DAYS = 30

/** `today` / `3 days ago` / a date. Recency is what matters when scanning. */
export function since(
  epochSeconds: number | null | undefined,
  now = Date.now()
) {
  if (!epochSeconds) return ""
  const days = Math.floor((now - epochSeconds * 1000) / 86_400_000)
  if (days <= 0) return "today"
  if (days === 1) return "yesterday"
  if (days < RECENT_DAYS) return `${days} days ago`
  return localDate(epochSeconds)
}

export const isoToEpoch = (iso: string | null | undefined) => {
  if (!iso) return null
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? null : ms / 1000
}

export const plural = (n: number, offering: Offering) =>
  offering === "rent"
    ? n === 1
      ? "rental"
      : "rentals"
    : n === 1
      ? "house"
      : "houses"
