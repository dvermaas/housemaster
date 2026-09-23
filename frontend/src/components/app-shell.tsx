import { Link, Outlet } from "@tanstack/react-router"
import { SearchIcon } from "lucide-react"
import * as React from "react"

import { CommandMenu } from "@/components/command-menu"
import { Button } from "@/components/ui/button"
import { Kbd, KbdGroup } from "@/components/ui/kbd"
import { useHotkeys } from "@/lib/hotkeys"

const isMac =
  typeof navigator !== "undefined" &&
  /mac|iphone|ipad/i.test(navigator.platform)

/** Opens the ⌘K palette from wherever its button is rendered. */
const CommandMenuContext = React.createContext<() => void>(() => {})

export function AppShell() {
  const [commandOpen, setCommandOpen] = React.useState(false)
  useHotkeys({ "mod+k": () => setCommandOpen((open) => !open) })
  const openCommand = React.useCallback(() => setCommandOpen(true), [])

  return (
    <CommandMenuContext value={openCommand}>
      <div className="flex min-h-svh flex-col">
        <Outlet />
        <CommandMenu open={commandOpen} onOpenChange={setCommandOpen} />
      </div>
    </CommandMenuContext>
  )
}

/** The logo and name. There is no header: it heads the filter rail. */
export function Brand() {
  return (
    <Link
      to="/"
      className="flex items-center gap-2 font-heading text-sm font-semibold tracking-tight"
    >
      <img src="/favicon.svg" alt="" className="size-5" />
      Housemaster
    </Link>
  )
}

export function CommandMenuButton() {
  const open = React.useContext(CommandMenuContext)
  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="hidden sm:inline-flex"
        onClick={open}
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
        onClick={open}
      >
        <SearchIcon />
      </Button>
    </>
  )
}
