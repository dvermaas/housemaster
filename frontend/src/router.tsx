import { QueryClient } from "@tanstack/react-query"
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  redirect,
} from "@tanstack/react-router"

import { AppShell } from "@/components/app-shell"
import { BrowsePage } from "@/components/browse-page"
import { HousePage } from "@/components/house-page"
import { NotFoundPage } from "@/components/not-found"
import { houseQuery } from "@/lib/data"
import {
  hasFilters,
  parseSearch,
  recallFilters,
  stringifySearch,
  validateSearch,
} from "@/lib/filters"

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // The data changes once a day, when `fetch` runs. Refetching on every
      // tab focus would be all cost and no news.
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

const rootRoute = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: AppShell,
  notFoundComponent: NotFoundPage,
})

export const browseRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  validateSearch,
  // Filter memory. The URL wins: a link someone shared carries filters and
  // must open on them, so this only fires on a URL with none at all. `view`
  // and `offering` are page properties, so they survive the redirect and pick
  // which side's memory to read.
  beforeLoad: ({ search }) => {
    if (hasFilters(search)) return
    const stored = recallFilters(search.offering ?? "buy")
    if (!stored) return
    // `replace`: a dead entry in history would bounce Back forward again.
    throw redirect({
      to: "/",
      search: { ...stored, offering: search.offering, view: search.view },
      replace: true,
    })
  },
  component: BrowsePage,
})

export const houseRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/house/$id",
  params: {
    parse: ({ id }) => ({ id: Number.parseInt(id, 10) }),
    stringify: ({ id }) => ({ id: String(id) }),
  },
  // Started, not awaited: the page renders at once from the index row it
  // already has, and the photos and kenmerken stream in behind it. Runs on
  // hover too (`defaultPreload: "intent"`), so usually it has already landed.
  loader: ({ context, params }) => {
    if (Number.isFinite(params.id))
      void context.queryClient.prefetchQuery(houseQuery(params.id))
  },
  component: HousePage,
})

export const router = createRouter({
  routeTree: rootRoute.addChildren([browseRoute, houseRoute]),
  context: { queryClient },
  parseSearch,
  stringifySearch,
  defaultPreload: "intent",
  // Unchanged parts of the search keep their identity across navigations, so
  // memoised components fed from them skip the re-render.
  defaultStructuralSharing: true,
  defaultPreloadDelay: 40,
  // TanStack Query owns caching; the router should not second-guess it.
  defaultPreloadStaleTime: 0,
  scrollRestoration: true,
})

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router
  }
}
