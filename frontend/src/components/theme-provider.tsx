import * as React from "react"

type Theme = "dark" | "light" | "system"
type ResolvedTheme = "dark" | "light"

type ThemeProviderProps = {
  children: React.ReactNode
  defaultTheme?: Theme
  /** Must match the pre-paint script in index.html. */
  storageKey?: string
}

type ThemeProviderState = {
  theme: Theme
  /** What is actually on screen. The map needs this, not `theme`: it picks a
   *  basemap, and "system" is not a basemap. */
  resolvedTheme: ResolvedTheme
  setTheme: (theme: Theme) => void
}

const COLOR_SCHEME_QUERY = "(prefers-color-scheme: dark)"
const THEMES = new Set<Theme>(["dark", "light", "system"])

const ThemeProviderContext = React.createContext<
  ThemeProviderState | undefined
>(undefined)

const isTheme = (value: string | null): value is Theme =>
  value !== null && THEMES.has(value as Theme)

function readStored(key: string, fallback: Theme): Theme {
  try {
    const stored = localStorage.getItem(key)
    return isTheme(stored) ? stored : fallback
  } catch {
    return fallback // private mode: follow the system
  }
}

function writeStored(key: string, theme: Theme) {
  try {
    localStorage.setItem(key, theme)
  } catch {
    // private mode: the choice lasts as long as the tab
  }
}

/** The OS preference, as live state. */
function useSystemDark() {
  return React.useSyncExternalStore(
    (onChange) => {
      const query = window.matchMedia(COLOR_SCHEME_QUERY)
      query.addEventListener("change", onChange)
      return () => query.removeEventListener("change", onChange)
    },
    () => window.matchMedia(COLOR_SCHEME_QUERY).matches
  )
}

/** Swapping every colour at once would otherwise animate each transition in
 *  the page separately, which reads as a flicker rather than a switch. */
function withoutTransitions(apply: () => void) {
  const style = document.createElement("style")
  style.append("*,*::before,*::after{transition:none!important}")
  document.head.append(style)
  apply()
  window.getComputedStyle(document.body)
  requestAnimationFrame(() => requestAnimationFrame(() => style.remove()))
}

function isEditableTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false
  return (
    target.isContentEditable ||
    target.closest("input, textarea, select, [contenteditable='true']") !== null
  )
}

export function ThemeProvider({
  children,
  defaultTheme = "system",
  storageKey = "theme",
}: ThemeProviderProps) {
  const [theme, setThemeState] = React.useState<Theme>(() =>
    readStored(storageKey, defaultTheme)
  )
  const systemDark = useSystemDark()
  const resolvedTheme: ResolvedTheme =
    theme === "system" ? (systemDark ? "dark" : "light") : theme

  const setTheme = React.useCallback(
    (next: Theme) => {
      writeStored(storageKey, next)
      setThemeState(next)
    },
    [storageKey]
  )

  React.useLayoutEffect(() => {
    const root = document.documentElement
    if (root.classList.contains(resolvedTheme)) return
    withoutTransitions(() => {
      root.classList.remove("light", "dark")
      root.classList.add(resolvedTheme)
    })
  }, [resolvedTheme])

  // `d` flips light and dark, from wherever you are.
  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.repeat || event.metaKey || event.ctrlKey || event.altKey) return
      if (isEditableTarget(event.target) || event.key.toLowerCase() !== "d")
        return
      setTheme(resolvedTheme === "dark" ? "light" : "dark")
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [resolvedTheme, setTheme])

  // Another tab changed it.
  React.useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== storageKey) return
      setThemeState(isTheme(event.newValue) ? event.newValue : defaultTheme)
    }
    window.addEventListener("storage", onStorage)
    return () => window.removeEventListener("storage", onStorage)
  }, [defaultTheme, storageKey])

  const value = React.useMemo(
    () => ({ theme, resolvedTheme, setTheme }),
    [theme, resolvedTheme, setTheme]
  )
  return (
    <ThemeProviderContext.Provider value={value}>
      {children}
    </ThemeProviderContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components -- the hook belongs with its provider
export const useTheme = () => {
  const context = React.useContext(ThemeProviderContext)
  if (context === undefined)
    throw new Error("useTheme must be used within a ThemeProvider")
  return context
}
