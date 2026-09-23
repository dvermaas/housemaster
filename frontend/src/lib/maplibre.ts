/* MapLibre setup shared by the map view and the detail page's small map.
 *
 * Only ever imported from lazily loaded modules, so MapLibre stays out of
 * the first load.
 */
import "maplibre-gl/dist/maplibre-gl.css"

import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url"
import { setWorkerUrl } from "maplibre-gl"

// MapLibre 6 finds its worker relative to its own module, which a bundler
// renames. Vite bundles the worker (and the chunk it shares with the main
// library) as its own file and hands back where it put it.
setWorkerUrl(workerUrl)

/** OpenFreeMap basemap tiles: no key, no account, no limits. */
export const basemap = (theme: "light" | "dark") =>
  `https://tiles.openfreemap.org/styles/${theme === "dark" ? "dark" : "positron"}`

/** A CSS custom property as `rgb()`. Theme tokens are oklch, which MapLibre's
 *  colour parser does not read, so the browser does the conversion: paint one
 *  pixel with it and read the pixel back. */
export function tokenColour(name: string): string {
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim()
  const canvas = document.createElement("canvas")
  canvas.width = canvas.height = 1
  const ctx = canvas.getContext("2d", { willReadFrequently: true })
  if (!ctx || !value) return "#888888"
  ctx.fillStyle = value
  ctx.fillRect(0, 0, 1, 1)
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data
  return `rgb(${r}, ${g}, ${b})`
}
