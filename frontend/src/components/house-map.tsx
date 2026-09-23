/* The detail page's small map: where this one house is, and nothing else.
 *
 * Lazy, like the map view, so MapLibre stays out of the first load. Not
 * interactive -- a map that grabs the scroll wheel halfway down a page is a
 * trap. The big map is one click away for exploring.
 */
import { Map as MapLibre } from "maplibre-gl"
import * as React from "react"

import { useTheme } from "@/components/theme-provider"
import { basemap, tokenColour } from "@/lib/maplibre"

const ZOOM = 14.5

export default function HouseMap({ lat, lng }: { lat: number; lng: number }) {
  const container = React.useRef<HTMLDivElement>(null)
  const { resolvedTheme } = useTheme()

  // Rebuilt on a theme change, as the map view is: setStyle() drops our layer.
  React.useEffect(() => {
    const node = container.current
    if (!node) return undefined
    const map = new MapLibre({
      container: node,
      style: basemap(resolvedTheme),
      center: [lng, lat],
      zoom: ZOOM,
      interactive: false,
      attributionControl: { compact: true },
    })
    map.on("load", () => {
      // The compact attribution opens expanded until the first interaction,
      // which on a map without interaction is forever. Keep it to its (i).
      node
        .querySelector(".maplibregl-ctrl-attrib")
        ?.classList.remove("maplibregl-compact-show")
      map.addSource("house", {
        type: "geojson",
        data: { type: "Point", coordinates: [lng, lat] },
      })
      map.addLayer({
        id: "house-halo",
        type: "circle",
        source: "house",
        paint: {
          "circle-radius": 20,
          "circle-color": tokenColour("--primary"),
          "circle-opacity": 0.15,
        },
      })
      map.addLayer({
        id: "house",
        type: "circle",
        source: "house",
        paint: {
          "circle-radius": 7,
          "circle-color": tokenColour("--primary"),
          "circle-stroke-width": 3,
          "circle-stroke-color": tokenColour("--background"),
        },
      })
    })
    return () => map.remove()
  }, [lat, lng, resolvedTheme])

  return (
    // MapLibre sets `position: relative` on its container; the wrapper owns
    // the size.
    <div className="aspect-4/3 w-full overflow-hidden rounded-xl border">
      <div ref={container} className="size-full" />
    </div>
  )
}
