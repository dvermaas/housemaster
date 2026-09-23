import { useWindowVirtualizer } from "@tanstack/react-virtual"
import * as React from "react"

import { HouseCard } from "@/components/house-card"
import type { House } from "@/lib/data"
import type { useBrowse } from "@/lib/use-browse"

const MIN_CARD_WIDTH = 280
const GAP = 16
const ROW_ESTIMATE = 360

/** Every matching house, virtualised.
 *
 *  All of them, not a page: the index is already in memory, so there is
 *  nothing to load more of. Only the rows near the viewport exist in the DOM,
 *  which keeps a 5 000-house result as cheap to scroll as a 20-house one. It
 *  scrolls the window rather than a box, so the browser's own scroll
 *  restoration and keyboard scrolling keep working. */
export function HouseGrid({
  houses,
  browse,
}: {
  houses: House[]
  browse: ReturnType<typeof useBrowse>
}) {
  const container = React.useRef<HTMLDivElement>(null)
  const [columns, setColumns] = React.useState(3)
  const [offset, setOffset] = React.useState(0)

  React.useLayoutEffect(() => {
    const node = container.current
    if (!node) return undefined
    const measure = () => {
      const width = node.clientWidth - GAP * 2
      setColumns(
        Math.max(1, Math.floor((width + GAP) / (MIN_CARD_WIDTH + GAP)))
      )
      setOffset(node.getBoundingClientRect().top + window.scrollY)
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  const rows = Math.ceil(houses.length / columns)
  const virtualizer = useWindowVirtualizer({
    count: rows,
    estimateSize: () => ROW_ESTIMATE,
    overscan: 3,
    gap: GAP,
    scrollMargin: offset,
  })

  const areaRange = browse.index.data?.facets.areaRange ?? [0, 0]
  const offering = browse.offering
  const items = virtualizer.getVirtualItems()

  return (
    <div ref={container} className="px-4 py-4">
      <div
        className="relative h-(--grid-height) w-full"
        style={
          {
            "--grid-height": `${virtualizer.getTotalSize()}px`,
          } as React.CSSProperties
        }
      >
        {items.map((row) => (
          <div
            key={row.key}
            data-index={row.index}
            ref={virtualizer.measureElement}
            className="absolute inset-x-0 top-0 grid translate-y-(--row-y) grid-cols-(--grid-columns) gap-4"
            style={
              {
                "--row-y": `${row.start - virtualizer.options.scrollMargin}px`,
                "--grid-columns": `repeat(${columns}, minmax(0, 1fr))`,
              } as React.CSSProperties
            }
          >
            {houses
              .slice(row.index * columns, row.index * columns + columns)
              .map((house, i) => (
                <HouseCard
                  key={house.id}
                  house={house}
                  offering={offering}
                  areaRange={areaRange}
                  // The first screenful is what the eye lands on: fetch those
                  // images before the lazy ones.
                  eager={row.index < 2 && i < columns}
                />
              ))}
          </div>
        ))}
      </div>
    </div>
  )
}
