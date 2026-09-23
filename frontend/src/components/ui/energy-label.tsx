import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "cn"

/** The Dutch NEN energy scale, best to worst. The rail renders all of it and
 *  greys out the steps no house has, so the ladder keeps its meaning. */
export const ENERGY_SCALE = [
  "A+++++", "A++++", "A+++", "A++", "A+", "A", "B", "C", "D", "E", "F", "G",
] as const // prettier-ignore

type Step =
  | "a5"
  | "a4"
  | "a3"
  | "a2"
  | "a1"
  | "a"
  | "b"
  | "c"
  | "d"
  | "e"
  | "f"
  | "g"
  | "unknown"

const STEP: Record<string, Step> = {
  "A+++++": "a5", "A++++": "a4", "A+++": "a3", "A++": "a2", "A+": "a1",
  A: "a", B: "b", C: "c", D: "d", E: "e", F: "f", G: "g",
} // prettier-ignore

export const energyStep = (label: string | null | undefined): Step =>
  STEP[(label ?? "").trim()] ?? "unknown"

const energyLabelVariants = cva(
  "inline-flex shrink-0 items-center justify-center rounded-sm font-mono font-semibold tabular-nums",
  {
    variants: {
      // Real certificate colours. C and D are pale yellows, so they take dark
      // ink; everything else takes white.
      step: {
        a5: "bg-energy-a5 text-energy-ink",
        a4: "bg-energy-a4 text-energy-ink",
        a3: "bg-energy-a3 text-energy-ink",
        a2: "bg-energy-a2 text-energy-ink",
        a1: "bg-energy-a1 text-energy-ink",
        a: "bg-energy-a text-energy-ink",
        b: "bg-energy-b text-energy-ink",
        c: "bg-energy-c text-energy-ink-dark",
        d: "bg-energy-d text-energy-ink-dark",
        e: "bg-energy-e text-energy-ink",
        f: "bg-energy-f text-energy-ink",
        g: "bg-energy-g text-energy-ink",
        unknown: "bg-energy-unknown text-energy-ink-dark",
      },
      size: {
        sm: "h-5 min-w-5 px-1 text-xs",
        default: "h-6 min-w-7 px-1.5 text-xs",
        lg: "h-8 min-w-10 px-2 text-sm",
      },
    },
    defaultVariants: { step: "unknown", size: "default" },
  }
)

function EnergyLabel({
  label,
  size,
  className,
  ...props
}: Omit<React.ComponentProps<"span">, "children"> &
  Omit<VariantProps<typeof energyLabelVariants>, "step"> & {
    label: string | null | undefined
  }) {
  return (
    <span
      data-slot="energy-label"
      title={label ? `Energy label ${label}` : "No energy label"}
      className={cn(
        energyLabelVariants({ step: energyStep(label), size }),
        className
      )}
      {...props}
    >
      {label || "?"}
    </span>
  )
}

export { EnergyLabel, energyLabelVariants }
