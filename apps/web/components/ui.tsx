/**
 * Shared UI primitives — Card, Button, Pill, Stat, Field, EmptyState,
 * Segmented, Skeleton.
 *
 * Phase Design-A — restyled for the premium climate-SaaS direction
 * (soft elevation, rounded surfaces, refined badges, calm green
 * accent). Public prop shapes are unchanged so existing call-sites
 * keep working; only the Tailwind classes changed.
 */
import { type ReactNode } from "react";

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-2xl border border-line bg-white/90 p-5 shadow-card backdrop-blur-sm ${className}`}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between border-b border-line-soft pb-3">
      <div>
        <h2 className="text-base font-semibold tracking-tight text-forest-900">
          {title}
        </h2>
        {subtitle && (
          <p className="mt-0.5 text-xs text-ink-muted">{subtitle}</p>
        )}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

export function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
}) {
  return (
    <div>
      <div className="text-xs font-medium uppercase tracking-wider text-ink-soft">
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold tracking-tight text-forest-900">
        {value}
      </div>
      {hint && <div className="mt-1 text-xs text-ink-muted">{hint}</div>}
    </div>
  );
}

export function Pill({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "brand" | "warn" | "ok" | "error";
  children: ReactNode;
}) {
  // Refined badges: soft tinted background + a 1px ring of the same
  // hue for definition on white surfaces.
  const cls = {
    neutral: "bg-line-soft text-ink-muted ring-1 ring-line",
    brand: "bg-mint-100 text-brand-700 ring-1 ring-brand-200",
    warn: "bg-warn-50 text-warn-700 ring-1 ring-warn-100",
    ok: "bg-mint-100 text-brand-700 ring-1 ring-brand-200",
    error: "bg-danger-50 text-danger-700 ring-1 ring-danger-100",
  }[tone];
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${cls}`}
    >
      {children}
    </span>
  );
}

export function Button({
  children,
  variant = "primary",
  disabled,
  type = "button",
  onClick,
  className = "",
}: {
  children: ReactNode;
  variant?: "primary" | "secondary" | "ghost" | "danger";
  disabled?: boolean;
  type?: "button" | "submit";
  onClick?: () => void;
  className?: string;
}) {
  const base =
    "inline-flex items-center justify-center gap-1.5 rounded-xl px-3.5 py-1.5 text-sm font-medium transition-all duration-150 disabled:cursor-not-allowed disabled:opacity-50 active:scale-[0.98]";
  const variants = {
    primary:
      "bg-brand-600 text-white shadow-soft hover:bg-brand-700 hover:shadow-card",
    secondary:
      "border border-line bg-white text-forest-700 hover:border-brand-200 hover:bg-mint-50",
    ghost: "text-forest-700 hover:bg-mint-100",
    danger:
      "border border-danger-100 bg-danger-50 text-danger-700 hover:bg-danger-100",
  };
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={`${base} ${variants[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-forest-700">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && <div className="mt-1 text-xs text-ink-muted">{hint}</div>}
    </label>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-dashed border-line bg-mint-50/60 p-8 text-center">
      <div className="text-sm font-semibold text-forest-900">{title}</div>
      <p className="mt-1 text-sm text-ink-muted">{description}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/**
 * Phase Design-A — segmented control for toggles (Vue / Méthodologie /
 * Confiance). A polished pill-group with a sliding-feel active state.
 */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  size = "md",
}: {
  options: { value: T; label: ReactNode }[];
  value: T;
  onChange: (v: T) => void;
  size?: "sm" | "md";
}) {
  const pad = size === "sm" ? "px-2 py-0.5 text-xs" : "px-3 py-1 text-sm";
  return (
    <div className="inline-flex items-center gap-0.5 rounded-xl border border-line bg-white/70 p-0.5 shadow-soft">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            onClick={() => onChange(o.value)}
            className={
              `rounded-lg font-medium transition-all duration-150 ${pad} ` +
              (active
                ? "bg-brand-600 text-white shadow-soft"
                : "text-ink-muted hover:bg-mint-100 hover:text-forest-700")
            }
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** Phase Design-A — skeleton placeholder for loading states. */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`shimmer rounded-lg ${className}`} />;
}
