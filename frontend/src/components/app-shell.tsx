import { useQuery } from "@tanstack/react-query"
import { Link, Outlet, useRouterState } from "@tanstack/react-router"
import { SearchIcon } from "lucide-react"
import * as React from "react"

import { CommandMenu } from "@/components/command-menu"
import { ThemeMenu } from "@/components/theme-menu"
import { Button } from "@/components/ui/button"
import { Kbd, KbdGroup } from "@/components/ui/kbd"
import { indexQuery, type Offering } from "@/lib/data"
import { since } from "@/lib/format"
import { useHotkeys } from "@/lib/hotkeys"

const isMac =
  typeof navigator !== "undefined" &&
  /mac|iphone|ipad/i.test(navigator.platform)

export function AppShell() {
  const [commandOpen, setCommandOpen] = React.useState(false)
  useHotkeys({ "mod+k": () => setCommandOpen((open) => !open) })

  return (
    <div className="flex min-h-svh flex-col">
      <header className="sticky top-0 z-40 flex h-12 shrink-0 items-center gap-3 border-b bg-background/85 px-4 backdrop-blur-md">
        <Link
          to="/"
          className="flex items-center gap-2 font-heading text-sm font-semibold tracking-tight"
        >
          <img src="/favicon.svg" alt="" className="size-5" />
          Housemaster
        </Link>
        <CacheStatus />
        <div className="ml-auto flex items-center gap-1.5">
          <Button
            variant="outline"
            size="sm"
            className="hidden sm:inline-flex"
            onClick={() => setCommandOpen(true)}
          >
            <SearchIcon data-icon="inline-start" />
            Jump to a house
            <KbdGroup>
              <Kbd>{isMac ? "⌘" : "Ctrl"}</Kbd>
              <Kbd>K</Kbd>
            </KbdGroup>
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            className="sm:hidden"
            aria-label="Jump to a house"
            onClick={() => setCommandOpen(true)}
          >
            <SearchIcon />
          </Button>
          <ThemeMenu />
        </div>
      </header>
      <Outlet />
      <CommandMenu open={commandOpen} onOpenChange={setCommandOpen} />
    </div>
  )
}

/** "4 170 cached · updated 3 hours ago", for whichever side is on screen. */
function CacheStatus() {
  const offering = useRouterState({
    select: (s) =>
      ((s.location.search as { offering?: Offering }).offering ??
        "buy") as Offering,
  })
  const { data } = useQuery(indexQuery(offering))
  if (!data) return null
  return (
    <span className="hidden items-center gap-2 text-xs text-muted-foreground md:flex">
      <span>
        <span className="font-mono text-foreground">
          {data.meta.counts.active}
        </span>{" "}
        cached
      </span>
      {data.meta.lastRun ? (
        <>
          <span aria-hidden>·</span>
          <span>updated {since(data.meta.lastRun)}</span>
        </>
      ) : null}
    </span>
  )
}
