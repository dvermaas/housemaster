import { useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { ImageOffIcon } from "lucide-react"
import * as React from "react"

import { Badge } from "@/components/ui/badge"
import { EnergyLabel } from "@/components/ui/energy-label"
import {
  type House,
  houseQuery,
  type Offering,
  photoSrcSet,
  photoUrl,
} from "@/lib/data"
import { euro, localDate, perM2, statusLabel } from "@/lib/format"

export const HouseCard = React.memo(function HouseCard({
  house,
  offering,
  areaRange,
  eager = false,
}: {
  house: House
  offering: Offering
  areaRange: [number, number]
  eager?: boolean
}) {
  const queryClient = useQueryClient()
  const status =
    house.status && house.status !== "none" ? statusLabel(house.status) : ""

  return (
    <Link
      to="/house/$id"
      params={{ id: house.id }}
      className="group/house flex flex-col gap-3 rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
      // Also warmed by the router's intent preload; this one covers touch,
      // where there is no hover to preload on.
      onFocus={() => void queryClient.prefetchQuery(houseQuery(house.id))}
    >
      <div className="relative aspect-3/2 overflow-hidden rounded-xl bg-muted">
        {house.image ? (
          <img
            src={photoUrl(house.image, 464)}
            srcSet={photoSrcSet(house.image, [464, 720, 1080])}
            sizes="(min-width: 1536px) 25vw, (min-width: 1280px) 33vw, (min-width: 640px) 50vw, 100vw"
            alt=""
            loading={eager ? "eager" : "lazy"}
            fetchPriority={eager ? "high" : "auto"}
            decoding="async"
            referrerPolicy="no-referrer"
            className="size-full object-cover transition-transform duration-300 group-hover/house:scale-102"
          />
        ) : (
          <div className="grid size-full place-items-center text-muted-foreground">
            <ImageOffIcon />
          </div>
        )}
        <div className="absolute top-2 left-2 flex flex-wrap gap-1">
          {house.delisted && <Badge variant="secondary">Left search</Badge>}
          {house.priceWas && <Badge>Price down</Badge>}
          {status && <Badge variant="secondary">{status}</Badge>}
        </div>
      </div>

      <div className="flex flex-col gap-1 px-0.5">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="truncate font-medium">{house.address}</div>
            <div className="truncate text-sm text-muted-foreground">
              {house.hood || house.city}
            </div>
          </div>
          <EnergyLabel label={house.label} />
        </div>

        <div className="flex items-baseline gap-2 font-mono text-sm">
          <span className="font-semibold">{euro(house.price)}</span>
          {house.priceWas && (
            <span className="text-muted-foreground line-through">
              {euro(house.priceWas)}
            </span>
          )}
          <span className="ml-auto text-xs text-muted-foreground">
            {euro(house.pricePerM2)}
            {perM2(offering)}
          </span>
        </div>

        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <AreaRuler area={house.area} range={areaRange} />
          <span>·</span>
          <span>{house.rooms ?? "–"} rooms</span>
          <span>·</span>
          <span>{house.beds ?? "–"} bed</span>
          <span className="ml-auto">{localDate(house.published)}</span>
        </div>
      </div>
    </Link>
  )
})

/** This house's size within the whole cache's range, the way a floor plan
 *  carries a scale bar under its figures. */
function AreaRuler({
  area,
  range,
}: {
  area: number | null
  range: [number, number]
}) {
  const [lo, hi] = range
  const pct =
    area && hi > lo
      ? Math.max(4, Math.round(((area - lo) * 100) / (hi - lo)))
      : null
  return (
    <span
      className="inline-flex items-center gap-1.5"
      title={
        pct ? `${area} m², where this search runs ${lo}–${hi} m²` : undefined
      }
    >
      <span className="font-mono text-foreground">{area ?? "–"} m²</span>
      {pct !== null && (
        <span className="h-1 w-8 overflow-hidden rounded-full bg-muted">
          <span
            className="block h-full w-(--pct) rounded-full bg-foreground/40"
            style={{ "--pct": `${pct}%` } as React.CSSProperties}
          />
        </span>
      )}
    </span>
  )
}
