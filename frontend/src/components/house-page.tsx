import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate, useParams } from "@tanstack/react-router"
import {
  ArrowLeftIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  ExternalLinkIcon,
  ImagesIcon,
  MapIcon,
  MapPinIcon,
} from "lucide-react"
import * as React from "react"

import { CommandMenuButton } from "@/components/app-shell"
import { Lightbox } from "@/components/lightbox"
import { NotFoundPage } from "@/components/not-found"
import { ThemeMenu } from "@/components/theme-menu"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { EnergyLabel } from "@/components/ui/energy-label"
import { Kbd } from "@/components/ui/kbd"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  type BrowseIndex,
  type House,
  type HouseDetail,
  houseQuery,
  NotFound,
  type Offering,
  OFFERINGS,
  photoSrcSet,
  photoUrl,
} from "@/lib/data"
import {
  compact,
  euro,
  isoToEpoch,
  perM2,
  publishedLabel,
  since,
  statusLabel,
} from "@/lib/format"
import { useHotkeys } from "@/lib/hotkeys"
import { lastSearch, neighbours } from "@/lib/session"
import { cn } from "@/lib/utils"

const GALLERY_PREVIEW = 5

// MapLibre is most of the bundle; the map view's chunk already holds it.
const HouseMap = React.lazy(() => import("@/components/house-map"))

/** The index row for this house, from whichever side's index is in memory.
 *  It holds everything the header shows, so the page paints at once and only
 *  the photos, description and kenmerken wait on the network. */
function useIndexRow(id: number): House | undefined {
  const queryClient = useQueryClient()
  for (const offering of OFFERINGS) {
    const index = queryClient.getQueryData<BrowseIndex>(["index", offering])
    const row = index?.byId.get(id)
    if (row) return row
  }
  return undefined
}

export function HousePage() {
  const { id } = useParams({ from: "/house/$id" })
  const row = useIndexRow(id)
  const detail = useQuery(houseQuery(id))
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  // Keyed by house, so walking to the next one with j/k closes the viewer
  // without an effect having to reset it.
  const [photo, setPhoto] = React.useState<{ id: number; at: number } | null>(
    null
  )
  const photoAt = photo?.id === id ? photo.at : null
  const setPhotoAt = React.useCallback(
    (at: number | null) => setPhoto(at === null ? null : { id, at }),
    [id]
  )

  const around = neighbours(id)
  const back = () => void navigate({ to: "/", search: lastSearch() })
  const go = (target: number | undefined) => {
    if (target !== undefined)
      void navigate({ to: "/house/$id", params: { id: target } })
  }

  // Warm the neighbours, so j/k lands on a page that is already loaded.
  React.useEffect(() => {
    for (const other of [around?.previous, around?.next]) {
      if (other !== undefined) void queryClient.prefetchQuery(houseQuery(other))
    }
  }, [around?.previous, around?.next, queryClient])

  useHotkeys(
    {
      j: () => go(around?.next),
      ArrowRight: () => go(around?.next),
      k: () => go(around?.previous),
      ArrowLeft: () => go(around?.previous),
      Escape: back,
    },
    photoAt === null
  )

  if (!Number.isFinite(id) || (detail.error instanceof NotFound && !row))
    return <NotFoundPage />

  const listing = detail.data?.listing
  const offering: Offering =
    listing?.offering_type ?? (row ? guessOffering(queryClient, id) : "buy")
  const head = {
    address: listing?.address ?? row?.address ?? "",
    postalCode: listing?.postal_code ?? row?.postalCode ?? "",
    city: listing?.city ?? row?.city ?? "",
    hood: listing?.neighbourhood ?? row?.hood ?? "",
    price: listing?.price ?? row?.price ?? null,
    pricePerM2: listing?.price_per_m2 ?? row?.pricePerM2 ?? null,
    area: listing?.living_area ?? row?.area ?? null,
    rooms: listing?.rooms ?? row?.rooms ?? null,
    beds: listing?.bedrooms ?? row?.beds ?? null,
    label: listing?.energy_label ?? row?.label ?? null,
    status: listing?.status ?? row?.status ?? "",
    delisted: listing ? Boolean(listing.delisted_at) : (row?.delisted ?? false),
  }
  const photos = detail.data?.photos ?? (row?.image ? [row.image] : [])
  const status =
    head.status && head.status !== "none" ? statusLabel(head.status) : ""

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-4">
      <nav className="flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={back}>
          <ArrowLeftIcon data-icon="inline-start" />
          All houses
        </Button>
        <div className="ml-auto flex items-center gap-1.5">
          {around && (
            <div className="flex items-center gap-1">
              <span className="mr-1 font-mono text-xs text-muted-foreground">
                {around.position} / {around.total}
              </span>
              <Tooltip>
                <TooltipTrigger
                  render={
                    <Button
                      variant="outline"
                      size="icon-sm"
                      aria-label="Previous house"
                      disabled={around.previous === undefined}
                      onClick={() => go(around.previous)}
                    />
                  }
                >
                  <ChevronUpIcon />
                </TooltipTrigger>
                <TooltipContent>
                  Previous <Kbd>K</Kbd>
                </TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger
                  render={
                    <Button
                      variant="outline"
                      size="icon-sm"
                      aria-label="Next house"
                      disabled={around.next === undefined}
                      onClick={() => go(around.next)}
                    />
                  }
                >
                  <ChevronDownIcon />
                </TooltipTrigger>
                <TooltipContent>
                  Next <Kbd>J</Kbd>
                </TooltipContent>
              </Tooltip>
            </div>
          )}
          <CommandMenuButton />
          <ThemeMenu />
        </div>
      </nav>

      <header className="flex flex-col gap-3">
        {head.address ? (
          <div>
            <h1 className="font-heading text-2xl font-semibold tracking-tight">
              {head.address}
            </h1>
            <p className="text-muted-foreground">
              {head.postalCode} {head.city}
              {head.hood && ` · ${head.hood}`}
            </p>
          </div>
        ) : (
          <Skeleton className="h-14 w-80" />
        )}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 font-mono text-sm">
          <span className="text-xl font-semibold">{euro(head.price)}</span>
          {listing?.price_condition && (
            <span className="text-muted-foreground">
              {listing.price_condition.replaceAll("_", " ")}
            </span>
          )}
          <span>
            {euro(head.pricePerM2)}
            {perM2(offering)}
          </span>
          <span>{head.area ?? "–"} m²</span>
          <span>
            {head.rooms ?? "–"} rooms · {head.beds ?? "–"} bed
          </span>
          <EnergyLabel label={head.label} size="lg" />
          {status && <Badge variant="secondary">{status}</Badge>}
          {/* Honest wording: it stopped matching the tracked searches, which is
              not the same as sold. */}
          {head.delisted && (
            <Badge variant="outline">No longer in the search</Badge>
          )}
        </div>
      </header>

      <Gallery
        photos={photos}
        loading={detail.isPending}
        title={head.address}
        onOpen={setPhotoAt}
      />

      {detail.isError && !(detail.error instanceof NotFound) ? (
        <p className="text-sm text-destructive">
          Could not load the details: {detail.error.message}
        </p>
      ) : null}

      {detail.data ? (
        <Details detail={detail.data} offering={offering} />
      ) : detail.isPending ? (
        <DetailsSkeleton />
      ) : null}

      <Lightbox
        photos={photos}
        index={photoAt}
        onIndexChange={setPhotoAt}
        onClose={() => setPhotoAt(null)}
        title={head.address}
      />
    </div>
  )
}

function guessOffering(
  queryClient: ReturnType<typeof useQueryClient>,
  id: number
): Offering {
  const rent = queryClient.getQueryData<BrowseIndex>(["index", "rent"])
  return rent?.byId.has(id) ? "rent" : "buy"
}

/** One big photo and four small, the way listing sites do it -- and only
 *  five. A 39-photo wall pushes the description, the reason to open this
 *  page, thousands of pixels down. The rest are one click away. */
function Gallery({
  photos,
  loading,
  title,
  onOpen,
}: {
  photos: string[]
  loading: boolean
  title: string
  onOpen: (index: number) => void
}) {
  const shown = photos.slice(0, GALLERY_PREVIEW)
  if (!shown.length && !loading) return null
  return (
    <div className="relative grid aspect-2/1 grid-cols-4 grid-rows-2 gap-2 overflow-hidden rounded-xl max-md:aspect-3/2 max-md:grid-cols-1 max-md:grid-rows-1">
      {Array.from({ length: GALLERY_PREVIEW }, (_, i) => {
        const photo = shown[i]
        const big = i === 0
        const tile = big
          ? "col-span-2 row-span-2 max-md:col-span-1 max-md:row-span-1"
          : "max-md:hidden"
        if (!photo) return <Skeleton key={i} className={tile} />
        return (
          <button
            key={photo}
            type="button"
            className={`${tile} overflow-hidden bg-muted outline-none focus-visible:ring-3 focus-visible:ring-ring/50`}
            onClick={() => onOpen(i)}
            aria-label={`Open photo ${i + 1}`}
          >
            <img
              src={photoUrl(photo, big ? 1080 : 464)}
              srcSet={
                big
                  ? photoSrcSet(photo, [720, 1080, 1440])
                  : photoSrcSet(photo, [464, 720])
              }
              sizes={big ? "(min-width: 768px) 50vw, 100vw" : "25vw"}
              alt={`${title}, ${i + 1} of ${photos.length}`}
              referrerPolicy="no-referrer"
              fetchPriority={big ? "high" : "auto"}
              decoding="async"
              className="size-full object-cover transition-opacity hover:opacity-90"
            />
          </button>
        )
      })}
      {photos.length > 1 && (
        <Button
          variant="secondary"
          size="sm"
          className="absolute right-3 bottom-3"
          onClick={() => onOpen(0)}
        >
          <ImagesIcon data-icon="inline-start" />
          All {photos.length} photos
        </Button>
      )}
    </div>
  )
}

type Fact = { label: string; value: React.ReactNode }

/** Rows funda sends that read as broken on their own. */
function tidy(
  group: string,
  rows: { label: string; value: string }[],
  energyLabel: string | null
): Fact[] {
  const out: Fact[] = []
  for (const { label, value } of rows) {
    // The value is the text of funda's help link; the label is a picture.
    if (label === "Energielabel" && value.startsWith("Wat betekent")) {
      if (energyLabel)
        out.push({ label, value: <EnergyLabel label={energyLabel} /> })
      continue
    }
    if (!value) {
      // A parcel number has no value, and is the whole point of its group.
      if (group === "Kadastrale gegevens") out.push({ label, value: null })
      // Anything else is a heading whose rows the parser does not keep yet.
      continue
    }
    out.push({ label, value })
  }
  return out
}

/** An eyebrow heading over a ruled section, as the old kenmerken were. */
function Section({
  title,
  className,
  children,
}: {
  title: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <section className={cn("flex flex-col gap-4", className)}>
      <h2 className="border-b pb-2 font-mono text-xs font-medium tracking-widest text-muted-foreground uppercase">
        {title}
      </h2>
      {children}
    </section>
  )
}

/** Label left, value right. A row without a value spans the line. */
function Facts({ rows }: { rows: Fact[] }) {
  return (
    <dl className="flex flex-col text-sm">
      {rows.map(({ label, value }) =>
        value === null ? (
          <dd key={label} className="py-1">
            {label}
          </dd>
        ) : (
          <div
            key={label}
            className="flex items-baseline justify-between gap-6 py-1"
          >
            <dt className="shrink-0 text-muted-foreground">{label}</dt>
            <dd className="text-right font-medium">{value}</dd>
          </div>
        )
      )}
    </dl>
  )
}

function Details({
  detail,
  offering,
}: {
  detail: HouseDetail
  offering: Offering
}) {
  const { listing, features, history } = detail
  const groups = React.useMemo(() => {
    // Funda's own grouping and ordering, preserved end to end.
    const out = new Map<string, { label: string; value: string }[]>()
    for (const f of features) {
      const rows = out.get(f.group) ?? []
      rows.push(f)
      out.set(f.group, rows)
    }
    return [...out]
      .map(([group, rows]) => ({
        group,
        rows: tidy(group, rows, listing.energy_label),
      }))
      .filter(({ rows }) => rows.length > 0)
  }, [features, listing.energy_label])

  const hood: Fact[] = []
  if (listing.neighbourhood_price_m2) {
    hood.push(
      {
        label: "Average asking price",
        value: (
          <span className="font-mono">
            {euro(listing.neighbourhood_price_m2)}/m²
          </span>
        ),
      },
      {
        label: "This house",
        value: (
          <span className="font-mono">
            {euro(listing.price_per_m2)}
            {perM2(offering)}
          </span>
        ),
      }
    )
  }
  if (listing.neighbourhood_inhabitants) {
    hood.push({
      label: "Inhabitants",
      value: (
        <span className="font-mono">
          {compact(listing.neighbourhood_inhabitants)}
        </span>
      ),
    })
  }

  const map =
    listing.lat !== null && listing.lng !== null ? (
      <div className="flex flex-col gap-2">
        <React.Suspense fallback={<Skeleton className="aspect-4/3 w-full" />}>
          <HouseMap lat={listing.lat} lng={listing.lng} />
        </React.Suspense>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            render={
              <Link
                to="/"
                search={{
                  view: "map",
                  offering: offering === "rent" ? "rent" : undefined,
                }}
              />
            }
          >
            <MapIcon data-icon="inline-start" />
            Big map
          </Button>
          <Button
            variant="outline"
            size="sm"
            render={
              <a
                href={`https://www.google.com/maps/search/?api=1&query=${listing.lat},${listing.lng}`}
                target="_blank"
                rel="noopener noreferrer"
                aria-label="Open in Google Maps"
              />
            }
          >
            <MapPinIcon data-icon="inline-start" />
            Google Maps
          </Button>
        </div>
      </div>
    ) : null

  // Two rows on a wide screen: the description beside the map, then the
  // kenmerken beside the rest -- so "Kenmerken" and "Buurt" start level.
  return (
    <div className="grid gap-10 lg:grid-cols-3">
      {listing.description && (
        <Description
          // Per house, so j/k to the next one starts collapsed and remeasures.
          key={listing.listing_id}
          text={listing.description}
          fill={map !== null}
        />
      )}
      {listing.description && map}

      {groups.length > 0 && (
        <Section title="Kenmerken" className="lg:col-span-2">
          {/* Columns, not a grid: groups run from one row to ten, and a grid
              pads every short one out to its row's tallest. */}
          <div className="columns-1 gap-10 sm:columns-2">
            {groups.map(({ group, rows }) => (
              <div key={group} className="mb-6 break-inside-avoid">
                <h3 className="mb-1 text-sm font-semibold">{group}</h3>
                <Facts rows={rows} />
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Sticky, so the numbers stay beside you down the kenmerken instead
          of leaving a column of white. */}
      <aside className="flex flex-col gap-8 lg:sticky lg:top-4 lg:col-start-3 lg:self-start">
        {!listing.description && map}

        {hood.length > 0 && (
          <Section title="Buurt">
            <Facts rows={hood} />
            {offering === "rent" && listing.neighbourhood_price_m2 ? (
              <p className="text-xs text-muted-foreground">
                Funda publishes the buurt average as a purchase price, so it is
                not a rent benchmark.
              </p>
            ) : null}
          </Section>
        )}

        {history.length > 1 && <PriceHistory history={history} />}

        <Section title="Listing">
          <Facts
            rows={[
              { label: "Agent", value: listing.agent },
              {
                label: "Published",
                value: (
                  <span className="font-mono">
                    {publishedLabel(listing.published)}
                  </span>
                ),
              },
              {
                label: "First seen",
                value: since(isoToEpoch(listing.first_seen_at)),
              },
            ]}
          />
          <Button
            variant="outline"
            size="sm"
            className="self-start"
            render={
              <a
                href={listing.url}
                target="_blank"
                rel="noopener noreferrer"
                aria-label="Open on funda"
              />
            }
          >
            <ExternalLinkIcon data-icon="inline-start" />
            Open on funda
          </Button>
        </Section>
      </aside>
    </div>
  )
}

/** Descriptions run to several screens. The first few lines say most of it. */
const COLLAPSED_LINES = 8

/** `fill`: on a wide screen, take exactly the height of the map beside it --
 *  as many whole lines as fit -- so the row below starts level on both sides.
 *  The section contributes no height of its own (`h-0 min-h-full`); the map
 *  sets the row, and the text is measured into it. */
function Description({ text, fill }: { text: string; fill: boolean }) {
  const [open, setOpen] = React.useState(false)
  const box = React.useRef<HTMLDivElement>(null)
  const body = React.useRef<HTMLParagraphElement>(null)
  const [fit, setFit] = React.useState({
    lines: COLLAPSED_LINES,
    overflows: text.length > 700,
  })

  React.useEffect(() => {
    const boxNode = box.current
    const bodyNode = body.current
    if (open || !boxNode || !bodyNode) return undefined
    const wide = window.matchMedia("(min-width: 64rem)")
    const measure = () => {
      const line = Number.parseFloat(getComputedStyle(bodyNode).lineHeight)
      const lines =
        fill && wide.matches && line > 0
          ? Math.max(3, Math.floor(boxNode.clientHeight / line))
          : COLLAPSED_LINES
      // scrollHeight is the whole text's height, whatever the clamp.
      const overflows = bodyNode.scrollHeight > lines * line + 1
      setFit((prev) =>
        prev.lines === lines && prev.overflows === overflows
          ? prev
          : { lines, overflows }
      )
    }
    const observer = new ResizeObserver(measure)
    observer.observe(boxNode)
    wide.addEventListener("change", measure)
    return () => {
      observer.disconnect()
      wide.removeEventListener("change", measure)
    }
  }, [open, fill])

  const filling = fill && !open
  return (
    <Section
      title="Omschrijving"
      className={cn("lg:col-span-2", filling && "lg:h-0 lg:min-h-full")}
    >
      <div ref={box} className={cn(filling && "lg:min-h-0 lg:flex-1")}>
        <p
          ref={body}
          className={cn(
            "text-sm leading-relaxed whitespace-pre-line",
            !open && "line-clamp-(--lines)"
          )}
          style={{ "--lines": fit.lines } as React.CSSProperties}
        >
          {text}
        </p>
      </div>
      {(open || fit.overflows) && (
        <Button
          variant="link"
          size="sm"
          className="-mt-2 -ml-2.5 self-start"
          onClick={() => setOpen(!open)}
        >
          {open ? "Show less" : "Read the whole description"}
        </Button>
      )}
    </Section>
  )
}

function PriceHistory({ history }: { history: HouseDetail["history"] }) {
  return (
    <Section title="Price history">
      <Table>
        <TableBody>
          {history.map((row, i) => {
            const previous = i > 0 ? history[i - 1].price : null
            const delta = previous && row.price ? row.price - previous : 0
            return (
              <TableRow key={row.id}>
                <TableCell>
                  <span className="font-mono text-xs">
                    {row.observed_at.slice(0, 10)}
                  </span>
                </TableCell>
                <TableCell>
                  <span className="font-mono">{euro(row.price)}</span>
                </TableCell>
                <TableCell>
                  {delta < 0 && (
                    <span className="font-mono text-xs text-energy-a">
                      −{euro(-delta)}
                    </span>
                  )}
                  {delta > 0 && (
                    <span className="font-mono text-xs text-destructive">
                      +{euro(delta)}
                    </span>
                  )}
                </TableCell>
                <TableCell>
                  <span className="text-xs text-muted-foreground">
                    {statusLabel(row.status)}
                  </span>
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </Section>
  )
}

function DetailsSkeleton() {
  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="flex flex-col gap-3 lg:col-span-2">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-2/3" />
      </div>
      <Skeleton className="aspect-4/3 w-full" />
    </div>
  )
}
