import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react"
import * as React from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"
import { photoUrl } from "@/lib/data"

/** The photo viewer: the whole set, navigable with the arrow keys. */
export function Lightbox({
  photos,
  index,
  onIndexChange,
  onClose,
  title,
}: {
  photos: string[]
  index: number | null
  onIndexChange: (index: number) => void
  onClose: () => void
  title: string
}) {
  const open = index !== null
  const at = index ?? 0
  const go = React.useCallback(
    (step: number) =>
      onIndexChange(Math.max(0, Math.min(photos.length - 1, at + step))),
    [at, onIndexChange, photos.length]
  )

  // Decode the neighbours ahead of time, so an arrow press shows the next
  // photo at once instead of a blank frame while it downloads.
  React.useEffect(() => {
    if (!open) return
    for (const i of [at - 1, at + 1]) {
      if (photos[i]) new Image().src = photoUrl(photos[i], 1440)
    }
  }, [open, at, photos])

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent
        size="fullscreen"
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") go(-1)
          else if (event.key === "ArrowRight") go(1)
        }}
      >
        <div className="flex h-12 shrink-0 items-center gap-3 border-b px-4 pr-12">
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {at + 1} of {photos.length}
          </DialogDescription>
        </div>
        <div className="relative flex min-h-0 flex-1 items-center justify-center p-4">
          {open && photos[at] && (
            <img
              key={photos[at]}
              src={photoUrl(photos[at], 1440)}
              alt={`${title}, ${at + 1} of ${photos.length}`}
              referrerPolicy="no-referrer"
              className="max-h-full max-w-full object-contain"
            />
          )}
          <Button
            variant="secondary"
            size="icon-lg"
            className="absolute left-4"
            aria-label="Previous photo"
            disabled={at === 0}
            onClick={() => go(-1)}
          >
            <ChevronLeftIcon />
          </Button>
          <Button
            variant="secondary"
            size="icon-lg"
            className="absolute right-4"
            aria-label="Next photo"
            disabled={at >= photos.length - 1}
            onClick={() => go(1)}
          >
            <ChevronRightIcon />
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
