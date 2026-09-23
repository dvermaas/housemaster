/* The map view: the same filtered set as the grid, drawn on MapLibre.
 *
 * Basemap tiles come from OpenFreeMap (no key, no account, no limits); every
 * marker is our own data, built in memory from the index -- a filter change
 * calls `setData()` on the live source and never touches the network.
 *
 * The one structural idea is the same as before the SPA: the map instance is
 * created once and kept. Only a theme change rebuilds it (see below), and the
 * camera is carried across that and across trips to the grid.
 */
import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import {
  Map as MapLibre,
  NavigationControl,
  Popup,
  ScaleControl,
} from "maplibre-gl"
import type {
  ExpressionSpecification,
  GeoJSONSource,
  LngLatBoundsLike,
} from "maplibre-gl"
import * as React from "react"
import { createPortal } from "react-dom"

import { useTheme } from "@/components/theme-provider"
import { Badge } from "@/components/ui/badge"
import { ENERGY_SCALE, EnergyLabel } from "@/components/ui/energy-label"
import { Toggle } from "@/components/ui/toggle"
import { type House, photoUrl, type Shapes, shapesQuery } from "@/lib/data"
import { compact, euro, perM2, plural, statusLabel } from "@/lib/format"
import { basemap, tokenColour } from "@/lib/maplibre"
import type { useBrowse } from "@/lib/use-browse"

const DEN_HAAG: [number, number] = [4.3007, 52.0705]
const HOODS_KEY = "housemaster-hoods"
const LEGEND = ["A", "B", "C", "D", "E", "F", "G"] as const

type Camera = {
  center: [number, number]
  zoom: number
  bearing: number
  pitch: number
}

/** Where you left the map, kept for the session: switching to the grid and
 *  back should not throw you out to a fit-all view again. */
let lastCamera: Camera | null = null

const tokenNumber = (name: string, fallback: number) =>
  Number(getComputedStyle(document.documentElement).getPropertyValue(name)) ||
  fallback

const STEP_TOKEN: Record<string, string> = {
  "A+++++": "a5", "A++++": "a4", "A+++": "a3", "A++": "a2", "A+": "a1",
  A: "a", B: "b", C: "c", D: "d", E: "e", F: "f", G: "g",
} // prettier-ignore

/** Marker colour by energy label, from the same tokens the chips use. */
function energyPaint(): ExpressionSpecification {
  const pairs = ENERGY_SCALE.flatMap((label) => [
    label,
    tokenColour(`--energy-${STEP_TOKEN[label]}`),
  ])
  return [
    "match",
    ["get", "label"],
    ...pairs,
    tokenColour("--energy-unknown"),
  ] as unknown as ExpressionSpecification
}

/** Buurt fill by funda's price level. A `step` over quantile edges, not an
 *  `interpolate`: Den Haag's buurt prices are skewed enough that an even ramp
 *  draws as one flat wash. See db.neighbourhood_price_scale. */
function rampPaint(scale: number[] | null): ExpressionSpecification | string {
  const colours = [1, 2, 3, 4, 5].map((i) => tokenColour(`--choro-${i}`))
  const missing = tokenColour("--choro-none")
  const edges = scale ? scale.slice(1, -1) : []
  if (!edges.length) return colours[2]
  return [
    "case",
    ["==", ["get", "price_m2"], null],
    missing,
    [
      "step",
      ["to-number", ["get", "price_m2"]],
      colours[0],
      ...edges.flatMap((edge, i) => [edge, colours[i + 1]]),
    ],
  ] as ExpressionSpecification
}

function toGeoJSON(houses: House[]): GeoJSON.FeatureCollection<GeoJSON.Point> {
  const features: GeoJSON.Feature<GeoJSON.Point>[] = []
  for (const h of houses) {
    if (h.lat === null || h.lng === null) continue
    features.push({
      type: "Feature",
      id: h.id,
      // GeoJSON is lng,lat -- the opposite order to how the rest of the app says it.
      geometry: { type: "Point", coordinates: [h.lng, h.lat] },
      properties: { id: h.id, label: h.label ?? "?" },
    })
  }
  return { type: "FeatureCollection", features }
}

function boundsOf(
  data: GeoJSON.FeatureCollection<GeoJSON.Point>
): LngLatBoundsLike | null {
  if (!data.features.length) return null
  let [x1, y1, x2, y2] = [Infinity, Infinity, -Infinity, -Infinity]
  for (const { geometry } of data.features) {
    const [x, y] = geometry.coordinates
    x1 = Math.min(x1, x)
    y1 = Math.min(y1, y)
    x2 = Math.max(x2, x)
    y2 = Math.max(y2, y)
  }
  return [
    [x1, y1],
    [x2, y2],
  ]
}

function readHoodsWanted() {
  try {
    return localStorage.getItem(HOODS_KEY) === "1"
  } catch {
    return false
  }
}

export default function MapView({
  browse,
}: {
  browse: ReturnType<typeof useBrowse>
}) {
  const { matched, index, offering } = browse
  const meta = index.data?.meta
  const hasOutlines = (meta?.counts.boundaries ?? 0) > 0
  const { resolvedTheme } = useTheme()

  const container = React.useRef<HTMLDivElement>(null)
  const mapRef = React.useRef<MapLibre | null>(null)
  const [ready, setReady] = React.useState(false)
  const [selected, setSelected] = React.useState<House | null>(null)
  const [hovered, setHovered] = React.useState<{
    name: string
    price: number | null
  } | null>(null)
  const [hoodsWanted, setHoodsWanted] = React.useState(readHoodsWanted)
  // A remembered "on" must not survive into a cache with no outlines yet.
  const hoodsOn = hoodsWanted && hasOutlines

  const shapes = useQuery({ ...shapesQuery, enabled: hasOutlines })
  const geojson = React.useMemo(() => toGeoJSON(matched), [matched])

  // The load handler runs once per instance but needs the latest of these.
  const latest = React.useRef({
    geojson,
    shapes: shapes.data,
    scale: meta?.hoodScale ?? null,
    hoodsOn,
    byId: index.data?.byId,
  })
  React.useEffect(() => {
    latest.current = {
      geojson,
      shapes: shapes.data,
      scale: meta?.hoodScale ?? null,
      hoodsOn,
      byId: index.data?.byId,
    }
  })

  // --- the instance -------------------------------------------------------
  //
  // Rebuilt, not restyled, when the theme changes. `setStyle()` discards our
  // layers and MapLibre gives no reliable moment to put them back -- a re-add
  // on `styledata` can land on the outgoing style and the houses silently
  // vanish. A rebuild has one code path, the same one first paint uses, and
  // carrying the camera over makes it invisible.
  React.useEffect(() => {
    const node = container.current
    if (!node) return undefined
    const camera = lastCamera
    const map = new MapLibre({
      container: node,
      style: basemap(resolvedTheme),
      center: camera?.center ?? DEN_HAAG,
      zoom: camera?.zoom ?? 12,
      bearing: camera?.bearing ?? 0,
      pitch: camera?.pitch ?? 0,
      attributionControl: { compact: true },
    })
    mapRef.current = map
    map.addControl(new NavigationControl({ showCompass: false }), "top-right")
    map.addControl(
      new ScaleControl({ maxWidth: 90, unit: "metric" }),
      "bottom-right"
    )

    map.on("load", () => {
      // Nudge the basemap toward the UI's own background, so the houses are
      // the only saturated thing on screen. Guarded: the layer ids are the
      // provider's, and a rename upstream must not break the map.
      try {
        if (map.getLayer("background")) {
          map.setPaintProperty(
            "background",
            "background-color",
            tokenColour("--background")
          )
        }
      } catch {
        /* the default style still works */
      }

      const { geojson: data, shapes: outlines, scale } = latest.current
      // Outlines first, so the house markers always sit on top of the wash.
      map.addSource("hoods", {
        type: "geojson",
        data: outlines ?? { type: "FeatureCollection", features: [] },
      })
      const visibility = latest.current.hoodsOn ? "visible" : "none"
      map.addLayer({
        id: "hoods-fill",
        type: "fill",
        source: "hoods",
        layout: { visibility },
        paint: {
          "fill-color": rampPaint(scale),
          "fill-opacity": [
            "case",
            ["boolean", ["feature-state", "hover"], false],
            tokenNumber("--choro-fill-hover", 0.6),
            tokenNumber("--choro-fill", 0.4),
          ],
        },
      })
      map.addLayer({
        id: "hoods-line",
        type: "line",
        source: "hoods",
        layout: { visibility, "line-join": "round" },
        paint: {
          "line-color": tokenColour("--choro-5"),
          "line-width": [
            "case",
            ["boolean", ["feature-state", "hover"], false],
            2,
            0.8,
          ],
          "line-opacity": 0.7,
        },
      })

      map.addSource("houses", { type: "geojson", data })
      map.addLayer({
        id: "houses-halo",
        type: "circle",
        source: "houses",
        paint: {
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["zoom"],
            10,
            5,
            14,
            9,
            17,
            14,
          ],
          "circle-color": tokenColour("--background"),
          "circle-opacity": 0.9,
        },
      })
      map.addLayer({
        id: "houses",
        type: "circle",
        source: "houses",
        paint: {
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["zoom"],
            10,
            3.2,
            14,
            6,
            17,
            10,
          ],
          "circle-color": energyPaint(),
          "circle-stroke-width": 1,
          "circle-stroke-color": tokenColour("--foreground"),
          "circle-stroke-opacity": 0.55,
        },
      })

      // Fit to the data only on a first visit: refitting on every filter
      // change would yank the map away from wherever you had panned.
      if (!camera) {
        const bounds = boundsOf(data)
        if (bounds)
          map.fitBounds(bounds, { padding: 56, maxZoom: 15, animate: false })
      }
      setReady(true)
    })

    map.on("click", "houses", (event) => {
      const id = event.features?.[0]?.properties?.id as number | undefined
      const house = id !== undefined ? latest.current.byId?.get(id) : undefined
      if (house) setSelected(house)
    })
    map.on("mouseenter", "houses", () => {
      map.getCanvas().style.cursor = "pointer"
    })
    map.on("mouseleave", "houses", () => {
      map.getCanvas().style.cursor = ""
    })

    let hoverId: string | number | undefined
    const clearHover = () => {
      if (hoverId !== undefined && map.getSource("hoods")) {
        map.setFeatureState({ source: "hoods", id: hoverId }, { hover: false })
      }
      hoverId = undefined
    }
    map.on("mousemove", "hoods-fill", (event) => {
      const feature = event.features?.[0]
      if (!feature || feature.id === hoverId) return
      clearHover()
      hoverId = feature.id
      if (hoverId !== undefined)
        map.setFeatureState({ source: "hoods", id: hoverId }, { hover: true })
      setHovered({
        name: feature.properties.name as string,
        price: (feature.properties.price_m2 as number | null) ?? null,
      })
    })
    map.on("mouseleave", "hoods-fill", () => {
      clearHover()
      setHovered(null)
    })

    return () => {
      const center = map.getCenter()
      lastCamera = {
        center: [center.lng, center.lat],
        zoom: map.getZoom(),
        bearing: map.getBearing(),
        pitch: map.getPitch(),
      }
      setReady(false)
      setSelected(null)
      mapRef.current = null
      // Also frees the WebGL context, which browsers only hand out a few of.
      map.remove()
    }
  }, [resolvedTheme])

  // --- data into the live instance -----------------------------------------

  React.useEffect(() => {
    if (!ready) return
    mapRef.current?.getSource<GeoJSONSource>("houses")?.setData(geojson)
  }, [geojson, ready])

  React.useEffect(() => {
    if (!ready || !shapes.data) return
    mapRef.current
      ?.getSource<GeoJSONSource>("hoods")
      ?.setData(shapes.data as Shapes)
  }, [shapes.data, ready])

  React.useEffect(() => {
    const map = mapRef.current
    if (!ready || !map) return
    for (const id of ["hoods-fill", "hoods-line"]) {
      if (map.getLayer(id))
        map.setLayoutProperty(id, "visibility", hoodsOn ? "visible" : "none")
    }
  }, [hoodsOn, ready])

  const toggleHoods = () => {
    const next = !hoodsWanted
    setHoodsWanted(next)
    try {
      localStorage.setItem(HOODS_KEY, next ? "1" : "0")
    } catch {
      /* private mode: the toggle still works for this visit */
    }
  }

  // --- the popup ------------------------------------------------------------

  const [popupNode] = React.useState(() => document.createElement("div"))
  React.useEffect(() => {
    const map = mapRef.current
    if (!map || !selected || selected.lat === null || selected.lng === null)
      return undefined
    const popup = new Popup({
      closeButton: true,
      maxWidth: "300px",
      offset: 12,
      className: "housepop",
    })
      .setLngLat([selected.lng, selected.lat])
      .setDOMContent(popupNode)
      .addTo(map)
    popup.on("close", () =>
      setSelected((current) => (current === selected ? null : current))
    )
    return () => {
      popup.remove()
    }
  }, [selected, popupNode])

  // --- chrome ---------------------------------------------------------------

  const mapped = geojson.features.length
  const scale = meta?.hoodScale

  return (
    <div className="relative min-h-96 flex-1">
      {/* MapLibre sets `position: relative` on its container, so the
          absolute positioning has to live on a wrapper. */}
      <div className="absolute inset-0">
        <div
          ref={container}
          className="size-full"
          aria-label="Map of matching houses"
        />
      </div>

      {/* A map that quietly omits results is worse than one that admits it.
          Coordinates arrive only with the detail page, so a cache that is
          still enriching can plot a fraction of what matched. */}
      {mapped < matched.length && (
        <div className="pointer-events-none absolute inset-x-0 top-3 flex justify-center">
          <Badge variant="secondary" className="pointer-events-auto">
            <span className="font-mono">{mapped}</span> of{" "}
            <span className="font-mono">{matched.length}</span>{" "}
            {plural(matched.length, offering)} have coordinates. The rest arrive
            as <code>fetch</code> enriches them.
          </Badge>
        </div>
      )}

      {hoodsOn && hovered && (
        <div className="pointer-events-none absolute top-3 left-3 rounded-lg bg-popover px-3 py-1.5 text-sm text-popover-foreground shadow-md ring-1 ring-foreground/10">
          {hovered.name}
          {hovered.price ? (
            <span className="ml-2 font-mono text-muted-foreground">
              {euro(hovered.price)}/m²
            </span>
          ) : null}
        </div>
      )}

      <div className="absolute bottom-8 left-3 flex flex-col items-start gap-2">
        {/* The wrapper carries the background: the map is behind it, and a
            translucent control over a busy basemap does not read. */}
        <div className="rounded-lg bg-background shadow-sm">
          <Toggle
            variant="outline"
            size="sm"
            pressed={hoodsOn}
            disabled={!hasOutlines}
            title={
              hasOutlines
                ? "Buurt outlines, coloured by funda's asking price per m²"
                : "No buurt outlines cached yet. `housemaster fetch` collects them last."
            }
            onPressedChange={toggleHoods}
          >
            Buurten
            {/* The legend lives inside the button, so turning it on changes the
              button's width and nothing above it has to move. */}
            {hoodsOn && scale && (
              <span className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
                <span>€/m²</span>
                <span>{compact(scale[0])}</span>
                <span
                  className="flex h-2 overflow-hidden rounded-sm"
                  title="Equal-count bins: about a fifth of buurten per step"
                >
                  <span className="w-3 bg-choro-1" />
                  <span className="w-3 bg-choro-2" />
                  <span className="w-3 bg-choro-3" />
                  <span className="w-3 bg-choro-4" />
                  <span className="w-3 bg-choro-5" />
                </span>
                <span>{compact(scale[scale.length - 1])}</span>
              </span>
            )}
          </Toggle>
        </div>
        {offering === "rent" && hoodsOn && (
          <p className="max-w-64 rounded-md bg-background/90 px-2 py-1 text-xs text-muted-foreground shadow-sm">
            Funda only publishes purchase prices per buurt, so this is not a
            rent benchmark.
          </p>
        )}
        <div className="flex items-center gap-1 rounded-lg bg-background/90 px-2 py-1.5 shadow-sm ring-1 ring-foreground/10">
          <span className="mr-1 text-xs text-muted-foreground">Energy</span>
          {LEGEND.map((label) => (
            <EnergyLabel key={label} label={label} size="sm" />
          ))}
        </div>
      </div>

      {selected &&
        createPortal(
          <PopupCard house={selected} offering={offering} />,
          popupNode
        )}
    </div>
  )
}

/** The card shown when a marker is clicked. Same vocabulary as a grid card,
 *  sized for a popup, rendered from the index -- no request. */
function PopupCard({
  house,
  offering,
}: {
  house: House
  offering: "buy" | "rent"
}) {
  const status =
    house.status && house.status !== "none" ? statusLabel(house.status) : ""
  return (
    <Link
      to="/house/$id"
      params={{ id: house.id }}
      className="flex w-64 flex-col gap-2 text-popover-foreground"
    >
      {house.image && (
        <img
          src={photoUrl(house.image, 464)}
          alt=""
          referrerPolicy="no-referrer"
          className="aspect-3/2 w-full rounded-md object-cover"
        />
      )}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-medium">{house.address}</div>
          <div className="truncate text-xs text-muted-foreground">
            {house.hood || house.city}
          </div>
        </div>
        <EnergyLabel label={house.label} size="sm" />
      </div>
      <div className="flex items-baseline gap-2 font-mono text-sm">
        <span className="font-semibold">{euro(house.price)}</span>
        {house.priceWas && (
          <span className="text-xs text-muted-foreground line-through">
            {euro(house.priceWas)}
          </span>
        )}
        <span className="ml-auto text-xs text-muted-foreground">
          {euro(house.pricePerM2)}
          {perM2(offering)}
        </span>
      </div>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span>{house.area ?? "–"} m²</span>·
        <span>{house.rooms ?? "–"} rooms</span>·
        <span>{house.beds ?? "–"} bed</span>
        {status && (
          <Badge variant="secondary" className="ml-auto">
            {status}
          </Badge>
        )}
      </div>
    </Link>
  )
}
