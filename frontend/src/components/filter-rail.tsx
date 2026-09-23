import { SearchIcon } from "lucide-react"
import * as React from "react"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { ENERGY_SCALE, EnergyLabel } from "@/components/ui/energy-label"
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSeparator,
  FieldSet,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group"
import { Kbd } from "@/components/ui/kbd"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { type BrowseSearch, STATUSES } from "@/lib/filters"
import { compact } from "@/lib/format"
import type { useBrowse } from "@/lib/use-browse"

type Browse = ReturnType<typeof useBrowse>
type NumberKey =
  "price_min" | "price_max" | "area_min" | "area_max" | "rooms_min" | "beds_min"

const ANY_STATUS = "any"
/** Stable, so the memoised buurt list sees "nothing chosen" as unchanged. */
const NO_HOODS: string[] = []

export function FilterRail({
  browse,
  searchRef,
}: {
  browse: Browse
  searchRef?: React.Ref<HTMLInputElement>
}) {
  const { search, update, offering, index, searching } = browse
  const facets = index.data?.facets

  return (
    <div className="p-4">
      <FieldGroup>
        <TextSearch
          value={search.q ?? ""}
          onChange={(q) => update({ q: q || undefined })}
          searching={searching}
          inputRef={searchRef}
        />

        <FieldSet>
          <FieldLegend variant="label">
            {offering === "rent" ? "Rent per month" : "Asking price"}
          </FieldLegend>
          <Range
            search={search}
            update={update}
            keys={["price_min", "price_max"]}
            placeholders={
              facets?.priceRange.map((v) => compact(v)) as [string, string]
            }
            label="price"
          />
        </FieldSet>

        <FieldSet>
          <FieldLegend variant="label">Living area m²</FieldLegend>
          <Range
            search={search}
            update={update}
            keys={["area_min", "area_max"]}
            placeholders={facets?.areaRange.map(String) as [string, string]}
            label="area"
          />
        </FieldSet>

        <div className="grid grid-cols-2 gap-2">
          <Field>
            <FieldLabel htmlFor="rooms_min">Rooms</FieldLabel>
            <NumberInput
              id="rooms_min"
              search={search}
              update={update}
              name="rooms_min"
              placeholder="any"
            />
          </Field>
          <Field>
            <FieldLabel htmlFor="beds_min">Bedrooms</FieldLabel>
            <NumberInput
              id="beds_min"
              search={search}
              update={update}
              name="beds_min"
              placeholder="any"
            />
          </Field>
        </div>

        <FieldSet>
          <FieldLegend variant="label">Energy label</FieldLegend>
          {/* The whole NEN ladder renders; steps with no houses are disabled
            rather than dropped, so the scale keeps its meaning. */}
          <ToggleGroup
            multiple
            variant="outline"
            size="sm"
            spacing={1}
            className="flex-wrap"
            value={search.label ?? []}
            onValueChange={(labels) => update({ label: labels as string[] })}
          >
            {ENERGY_SCALE.map((label) => {
              const n = facets?.labelCounts.get(label) ?? 0
              return (
                <ToggleGroupItem
                  key={label}
                  value={label}
                  disabled={!n && !search.label?.includes(label)}
                  aria-label={`Energy label ${label}, ${n} houses`}
                  title={`${n} houses`}
                >
                  <EnergyLabel label={label} size="sm" />
                  <span className="font-mono text-xs text-muted-foreground">
                    {n || ""}
                  </span>
                </ToggleGroupItem>
              )
            })}
          </ToggleGroup>
        </FieldSet>

        {facets && facets.hoods.length > 0 && (
          <Hoods
            hoods={facets.hoods}
            selected={search.hood ?? NO_HOODS}
            update={update}
          />
        )}

        <FieldSet>
          <FieldLegend variant="label">Availability</FieldLegend>
          <Select
            value={search.status ?? ANY_STATUS}
            onValueChange={(value) =>
              update({
                status: value === ANY_STATUS ? undefined : (value as string),
              })
            }
            items={[
              { value: ANY_STATUS, label: "Any" },
              ...Object.entries(STATUSES).map(([value, label]) => ({
                value,
                label,
              })),
            ]}
          >
            <SelectTrigger className="w-full" aria-label="Availability">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY_STATUS}>Any</SelectItem>
              {Object.entries(STATUSES).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Field orientation="horizontal">
            <Checkbox
              id="delisted"
              checked={search.delisted === 1}
              onCheckedChange={(checked) =>
                update({ delisted: checked ? 1 : undefined })
              }
            />
            {/* Honest wording: it stopped matching the tracked searches, which
              is not the same as sold -- a price rise past the ceiling does it. */}
            <FieldLabel htmlFor="delisted">
              Include houses that left the search
            </FieldLabel>
          </Field>
        </FieldSet>

        <FieldSeparator />
        <Button variant="ghost" size="sm" onClick={browse.clear}>
          Clear all filters
        </Button>
      </FieldGroup>
    </div>
  )
}

function TextSearch({
  value,
  onChange,
  searching,
  inputRef,
}: {
  value: string
  onChange: (value: string) => void
  searching: boolean
  inputRef?: React.Ref<HTMLInputElement>
}) {
  return (
    <InputGroup>
      <InputGroupAddon>
        <SearchIcon />
      </InputGroupAddon>
      <InputGroupInput
        ref={inputRef}
        type="search"
        value={value}
        placeholder="Street, buurt, description"
        aria-label="Search text"
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") event.currentTarget.blur()
        }}
      />
      <InputGroupAddon align="inline-end">
        {searching ? <Spinner /> : <Kbd>/</Kbd>}
      </InputGroupAddon>
    </InputGroup>
  )
}

const parse = (text: string) => {
  const cleaned = text.replaceAll(".", "").replaceAll(",", "").trim()
  return /^\d+$/.test(cleaned) ? Number.parseInt(cleaned, 10) : undefined
}

/** A number field that keeps what you typed while the URL keeps the number.
 *
 *  Bound straight to the URL, "300.000" would snap to "300000" mid-keystroke
 *  and throw the caret to the end. The draft only resets when the URL changes
 *  from elsewhere -- a clear, a Back, a restored filter. */
function NumberInput({
  search,
  update,
  name,
  ...props
}: {
  search: BrowseSearch
  update: Browse["update"]
  name: NumberKey
} & Omit<React.ComponentProps<typeof Input>, "name" | "value" | "onChange">) {
  const committed = search[name]
  const [draft, setDraft] = React.useState(committed?.toString() ?? "")
  if (
    parse(draft) !== committed &&
    !(draft === "" && committed === undefined)
  ) {
    // Render-time sync, React's recommended alternative to an effect here.
    setDraft(committed?.toString() ?? "")
  }
  return (
    <Input
      inputMode="numeric"
      autoComplete="off"
      value={draft}
      onChange={(event) => {
        setDraft(event.target.value)
        update({ [name]: parse(event.target.value) })
      }}
      {...props}
    />
  )
}

function Range({
  search,
  update,
  keys,
  placeholders,
  label,
}: {
  search: BrowseSearch
  update: Browse["update"]
  keys: [NumberKey, NumberKey]
  placeholders?: [string, string]
  label: string
}) {
  return (
    <div className="flex items-center gap-2">
      <NumberInput
        search={search}
        update={update}
        name={keys[0]}
        placeholder={placeholders?.[0]}
        aria-label={`Minimum ${label}`}
      />
      <span className="text-muted-foreground" aria-hidden>
        –
      </span>
      <NumberInput
        search={search}
        update={update}
        name={keys[1]}
        placeholder={placeholders?.[1]}
        aria-label={`Maximum ${label}`}
      />
    </div>
  )
}

/** Den Haag alone has over a hundred buurten, so the list gets its own filter,
 *  and the ones you picked stay on top where you can see them. */
// Memoised: 150 checkboxes are most of the rail, and they only need to
// re-render when the buurt selection itself changes, not on every filter.
const Hoods = React.memo(function Hoods({
  hoods,
  selected,
  update,
}: {
  hoods: string[]
  selected: string[]
  update: Browse["update"]
}) {
  const [filter, setFilter] = React.useState("")
  const chosen = React.useMemo(() => new Set(selected), [selected])
  const visible = React.useMemo(() => {
    const needle = filter.trim().toLowerCase()
    const matching = needle
      ? hoods.filter((h) => h.toLowerCase().includes(needle))
      : hoods
    return [
      ...selected.filter((h) => !needle || h.toLowerCase().includes(needle)),
      ...matching.filter((h) => !chosen.has(h)),
    ]
  }, [hoods, selected, chosen, filter])

  const toggle = (hood: string, on: boolean) =>
    update({
      hood: on ? [...selected, hood] : selected.filter((h) => h !== hood),
    })

  return (
    <FieldSet>
      <FieldLegend variant="label">
        Buurt
        {selected.length > 0 && (
          <span className="ml-1 font-normal text-muted-foreground">
            · {selected.length} chosen
          </span>
        )}
      </FieldLegend>
      <Input
        type="search"
        placeholder={`Filter ${hoods.length} buurten`}
        aria-label="Filter buurten"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
      />
      <div className="rounded-lg border">
        <ScrollArea className="h-56">
          <div className="flex flex-col p-1">
            {visible.map((hood) => {
              const id = `hood-${hood}`
              return (
                <div key={hood} className="rounded-md px-2 py-1 hover:bg-muted">
                  <Field orientation="horizontal">
                    <Checkbox
                      id={id}
                      checked={chosen.has(hood)}
                      onCheckedChange={(on) => toggle(hood, on)}
                    />
                    <FieldLabel htmlFor={id}>{hood}</FieldLabel>
                  </Field>
                </div>
              )
            })}
            {visible.length === 0 && (
              <p className="px-2 py-3 text-sm text-muted-foreground">
                No buurt matches.
              </p>
            )}
          </div>
        </ScrollArea>
      </div>
    </FieldSet>
  )
})
