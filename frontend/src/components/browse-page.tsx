import * as React from "react"

import { FilterRail } from "@/components/filter-rail"
import { HouseGrid } from "@/components/house-grid"
import { ResultsBar } from "@/components/results-bar"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { plural } from "@/lib/format"
import { useHotkeys } from "@/lib/hotkeys"
import { rememberList } from "@/lib/session"
import { useBrowse } from "@/lib/use-browse"

// Split out: MapLibre is most of the bundle, and the grid never needs it.
const loadMap = () => import("@/components/map-view")
const MapView = React.lazy(loadMap)

export function BrowsePage() {
  const browse = useBrowse()
  const { index, sorted, search, view, update } = browse

  // So the detail page can walk this list with j/k, and "back" returns to it.
  React.useEffect(() => {
    rememberList(
      sorted.map((h) => h.id),
      search
    )
  }, [sorted, search])

  // Warm the map chunk once the grid is up, so switching view is instant.
  React.useEffect(() => {
    const idle =
      window.requestIdleCallback ??
      ((fn: () => void) => window.setTimeout(fn, 1500))
    idle(() => void loadMap())
  }, [])

  const searchInput = React.useRef<HTMLInputElement>(null)
  useHotkeys({
    m: () => update({ view: view === "map" ? undefined : "map" }),
    "/": () => searchInput.current?.focus(),
  })

  return (
    <div className="flex flex-1">
      <aside className="sticky top-12 hidden h-below-header w-72 shrink-0 overflow-y-auto border-r lg:block">
        <FilterRail browse={browse} searchRef={searchInput} />
      </aside>
      <main className="flex min-w-0 flex-1 flex-col">
        <ResultsBar browse={browse} />
        {index.isPending ? (
          <GridSkeleton />
        ) : index.isError ? (
          <Empty>
            <EmptyHeader>
              <EmptyTitle>Could not load the houses.</EmptyTitle>
              <EmptyDescription>{String(index.error.message)}</EmptyDescription>
            </EmptyHeader>
            <EmptyContent>
              <Button variant="outline" onClick={() => void index.refetch()}>
                Try again
              </Button>
            </EmptyContent>
          </Empty>
        ) : view === "map" ? (
          <React.Suspense
            fallback={
              <div className="grid flex-1 place-items-center">
                <Spinner />
              </div>
            }
          >
            <MapView browse={browse} />
          </React.Suspense>
        ) : sorted.length ? (
          <HouseGrid houses={sorted} browse={browse} />
        ) : (
          <NoMatches browse={browse} />
        )}
      </main>
    </div>
  )
}

function NoMatches({ browse }: { browse: ReturnType<typeof useBrowse> }) {
  const total = browse.index.data?.meta.counts.total ?? 0
  // An empty cache is not a filter that matched nothing, and saying so sends
  // people to widen a range that was never the problem.
  if (total === 0) {
    return (
      <Empty>
        <EmptyHeader>
          <EmptyTitle>The cache is empty.</EmptyTitle>
          <EmptyDescription>
            Track a search with <code>housemaster add &lt;url&gt;</code>, then
            run <code>housemaster fetch</code> to populate it.
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }
  return (
    <Empty>
      <EmptyHeader>
        <EmptyTitle>
          No {plural(2, browse.offering)} match these filters.
        </EmptyTitle>
        <EmptyDescription>
          Widen a range, or start again. Filters are remembered between visits,
          so this can happen on arrival too.
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <Button variant="outline" onClick={browse.clear}>
          Clear all filters
        </Button>
      </EmptyContent>
    </Empty>
  )
}

function GridSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className="flex flex-col gap-3">
          <Skeleton className="aspect-3/2 w-full" />
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-4 w-1/3" />
        </div>
      ))}
    </div>
  )
}
