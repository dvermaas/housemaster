import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { useNavigate, useSearch } from "@tanstack/react-router"
import * as React from "react"

import { fetchSearch, indexQuery, type Offering } from "@/lib/data"
import {
  applyFilters,
  type BrowseSearch,
  FILTER_KEYS,
  rememberFilters,
  sortHouses,
} from "@/lib/filters"

const SEARCH_DEBOUNCE_MS = 180

function useDebounced<T>(value: T, ms: number) {
  const [settled, setSettled] = React.useState(value)
  React.useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), ms)
    return () => window.clearTimeout(timer)
  }, [value, ms])
  return settled
}

/** The browse page's state: the URL's filters, the index, and the result. */
export function useBrowse() {
  const search = useSearch({ from: "/" })
  const offering: Offering = search.offering ?? "buy"
  const index = useQuery(indexQuery(offering))

  // Description hits come from the server; address and buurt match locally at
  // once, so the grid never waits on the network to react to a keystroke.
  const q = useDebounced(search.q ?? "", SEARCH_DEBOUNCE_MS)
  const remote = useQuery({
    queryKey: ["search", offering, q],
    queryFn: () => fetchSearch(offering, q),
    enabled: q.length > 0,
    staleTime: Infinity,
    placeholderData: keepPreviousData,
  })
  const extra = search.q && remote.data ? remote.data : undefined

  // Deferred: typing stays responsive and React filters in the gap after.
  const deferred = React.useDeferredValue(search)
  const houses = index.data?.houses
  // Two memos, so a sort change never refilters and the map (which ignores
  // order) never sees a new array because of one.
  const matched = React.useMemo(
    () => (houses ? applyFilters(houses, deferred, extra) : []),
    [houses, deferred, extra]
  )
  const sorted = React.useMemo(
    () => sortHouses(matched, deferred.sort),
    [matched, deferred.sort]
  )

  const navigate = useNavigate({ from: "/" })

  /** Change filters. Replaces the history entry rather than pushing one --
   *  Back should leave the page, not undo a keystroke at a time. */
  const update = React.useCallback(
    (patch: Partial<BrowseSearch>) => {
      void navigate({
        search: (previous: BrowseSearch) => {
          const next = { ...previous, ...patch }
          for (const key of Object.keys(next) as (keyof BrowseSearch)[]) {
            const value = next[key]
            if (
              value === undefined ||
              value === "" ||
              (Array.isArray(value) && !value.length)
            ) {
              delete next[key]
            }
          }
          // Saved on interaction only, never on arrival -- opening a shared
          // link must not overwrite your own remembered filters.
          if (
            Object.keys(patch).some((k) =>
              (FILTER_KEYS as readonly string[]).includes(k)
            )
          ) {
            rememberFilters(next)
          }
          return next
        },
        replace: true,
      })
    },
    [navigate]
  )

  const clear = React.useCallback(() => {
    const cleared = Object.fromEntries(FILTER_KEYS.map((k) => [k, undefined]))
    update(cleared)
  }, [update])

  return {
    search,
    offering,
    view: search.view ?? "grid",
    index,
    matched,
    sorted,
    searching: remote.isFetching,
    update,
    clear,
  }
}
