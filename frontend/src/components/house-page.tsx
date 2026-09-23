import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate, useParams } from "@tanstack/react-router"
import {
  ArrowLeftIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  ExternalLinkIcon,
  ImagesIcon,
  MapPinIcon,
} from "lucide-react"
import * as React from "react"

import { Lightbox } from "@/components/lightbox"
import { NotFoundPage } from "@/components/not-found"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
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

const GALLERY_PREVIEW = 5

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
        {around && (
          <div className="ml-auto flex items-center gap-1">
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
  }, [features])

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="flex flex-col gap-6 lg:col-span-2">
        {listing.description && <Description text={listing.description} />}

        {groups.length > 0 && (
          <section className="flex flex-col gap-3">
            <h2 className="font-heading text-lg font-semibold">Kenmerken</h2>
            <div className="grid gap-4 sm:grid-cols-2">
              {groups.map(([group, rows]) => (
                <Card key={group} size="sm">
                  <CardHeader>
                    <CardTitle>{group}</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-sm">
                      {rows.map((row) => (
                        <React.Fragment key={`${row.label}-${row.value}`}>
                          <dt className="text-muted-foreground">{row.label}</dt>
                          <dd>{row.value}</dd>
                        </React.Fragment>
                      ))}
                    </dl>
                  </CardContent>
                </Card>
              ))}
            </div>
          </section>
        )}
      </div>

      <aside className="flex flex-col gap-4">
        {history.length > 1 && <PriceHistory history={history} />}

        <Card size="sm">
          <CardHeader>
            <CardTitle>Buurt</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-sm">
              {listing.neighbourhood_price_m2 ? (
                <>
                  <dt className="text-muted-foreground">
                    Average asking price
                  </dt>
                  <dd className="font-mono">
                    {euro(listing.neighbourhood_price_m2)}/m²
                  </dd>
                  <dt className="text-muted-foreground">This house</dt>
                  <dd className="font-mono">
                    {euro(listing.price_per_m2)}
                    {perM2(offering)}
                  </dd>
                </>
              ) : null}
              {listing.neighbourhood_inhabitants ? (
                <>
                  <dt className="text-muted-foreground">Inhabitants</dt>
                  <dd className="font-mono">
                    {compact(listing.neighbourhood_inhabitants)}
                  </dd>
                </>
              ) : null}
            </dl>
            {offering === "rent" && listing.neighbourhood_price_m2 ? (
              <p className="mt-2 text-xs text-muted-foreground">
                Funda publishes the buurt average as a purchase price, so it is
                not a rent benchmark.
              </p>
            ) : null}
          </CardContent>
        </Card>

        <Card size="sm">
          <CardHeader>
            <CardTitle>Listing</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-col gap-3">
              <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-sm">
                <dt className="text-muted-foreground">Agent</dt>
                <dd>{listing.agent}</dd>
                <dt className="text-muted-foreground">Published</dt>
                <dd className="font-mono">
                  {publishedLabel(listing.published)}
                </dd>
                <dt className="text-muted-foreground">First seen</dt>
                <dd>{since(isoToEpoch(listing.first_seen_at))}</dd>
              </dl>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  size="sm"
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
                {listing.lat !== null && listing.lng !== null && (
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
                )}
                <Button
                  variant="ghost"
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
                  Show on map
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </aside>
    </div>
  )
}

/** Descriptions run to several screens. The first few lines say most of it. */
function Description({ text }: { text: string }) {
  const [open, setOpen] = React.useState(false)
  const long = text.length > 700
  return (
    <section className="flex flex-col gap-3">
      <h2 className="font-heading text-lg font-semibold">Omschrijving</h2>
      <Collapsible open={open || !long} onOpenChange={setOpen}>
        <div
          className={
            open || !long
              ? "text-sm leading-relaxed whitespace-pre-line"
              : "line-clamp-8 text-sm leading-relaxed whitespace-pre-line"
          }
        >
          {text}
        </div>
        <CollapsibleContent />
        {long && (
          <CollapsibleTrigger
            render={
              <Button variant="link" size="sm" className="mt-1 -ml-2.5" />
            }
          >
            {open ? "Show less" : "Read the whole description"}
          </CollapsibleTrigger>
        )}
      </Collapsible>
    </section>
  )
}

function PriceHistory({ history }: { history: HouseDetail["history"] }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>Price history</CardTitle>
      </CardHeader>
      <CardContent>
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
      </CardContent>
    </Card>
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
      <Skeleton className="h-48 w-full" />
    </div>
  )
}
