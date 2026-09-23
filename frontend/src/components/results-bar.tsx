import { LayoutGridIcon, MapIcon, SlidersHorizontalIcon } from "lucide-react"

import { FilterRail } from "@/components/filter-rail"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { Kbd } from "@/components/ui/kbd"
import type { Offering } from "@/lib/data"
import { DEFAULT_SORT, FILTER_KEYS, type Sort, SORTS } from "@/lib/filters"
import { plural } from "@/lib/format"
import type { useBrowse } from "@/lib/use-browse"

type Browse = ReturnType<typeof useBrowse>

export function ResultsBar({ browse }: { browse: Browse }) {
  const { search, update, offering, view, matched, index } = browse
  const active = FILTER_KEYS.filter(
    (k) => k !== "sort" && search[k] !== undefined
  ).length

  return (
    <div className="sticky top-12 z-30 flex flex-wrap items-center gap-2 border-b bg-background/85 px-4 py-2 backdrop-blur-md">
      {/* The mode switch, not a filter: it changes what `price` means, so
          switching drops the price bounds -- a 0–1 800 rent range means
          nothing for buying. The other filters come along. */}
      <Tabs
        value={offering}
        onValueChange={(value) =>
          update({
            offering: value === "rent" ? "rent" : undefined,
            price_min: undefined,
            price_max: undefined,
          })
        }
      >
        <TabsList>
          <TabsTrigger value={"buy" satisfies Offering}>Buy</TabsTrigger>
          <TabsTrigger value={"rent" satisfies Offering}>Rent</TabsTrigger>
        </TabsList>
      </Tabs>

      <span className="text-sm text-muted-foreground" aria-live="polite">
        {index.data ? (
          <>
            <span className="font-mono font-medium text-foreground">
              {matched.length}
            </span>{" "}
            {plural(matched.length, offering)}
          </>
        ) : null}
      </span>

      <div className="ml-auto flex items-center gap-2">
        <Sheet>
          <SheetTrigger
            render={
              <Button variant="outline" size="sm" className="lg:hidden" />
            }
          >
            <SlidersHorizontalIcon data-icon="inline-start" />
            Filters
            {active > 0 && <Badge variant="secondary">{active}</Badge>}
          </SheetTrigger>
          <SheetContent side="left" className="overflow-y-auto">
            <SheetHeader>
              <SheetTitle>Filters</SheetTitle>
            </SheetHeader>
            <FilterRail browse={browse} />
          </SheetContent>
        </Sheet>

        {/* A map has no reading order, so the sort control is hidden there
            rather than left to look like it does something. The choice stays
            in the URL, so switching back keeps it. */}
        {view === "grid" && (
          <Select
            value={search.sort ?? DEFAULT_SORT}
            onValueChange={(value) =>
              update({
                sort: value === DEFAULT_SORT ? undefined : (value as Sort),
              })
            }
            items={Object.entries(SORTS).map(([value, label]) => ({
              value,
              label,
            }))}
          >
            <SelectTrigger size="sm" aria-label="Sort order">
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="end">
              {Object.entries(SORTS).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        <Tooltip>
          <TooltipTrigger
            render={
              <ToggleGroup
                variant="outline"
                size="sm"
                value={[view]}
                onValueChange={(value) => {
                  const next = value[0]
                  if (next) update({ view: next === "map" ? "map" : undefined })
                }}
                aria-label="View"
              />
            }
          >
            <ToggleGroupItem value="grid" aria-label="Grid">
              <LayoutGridIcon />
            </ToggleGroupItem>
            <ToggleGroupItem value="map" aria-label="Map">
              <MapIcon />
            </ToggleGroupItem>
          </TooltipTrigger>
          <TooltipContent>
            Switch view <Kbd>M</Kbd>
          </TooltipContent>
        </Tooltip>
      </div>
    </div>
  )
}
