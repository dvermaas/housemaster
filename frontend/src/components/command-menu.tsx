import { useQuery } from "@tanstack/react-query"
import { useNavigate, useRouterState } from "@tanstack/react-router"
import {
  FilterXIcon,
  HouseIcon,
  KeyRoundIcon,
  MapIcon,
  LayoutGridIcon,
  MoonIcon,
} from "lucide-react"
import * as React from "react"

import { useTheme } from "@/components/theme-provider"
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command"
import { EnergyLabel } from "@/components/ui/energy-label"
import { type House, indexQuery, type Offering } from "@/lib/data"
import { type BrowseSearch, FILTER_KEYS, rememberFilters } from "@/lib/filters"
import { euro } from "@/lib/format"

const MAX_HITS = 40

/** ⌘K. Jump to any house by address, postcode or buurt, or run an action.
 *
 *  cmdk's own filtering scores every item on every keystroke, which is fine
 *  for a menu and hopeless for 5 000 houses -- so it is switched off and the
 *  search runs here, over the index, stopping at the first handful of hits. */
export function CommandMenu({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const navigate = useNavigate()
  const { resolvedTheme, setTheme } = useTheme()
  const search = useRouterState({
    select: (s) => s.location.search as BrowseSearch,
  })
  const onBrowse = useRouterState({
    select: (s) => s.location.pathname === "/",
  })
  const offering: Offering = search.offering ?? "buy"
  const { data } = useQuery(indexQuery(offering))
  const [query, setQuery] = React.useState("")

  // Reset on close, so the palette always opens empty.
  const setOpen = (next: boolean) => {
    if (!next) setQuery("")
    onOpenChange(next)
  }

  const hits = React.useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!data || needle.length < 2) return []
    const compactNeedle = needle.replaceAll(" ", "")
    const out: House[] = []
    for (const house of data.houses) {
      if (
        house.haystack.includes(needle) ||
        house.postalCode
          .toLowerCase()
          .replaceAll(" ", "")
          .startsWith(compactNeedle)
      ) {
        out.push(house)
        if (out.length >= MAX_HITS) break
      }
    }
    return out
  }, [data, query])

  const run = (action: () => void) => {
    setOpen(false)
    action()
  }

  const toBrowse = (patch: Partial<BrowseSearch>) =>
    navigate({
      to: "/",
      search: (previous: BrowseSearch) => ({
        ...(onBrowse ? previous : {}),
        ...patch,
      }),
    })

  return (
    <CommandDialog
      open={open}
      onOpenChange={setOpen}
      title="Jump to a house"
      description="Search by address, postcode or buurt"
    >
      <Command shouldFilter={false}>
        <CommandInput
          placeholder="Address, postcode or buurt…"
          value={query}
          onValueChange={setQuery}
        />
        <CommandList>
          <CommandEmpty>
            {query.trim().length < 2 ? "Type an address…" : "No house matches."}
          </CommandEmpty>
          {hits.length > 0 && (
            <CommandGroup
              heading={`Houses${hits.length >= MAX_HITS ? ` (first ${MAX_HITS})` : ""}`}
            >
              {hits.map((house) => (
                <CommandItem
                  key={house.id}
                  value={`house-${house.id}`}
                  onSelect={() =>
                    run(() =>
                      navigate({ to: "/house/$id", params: { id: house.id } })
                    )
                  }
                >
                  <EnergyLabel label={house.label} size="sm" />
                  <span className="truncate">{house.address}</span>
                  <span className="truncate text-muted-foreground">
                    {house.hood || house.city}
                  </span>
                  <CommandShortcut>{euro(house.price)}</CommandShortcut>
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          {query.trim().length < 2 && (
            <CommandGroup heading="Actions">
              <CommandItem
                value="view"
                onSelect={() =>
                  run(() =>
                    toBrowse({
                      view:
                        onBrowse && search.view === "map" ? undefined : "map",
                    })
                  )
                }
              >
                {onBrowse && search.view === "map" ? (
                  <LayoutGridIcon />
                ) : (
                  <MapIcon />
                )}
                {onBrowse && search.view === "map"
                  ? "Show as grid"
                  : "Show on map"}
                <CommandShortcut>M</CommandShortcut>
              </CommandItem>
              <CommandItem
                value="offering"
                onSelect={() =>
                  run(() =>
                    toBrowse({
                      offering: offering === "rent" ? undefined : "rent",
                      price_min: undefined,
                      price_max: undefined,
                    })
                  )
                }
              >
                {offering === "rent" ? <HouseIcon /> : <KeyRoundIcon />}
                {offering === "rent" ? "Switch to buying" : "Switch to renting"}
              </CommandItem>
              <CommandItem
                value="clear"
                onSelect={() =>
                  run(() => {
                    const cleared: BrowseSearch = {
                      offering: search.offering,
                      view: search.view,
                    }
                    rememberFilters(cleared)
                    void navigate({
                      to: "/",
                      search: {
                        ...cleared,
                        ...Object.fromEntries(
                          FILTER_KEYS.map((k) => [k, undefined])
                        ),
                      },
                    })
                  })
                }
              >
                <FilterXIcon />
                Clear all filters
              </CommandItem>
              <CommandItem
                value="theme"
                onSelect={() =>
                  run(() =>
                    setTheme(resolvedTheme === "dark" ? "light" : "dark")
                  )
                }
              >
                <MoonIcon />
                Toggle dark mode
                <CommandShortcut>D</CommandShortcut>
              </CommandItem>
            </CommandGroup>
          )}
        </CommandList>
      </Command>
    </CommandDialog>
  )
}
