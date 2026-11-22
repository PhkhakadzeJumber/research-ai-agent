/**
 * Eureka's basic components, matching the Figma components (page "Components"): Button (Primary / Secondary / Ghost
 * / Accent), Pill (Neutral / Green / Amber / Red / Indigo / Violet / Spark), Card, Logo, MonoLabel, the form
 * fields and the segmented choice. Step 10a.
 */
import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";

const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ");
export { cx };

// --- Button --------------------------------------------------------------------------------------------------

export type ButtonKind = "primary" | "secondary" | "ghost" | "accent";

const BUTTON: Record<ButtonKind, string> = {
  primary: "bg-ink text-paper hover:bg-ink/85",
  secondary: "bg-surface text-ink border border-line hover:border-ink-3",
  ghost: "text-ink hover:bg-surface-muted",
  accent: "bg-accent text-ink hover:brightness-95",
};

export const buttonClass = (kind: ButtonKind, extra?: string) =>
  cx(
    "inline-flex items-center justify-center gap-2 rounded-pill px-5 py-2.5 text-sm font-semibold transition",
    "disabled:cursor-not-allowed disabled:opacity-50",
    BUTTON[kind],
    extra,
  );

export function Button({ kind = "primary", className, ...props }: ComponentProps<"button"> & { kind?: ButtonKind }) {
  return <button type="button" className={buttonClass(kind, className)} {...props} />;
}

export function ButtonLink({ kind = "primary", className, ...props }: ComponentProps<typeof Link> & { kind?: ButtonKind }) {
  return <Link className={buttonClass(kind, className)} {...props} />;
}

// --- Pill ----------------------------------------------------------------------------------------------------

export type Tone = "neutral" | "green" | "amber" | "red" | "indigo" | "violet" | "spark";

const PILL: Record<Tone, [string, string]> = {
  neutral: ["bg-surface-muted text-ink-2", "bg-ink-3"],
  green: ["bg-green-soft text-green", "bg-green"],
  amber: ["bg-amber-soft text-amber", "bg-amber"],
  red: ["bg-red-soft text-red", "bg-red"],
  indigo: ["bg-indigo-soft text-indigo", "bg-indigo"],
  violet: ["bg-violet-soft text-violet", "bg-violet"],
  spark: ["bg-accent-soft text-ink", "bg-accent"],
};

export function Pill({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  const [colors, dot] = PILL[tone];
  return (
    <span className={cx("inline-flex items-center gap-1.5 rounded-pill px-2.5 py-0.5 label-s whitespace-nowrap", colors, className)}>
      <span aria-hidden className={cx("size-1.5 rounded-full", dot)} />
      {children}
    </span>
  );
}

// --- Card, labels --------------------------------------------------------------------------------------------

const CARD_TONES = {
  plain: "border border-line bg-surface",
  dark: "bg-ink text-paper",
  accent: "bg-accent-soft",
  muted: "bg-surface-muted",
};

/** A card: white with a border by default; `tone` gives the design's dark, yellow (accent) and grey (muted) cards.
 *  (A tone, not a background class in className: two background classes would fight and the default could win.) */
export function Card({ className, dark, tone, ...props }: ComponentProps<"div"> & { dark?: boolean; tone?: keyof typeof CARD_TONES }) {
  return <div className={cx("rounded-lg p-7", CARD_TONES[tone ?? (dark ? "dark" : "plain")], className)} {...props} />;
}

export function MonoLabel({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("label-mono text-ink-3", className)}>{children}</div>;
}

export function Logo({ className }: { className?: string }) {
  return (
    <span className={cx("inline-flex items-center gap-2", className)}>
      <svg aria-hidden width="22" height="22" viewBox="0 0 22 22">
        <circle cx="11" cy="11" r="11" fill="var(--color-accent)" />
        <path d="M11 5 L12.4 9.6 L17 11 L12.4 12.4 L11 17 L9.6 12.4 L5 11 L9.6 9.6 Z" fill="var(--color-ink)" />
      </svg>
      <span className="font-serif text-2xl italic leading-none">Eureka</span>
    </span>
  );
}

// --- Form fields ---------------------------------------------------------------------------------------------

export function Field({ label, hint, error, children }: { label: string; hint?: ReactNode; error?: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="label-s text-[13px] text-ink">{label}</span>
      {children}
      {error ? <span className="body-s text-red">{error}</span> : hint ? <span className="body-s text-ink-3">{hint}</span> : null}
    </label>
  );
}

const inputClass =
  "w-full rounded-md border border-line bg-surface px-3.5 py-2.5 body-m text-ink placeholder:text-ink-3 outline-none focus:border-ink-3";

export function Input({ className, ...props }: ComponentProps<"input">) {
  return <input className={cx(inputClass, className)} {...props} />;
}

export function TextArea({ className, ...props }: ComponentProps<"textarea">) {
  return <textarea className={cx(inputClass, "resize-y", className)} {...props} />;
}

export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
  hard,
}: {
  label: string;
  options: { value: T; label: string }[];
  value: T | null | undefined;
  onChange: (value: T | null) => void;
  hard?: boolean;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="label-s text-[13px] text-ink">{label}</span>
        {hard ? <span className="label-mono rounded-sm bg-red-soft px-1.5 text-red">Hard limit</span> : null}
      </div>
      <div role="radiogroup" aria-label={label} className="flex w-fit flex-wrap gap-1 rounded-pill bg-surface-muted p-1">
        {options.map((option) => {
          const on = option.value === value;
          return (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={on}
              onClick={() => onChange(on ? null : option.value)}
              className={cx("rounded-pill px-3.5 py-1.5 label-s text-[13px] transition", on ? "bg-ink text-paper" : "text-ink-2 hover:text-ink")}
            >
              {option.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : "Something went wrong.";
  const requestId = (error as { requestId?: string | null }).requestId;
  return (
    <div role="alert" className="rounded-md bg-red-soft px-4 py-3 body-s text-red">
      {message}
      {requestId ? <span className="ml-1 font-mono text-[11px] opacity-70">(request {requestId.slice(0, 8)})</span> : null}
    </div>
  );
}
