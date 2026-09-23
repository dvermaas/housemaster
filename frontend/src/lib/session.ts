/* What the detail page needs to know about the list you came from.
 *
 * Module state rather than URL state on purpose: a shared detail link should
 * open the house, not a stranger's filter set. Within a session, though, j/k
 * walks the list in the order you were looking at it, and "back" returns to
 * exactly those filters.
 */
import type { BrowseSearch } from "@/lib/filters"

let order: number[] = []
let search: BrowseSearch = {}

export function rememberList(ids: number[], from: BrowseSearch) {
  order = ids
  search = from
}

export const lastSearch = () => search

/** The neighbours of `id` in the last list shown, and its place in it. */
export function neighbours(id: number) {
  const at = order.indexOf(id)
  if (at < 0) return null
  return {
    position: at + 1,
    total: order.length,
    previous: at > 0 ? order[at - 1] : undefined,
    next: at < order.length - 1 ? order[at + 1] : undefined,
  }
}
