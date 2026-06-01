/**
 * Phase Product-UX-B — number formatting helpers for the report.
 *
 * Backend numbers arrive as high-precision strings
 * (e.g. "175266.95000000", "19.21496323"). These helpers render them
 * cleanly — no useless trailing zeros — with locale-aware thousands
 * separators (French by default: narrow no-break space).
 */

function _toNumber(value: number | string | null | undefined): number | null {
  if (value == null) return null;
  const n = typeof value === "string" ? Number(value) : value;
  return Number.isFinite(n) ? n : null;
}

/** Format a number with up to ``decimals`` fraction digits, trimming
 *  trailing zeros, using locale grouping. */
export function formatNumber(
  value: number | string | null | undefined,
  { decimals = 0, locale = "fr-FR" }: { decimals?: number; locale?: string } = {},
): string {
  const n = _toNumber(value);
  if (n == null) return "—";
  return n.toLocaleString(locale, {
    maximumFractionDigits: decimals,
    minimumFractionDigits: 0,
  });
}

/** Kilograms — integer for large values, 1 decimal under 1000. */
export function formatKg(
  value: number | string | null | undefined,
  { locale = "fr-FR" }: { locale?: string } = {},
): string {
  const n = _toNumber(value);
  if (n == null) return "—";
  const decimals = Math.abs(n) >= 1000 ? 0 : 1;
  return `${formatNumber(n, { decimals, locale })} kg`;
}

/** Percentage — one decimal, trailing zero trimmed, ``%`` suffix.
 *  Accepts either a 0–100 value or, when ``fraction`` is true, a 0–1
 *  ratio. */
export function formatPct(
  value: number | string | null | undefined,
  { fraction = false, locale = "fr-FR" }: { fraction?: boolean; locale?: string } = {},
): string {
  const n = _toNumber(value);
  if (n == null) return "—";
  const pct = fraction ? n * 100 : n;
  return `${formatNumber(pct, { decimals: 1, locale })} %`;
}

/** A 0–1 ratio rendered as a percentage. */
export function formatRatio(
  value: number | string | null | undefined,
  opts: { locale?: string } = {},
): string {
  return formatPct(value, { fraction: true, ...opts });
}

/** Signed gap vs a target (in percentage points), e.g. "+3.2 pts" /
 *  "−1.5 pts". Returns null when either input is missing. */
export function formatGapPts(
  actualPct: number | string | null | undefined,
  targetPct: number | string | null | undefined,
  { locale = "fr-FR" }: { locale?: string } = {},
): string | null {
  const a = _toNumber(actualPct);
  const t = _toNumber(targetPct);
  if (a == null || t == null) return null;
  const gap = a - t;
  const sign = gap > 0 ? "+" : gap < 0 ? "−" : "";
  return `${sign}${formatNumber(Math.abs(gap), { decimals: 1, locale })} pts`;
}
