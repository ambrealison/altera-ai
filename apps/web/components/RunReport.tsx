"use client";

/**
 * Phase Product-UX-B/E — shared, demo-ready run report.
 *
 * Renders a ``ReportDocument`` (PT and/or WWF sections + executive
 * summary) as a beautiful in-workflow report. Pure presentation: no
 * fetching, no calculation — it formats existing backend numbers.
 * Phase Product-UX-E: all UI labels go through the i18n dictionary
 * (report.* / common.*); food-group / bucket labels resolve via t().
 *
 * Handles all three methodology variants gracefully: PT-only, WWF-only,
 * and PT+WWF.
 */

import { useState } from "react";

import { Card, Pill, Segmented } from "@/components/ui";
import { formatKg, formatPct, formatGapPts } from "@/lib/format";
import { useT } from "@/lib/i18n";
import type {
  PTReportSection,
  ReportDocument,
  WWFReportSection,
} from "@/lib/api";

// Phase Product-UX-F — official WWF retailer methodology PDF.
const WWF_METHODOLOGY_PDF =
  "https://wwfint.awsassets.panda.org/downloads/wwf-planet-based-diets-retailer-methodology.pdf";

// Group / food-group / bucket display metadata. Labels are i18n keys
// (resolved via t() at render); emojis are decorative and stay inline.
const PT_GROUP_META: Record<string, { key: string; emoji: string }> = {
  plant_based_core: { key: "report.ptGroup.plant_based_core", emoji: "🌱" },
  plant_based_non_core: { key: "report.ptGroup.plant_based_non_core", emoji: "🥗" },
  animal_core: { key: "report.ptGroup.animal_core", emoji: "🐄" },
  composite_products: { key: "report.ptGroup.composite_products", emoji: "🍽️" },
  out_of_scope: { key: "report.ptGroup.out_of_scope", emoji: "▫️" },
  unknown: { key: "report.ptGroup.unknown", emoji: "❔" },
};

const WWF_FG_META: Record<string, { key: string; emoji: string }> = {
  FG1: { key: "report.fg.FG1", emoji: "🍗" },
  FG2: { key: "report.fg.FG2", emoji: "🧀" },
  FG3: { key: "report.fg.FG3", emoji: "🫒" },
  FG4: { key: "report.fg.FG4", emoji: "🥕" },
  FG5: { key: "report.fg.FG5", emoji: "🌾" },
  FG6: { key: "report.fg.FG6", emoji: "🥔" },
  FG7: { key: "report.fg.FG7", emoji: "⚠️" },
  out_of_scope: { key: "report.fg.out_of_scope", emoji: "▫️" },
  unknown: { key: "report.fg.unknown", emoji: "❔" },
};

function Bar({ pct, tone = "brand" }: { pct: number; tone?: "brand" | "warn" | "neutral" }) {
  const w = Math.max(0, Math.min(100, pct));
  const cls =
    tone === "warn"
      ? "from-warn-400 to-warn-500"
      : tone === "neutral"
        ? "from-ink-soft to-ink-muted"
        : "from-brand-400 to-brand-600";
  return (
    <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-line-soft">
      <div
        className={`h-full rounded-full bg-gradient-to-r ${cls} transition-all duration-500`}
        style={{ width: `${w}%` }}
      />
    </div>
  );
}

function Kpi({
  label,
  value,
  sub,
  tone = "neutral",
}: {
  label: string;
  value: string;
  sub?: string | null;
  tone?: "brand" | "warn" | "neutral";
}) {
  const ring =
    tone === "brand"
      ? "ring-brand-200 bg-mint-50"
      : tone === "warn"
        ? "ring-warn-100 bg-warn-50"
        : "ring-line bg-white";
  return (
    <div className={`rounded-2xl px-4 py-3 ring-1 ${ring}`}>
      <p className="text-xs text-ink-soft">{label}</p>
      <p className="mt-0.5 text-xl font-semibold text-forest-900">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-ink-muted">{sub}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Top-N product contributors (Phase Product-UX-C)
// ---------------------------------------------------------------------------
type ContributorItem = {
  id: string;
  name: string;
  category: string | null;
  value: string;
  rationale: string;
};

function ContributorList({
  title,
  emoji,
  tone,
  items,
  emptyText,
}: {
  title: string;
  emoji: string;
  tone: "brand" | "warn";
  items: ContributorItem[];
  emptyText: string;
}) {
  const rankCls =
    tone === "warn"
      ? "bg-warn-100 text-warn-700"
      : "bg-mint-100 text-forest-700";
  const ring = tone === "warn" ? "ring-warn-100" : "ring-brand-100";
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wide text-ink-soft">
        {emoji} {title}
      </p>
      {items.length === 0 ? (
        <p className="mt-2 rounded-xl bg-line-soft/40 px-3 py-2 text-xs text-ink-muted">
          {emptyText}
        </p>
      ) : (
        <ol className="mt-2 space-y-1.5">
          {items.map((it, i) => (
            <li
              key={it.id}
              className={`flex items-center gap-3 rounded-xl bg-white px-3 py-2 ring-1 ${ring}`}
            >
              <span
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${rankCls}`}
              >
                {i + 1}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-forest-900">
                  {it.name}
                </p>
                <p className="truncate text-[11px] text-ink-soft">
                  {it.rationale}
                  {it.category ? ` · ${it.category}` : ""}
                </p>
              </div>
              <span className="shrink-0 text-sm font-semibold text-forest-900">
                {it.value}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function ContributorsUnavailable() {
  const t = useT();
  return (
    <div className="mt-5">
      <p className="text-xs font-semibold uppercase tracking-wide text-ink-soft">
        🏅 {t("report.contributors.title")}
      </p>
      <p className="mt-2 rounded-xl bg-line-soft/40 px-3 py-2 text-xs text-ink-muted">
        {t("report.contributors.unavailable")}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Protein Tracker section
// ---------------------------------------------------------------------------
function PtSection({ pt }: { pt: PTReportSection }) {
  const t = useT();
  const plantRatioPct = formatPct(pt.plant_share_pct);
  const totalProtein = Number(pt.total_in_scope_protein_kg) || 0;
  const sortedGroups = [...pt.groups].sort(
    (a, b) => (Number(b.protein_kg) || 0) - (Number(a.protein_kg) || 0),
  );
  const topPlant = sortedGroups.find((g) => g.pt_group === "plant_based_core");
  const ptPositive = pt.top_positive_contributors ?? [];
  const ptWatchout = pt.top_watchout_contributors ?? [];
  const ptHasContributors = ptPositive.length > 0 || ptWatchout.length > 0;

  return (
    <Card>
      <h3 className="text-base font-semibold text-forest-900">
        🌱 Protein Tracker
      </h3>
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Kpi
          label={t("report.kpi.plantProtein")}
          value={formatKg(pt.plant_protein_kg)}
          sub={formatPct(pt.plant_share_pct)}
          tone="brand"
        />
        <Kpi
          label={t("report.kpi.animalProtein")}
          value={formatKg(pt.animal_protein_kg)}
          sub={formatPct(pt.animal_share_pct)}
          tone="warn"
        />
        <Kpi
          label={t("report.kpi.totalProtein")}
          value={formatKg(pt.total_in_scope_protein_kg)}
        />
        <Kpi label={t("report.kpi.plantRatio")} value={plantRatioPct} tone="brand" />
      </div>

      {/* Category analysis */}
      <div className="mt-5">
        <p className="text-xs font-semibold uppercase tracking-wide text-ink-soft">
          📊 {t("report.categoryAnalysis")}
        </p>
        <div className="mt-2 space-y-2">
          {sortedGroups.map((g) => {
            const meta = PT_GROUP_META[g.pt_group];
            const label = meta ? t(meta.key) : g.pt_group;
            const emoji = meta?.emoji ?? "•";
            const proteinKg = Number(g.protein_kg) || 0;
            const share = totalProtein > 0 ? (proteinKg / totalProtein) * 100 : 0;
            return (
              <div key={g.pt_group}>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-forest-700">
                    {emoji} {label}
                    <span className="ml-1 text-ink-soft">
                      ({g.item_count})
                    </span>
                  </span>
                  <span className="text-ink-muted">
                    {formatKg(g.protein_kg)} · {formatPct(share)}
                  </span>
                </div>
                <Bar
                  pct={share}
                  tone={g.pt_group === "animal_core" ? "warn" : "brand"}
                />
              </div>
            );
          })}
        </div>
      </div>

      {topPlant && totalProtein > 0 && (
        <div className="mt-4 rounded-xl bg-mint-50 px-3 py-2 text-sm text-forest-700 ring-1 ring-brand-100">
          ✅{" "}
          {t("report.pt.coreInsight")
            .replace("{label}", t("report.ptGroup.plant_based_core"))
            .replace(
              "{pct}",
              formatPct(((Number(topPlant.protein_kg) || 0) / totalProtein) * 100),
            )}
        </div>
      )}

      {/* Top product contributors (Phase Product-UX-C) */}
      {ptHasContributors ? (
        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <ContributorList
            title={t("report.pt.topPositive")}
            emoji="🌱"
            tone="brand"
            emptyText={t("report.pt.emptyPositive")}
            items={ptPositive.map((c) => ({
              id: c.product_id,
              name: c.product_name,
              category: c.retailer_category,
              value: formatKg(c.plant_protein_kg),
              rationale: c.rationale,
            }))}
          />
          <ContributorList
            title={t("report.pt.topWatchout")}
            emoji="🐄"
            tone="warn"
            emptyText={t("report.pt.emptyWatchout")}
            items={ptWatchout.map((c) => ({
              id: c.product_id,
              name: c.product_name,
              category: c.retailer_category,
              value: formatKg(c.animal_protein_kg),
              rationale: c.rationale,
            }))}
          />
        </div>
      ) : (
        <ContributorsUnavailable />
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// WWF section
// ---------------------------------------------------------------------------
function WwfSection({ wwf }: { wwf: WWFReportSection }) {
  const t = useT();
  const compositesTotal = Number(wwf.composites_total_weight_kg) || 0;
  const buckets = [
    { key: "meat", label: t("report.bucket.meat"), emoji: "🥩", kg: wwf.composites_meat_based_kg },
    { key: "seafood", label: t("report.bucket.seafood"), emoji: "🐟", kg: wwf.composites_seafood_based_kg },
    { key: "veg", label: t("report.bucket.vegetarian"), emoji: "🧀", kg: wwf.composites_vegetarian_kg },
    { key: "vegan", label: t("report.bucket.vegan"), emoji: "🌱", kg: wwf.composites_vegan_kg },
  ];
  const wwfPositive = wwf.top_positive_contributors ?? [];
  const wwfWatchout = wwf.top_watchout_contributors ?? [];
  const wwfHasContributors = wwfPositive.length > 0 || wwfWatchout.length > 0;

  return (
    <Card>
      <h3 className="text-base font-semibold text-forest-900">
        🥕 WWF Planet-Based Diets
      </h3>
      {/* Phase Product-UX-D/E — be explicit about the methodology scope. */}
      <div className="mt-2 rounded-xl bg-mint-50/60 px-3 py-2 text-xs leading-relaxed text-forest-700 ring-1 ring-brand-100">
        <span className="font-semibold">{t("report.wwf.step1Label")}</span>{" "}
        {t("report.wwf.step1Body")}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Kpi label={t("report.wwf.kpiVolume")} value={formatKg(wwf.total_in_scope_weight_kg)} tone="brand" />
        <Kpi label={t("report.wwf.kpiComposites")} value={formatKg(wwf.composites_total_weight_kg)} />
        <Kpi
          label={t("report.wwf.kpiVegVegan")}
          value={
            compositesTotal > 0
              ? formatPct(
                  ((Number(wwf.composites_vegetarian_kg) || 0) +
                    (Number(wwf.composites_vegan_kg) || 0)) /
                    compositesTotal *
                    100,
                )
              : "—"
          }
          tone="brand"
        />
      </div>

      {/* Per food group vs PHD target */}
      <div className="mt-5">
        <p className="text-xs font-semibold uppercase tracking-wide text-ink-soft">
          📊 {t("report.wwf.fgVsTarget")}
        </p>
        <div className="mt-2 space-y-2">
          {wwf.per_food_group.map((fg) => {
            const meta = WWF_FG_META[fg.food_group];
            const label = meta ? t(meta.key) : fg.food_group;
            const emoji = meta?.emoji ?? "•";
            const share = Number(fg.share_pct) || 0;
            const target = fg.phd_reference_share_pct;
            const gap = formatGapPts(fg.share_pct, target);
            const below = target != null && share < (Number(target) || 0);
            return (
              <div key={fg.food_group}>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-forest-700">
                    {emoji} {label}
                  </span>
                  <span className="text-ink-muted">
                    {formatKg(fg.weight_kg)} · {formatPct(fg.share_pct)}
                    {target != null && (
                      <span className="ml-1 text-ink-soft">
                        ({t("report.wwf.target")} {formatPct(target)}
                        {gap && (
                          <>
                            ,{" "}
                            <span
                              className={
                                below
                                  ? "font-semibold text-danger-700"
                                  : "font-semibold text-brand-700"
                              }
                            >
                              {gap}
                            </span>
                          </>
                        )}
                        )
                      </span>
                    )}
                  </span>
                </div>
                <Bar
                  pct={share}
                  tone={fg.food_group === "FG7" ? "warn" : "brand"}
                />
              </div>
            );
          })}
        </div>
      </div>

      {/* Composite buckets */}
      {compositesTotal > 0 && (
        <div className="mt-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink-soft">
            🍽️ {t("report.wwf.compositesByBucket")}
          </p>
          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {buckets.map((b) => {
              const kg = Number(b.kg) || 0;
              const share = compositesTotal > 0 ? (kg / compositesTotal) * 100 : 0;
              return (
                <div
                  key={b.key}
                  className="rounded-xl border border-line bg-mint-50/50 px-3 py-2"
                >
                  <p className="text-xs text-ink-soft">
                    {b.emoji} {b.label}
                  </p>
                  <p className="mt-0.5 text-sm font-semibold text-forest-900">
                    {formatKg(b.kg)}
                  </p>
                  <p className="text-[11px] text-ink-muted">{formatPct(share)}</p>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Top product contributors (Phase Product-UX-C) */}
      {wwfHasContributors ? (
        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <ContributorList
            title={t("report.wwf.topAligned")}
            emoji="✅"
            tone="brand"
            emptyText={t("report.wwf.emptyAligned")}
            items={wwfPositive.map((c) => ({
              id: c.product_id,
              name: c.product_name,
              category: c.retailer_category,
              value: formatKg(c.weight_kg),
              rationale: c.rationale,
            }))}
          />
          <ContributorList
            title={t("report.wwf.topWatchout")}
            emoji="⚠️"
            tone="warn"
            emptyText={t("report.wwf.emptyWatchout")}
            items={wwfWatchout.map((c) => ({
              id: c.product_id,
              name: c.product_name,
              category: c.retailer_category,
              value: formatKg(c.weight_kg),
              rationale: c.rationale,
            }))}
          />
        </div>
      ) : (
        <ContributorsUnavailable />
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Top-level report
// ---------------------------------------------------------------------------
export function RunReport({ doc }: { doc: ReportDocument }) {
  const t = useT();
  const both = Boolean(doc.pt_section && doc.wwf_section);
  // Phase Report-Toggle — when a project has BOTH methodologies, the report
  // is a "double report": a segmented toggle switches the detailed view
  // between Protein Tracker and WWF (the hero stays a combined overview).
  const [view, setView] = useState<"pt" | "wwf">("pt");
  return (
    <div className="space-y-4">
      {/* Hero / executive summary */}
      <div className="overflow-hidden rounded-3xl bg-forest-hero p-6 shadow-card">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center rounded-full bg-white/15 px-2.5 py-0.5 text-[11px] font-medium uppercase tracking-wider text-mint-100 ring-1 ring-white/20">
            🎯 {t("report.badge")}
          </span>
          {doc.meta.approval_status && (
            <span className="text-[11px] text-mint-100/80">
              {doc.meta.reporting_period}
            </span>
          )}
        </div>
        <h2 className="mt-2 text-xl font-semibold tracking-tight text-white">
          {doc.meta.project_name}
        </h2>
        {/* Phase Product-UX-F — localized, formatted summary built from
            structured fields (no raw Decimals, no "being prepared"). The
            WWF methodology PDF link appears only when a WWF section
            exists (WWF-only or PT+WWF). */}
        {(doc.pt_section || doc.wwf_section) && (
          <div className="mt-1 max-w-3xl space-y-1 text-sm leading-relaxed text-mint-100/90">
            {doc.pt_section && (
              <p>
                {doc.pt_section.plant_share_pct != null
                  ? t("report.summary.ptRatio")
                      .replace("{ratio}", formatPct(doc.pt_section.plant_share_pct))
                      .replace("{plant}", formatKg(doc.pt_section.plant_protein_kg))
                      .replace("{animal}", formatKg(doc.pt_section.animal_protein_kg))
                      .replace(
                        "{total}",
                        formatKg(doc.pt_section.total_in_scope_protein_kg),
                      )
                  : t("report.summary.ptEmpty")}
              </p>
            )}
            {doc.wwf_section && (
              <p>
                {t("report.summary.wwfLead").replace(
                  "{weight}",
                  formatKg(doc.wwf_section.total_in_scope_weight_kg),
                )}
                <a
                  href={WWF_METHODOLOGY_PDF}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-white underline decoration-mint-100/60 underline-offset-2 hover:decoration-white"
                >
                  {t("report.summary.wwfMethodologyLink")}
                </a>
                {t("report.summary.wwfTail")}
              </p>
            )}
          </div>
        )}
      </div>

      {both && (
        <div className="flex flex-col items-center gap-2 rounded-2xl border border-line bg-white/80 px-4 py-3">
          <p className="text-xs font-medium uppercase tracking-wider text-ink-soft">
            {t("report.toggle.label")}
          </p>
          <Segmented
            value={view}
            onChange={(v) => setView(v)}
            options={[
              { value: "pt", label: `🌱 ${t("report.toggle.pt")}` },
              { value: "wwf", label: `🌍 ${t("report.toggle.wwf")}` },
            ]}
          />
        </div>
      )}

      {/* Double report: when both methodologies exist the toggle picks the
          detailed section; otherwise render whichever single section exists. */}
      {both ? (
        view === "pt" ? (
          <PtSection pt={doc.pt_section!} />
        ) : (
          <WwfSection wwf={doc.wwf_section!} />
        )
      ) : (
        <>
          {doc.pt_section && <PtSection pt={doc.pt_section} />}
          {doc.wwf_section && <WwfSection wwf={doc.wwf_section} />}
        </>
      )}

      {doc.recommendations && doc.recommendations.length > 0 && (
        <Card>
          <h3 className="text-base font-semibold text-forest-900">
            🎯 {t("report.priorities")}
          </h3>
          <ul className="mt-2 space-y-1.5">
            {doc.recommendations.slice(0, 5).map((r, i) => (
              <li key={r.id ?? i} className="flex items-start gap-2 text-sm">
                <Pill
                  tone={
                    r.priority === "critical" || r.priority === "high"
                      ? "warn"
                      : "neutral"
                  }
                >
                  {r.priority}
                </Pill>
                <span className="text-forest-700">{r.title}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
