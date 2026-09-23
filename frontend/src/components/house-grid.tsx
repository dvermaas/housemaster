import { useWindowVirtualizer } from "@tanstack/react-virtual"
import * as React from "react"

import { HouseCard } from "@/components/house-card"
import type { House } from "@/lib/data"
import type { useBrowse } from "@/lib/use-browse"

const MIN_CARD_WIDTH = 280
const GAP = 16
const ROW_ESTIMATE = 360
/** Divides evenly into 1, 2, 3, 4 and 6 columns, so no batch ends mid-row. */
const BATCH = 48
/** Grow once the last rendered row is this close to the end. */
const GROW_AHEAD = 3

/** How far the last list was scrolled into, so Back from a detail page lands
 *  on a page tall enough for the router to restore the scroll position. */
let remembered = { key: "", count: BATCH }

/** The matching houses, virtualised, in batches as you scroll.
 *
 *  The index is already in memory, so a batch costs nothing to "load"; it
 *  exists so the page -- and its scrollbar -- is as long as what you have
 *  looked at, not the whole result. Only the rows near the viewport exist in
 *  the DOM either way. It scrolls the window rather than a box, so the
 *  browser's own scroll restoration and keyboard scrolling keep working. */
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

  // A new filter set starts from the first batch again.
  const listKey = JSON.stringify(browse.search)
  const [batch, setBatch] = React.useState(() => ({
    key: listKey,
    count: remembered.key === listKey ? remembered.count : BATCH,
  }))
  if (batch.key !== listKey) setBatch({ key: listKey, count: BATCH })
  const shown = Math.min(batch.count, houses.length)

  React.useEffect(() => {
    remembered = batch
  }, [batch])

  const rows = Math.ceil(shown / columns)
  const virtualizer = useWindowVirtualizer({
    count: rows,
    estimateSize: () => ROW_ESTIMATE,
    overscan: 3,
    gap: GAP,
    scrollMargin: offset,
    // Scrolling, resizing and measuring all land here: the moment the last
    // rendered row nears the end is the moment to add the next batch.
    onChange: (instance) => {
      const last = instance.getVirtualItems().at(-1)?.index ?? 0
      if (shown < houses.length && last >= rows - GROW_AHEAD)
        // Idempotent: several calls before the next render add one batch.
        setBatch((b) => ({ ...b, count: Math.max(b.count, shown + BATCH) }))
    },
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
              .slice(
                row.index * columns,
                Math.min(shown, row.index * columns + columns)
              )
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
