import * as React from "react"

/** Single-key shortcuts, Linear style, that stand aside while you type. */
export function isTyping(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false
  return (
    target.isContentEditable ||
    target.closest("input, textarea, select") !== null
  )
}

type Handler = (event: KeyboardEvent) => void

/** `keys` maps `event.key` to a handler. Modifier chords are left alone unless
 *  the key names them (`mod+k` means ⌘K on a Mac and Ctrl+K elsewhere). */
export function useHotkeys(
  keys: Record<string, Handler | undefined>,
  enabled = true
) {
  const latest = React.useRef(keys)
  React.useEffect(() => {
    latest.current = keys
  })

  React.useEffect(() => {
    if (!enabled) return undefined
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing) return
      const mod = event.metaKey || event.ctrlKey
      const name = mod ? `mod+${event.key.toLowerCase()}` : event.key
      if (!mod && (event.altKey || isTyping(event.target))) return
      const handler = latest.current[name]
      if (!handler) return
      event.preventDefault()
      handler(event)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [enabled])
}
