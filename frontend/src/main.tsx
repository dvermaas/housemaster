import { QueryClientProvider } from "@tanstack/react-query"
import { RouterProvider } from "@tanstack/react-router"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"
import { ThemeProvider } from "@/components/theme-provider"
import { TooltipProvider } from "@/components/ui/tooltip"
import { indexQuery, loadCachedIndex, type Offering } from "@/lib/data"
import { queryClient, router } from "@/router"

/** Seed the query cache from IndexedDB before the first render.
 *
 *  A return visit then paints real houses on the first frame instead of a
 *  skeleton, and the query revalidates in the background -- `updatedAt: 0`
 *  marks the copy stale so it does. */
async function hydrate() {
  const offering: Offering =
    new URLSearchParams(window.location.search).get("offering") === "rent"
      ? "rent"
      : "buy"
  const cached = await loadCachedIndex(offering).catch(() => undefined)
  if (cached)
    queryClient.setQueryData(indexQuery(offering).queryKey, cached, {
      updatedAt: 0,
    })
}

void hydrate().finally(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <ThemeProvider>
        <QueryClientProvider client={queryClient}>
          <TooltipProvider>
            <RouterProvider router={router} />
          </TooltipProvider>
        </QueryClientProvider>
      </ThemeProvider>
    </StrictMode>
  )
})
