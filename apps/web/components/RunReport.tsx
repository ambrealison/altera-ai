"use client";

/**
 * Phase Product-UX-B — shared, demo-ready run report.
 *
 * Renders a ``ReportDocument`` (PT and/or WWF sections + executive
 * summary) as a beautiful in-workflow report so the operator never has
 * to leave the guided flow. Reused by the workflow result step and
 * (optionally) the technical run page. Pure presentation: no fetching,
 * no calculation — it formats existing backend numbers.
 *
 * Handles all three methodology variants gracefully: PT-only (pt_section
 * present, wwf null), WWF-only (wwf null), and PT+WWF (both).
 */

import { Card, Pill } from "@/components/ui";
import { formatKg, formatNumber, formatPct, formatRatio, formatGapPts } from "@/lib/format";
import type {
  PTReportSection,
  ReportDocument,
  WWFReportSection,
} from "@/lib/api";

const PT_GROUP_LABELS: Record<string, { label: string; emoji: string }> = {
  plant_based_core: { label: "Végétal — cœur", emoji: "🌱" },
  plant_based_non_core: { label: "Végétal — hors cœur", emoji: "🥗" },
  animal_core: { label: "Animal — cœur", emoji: "🐄" },
  composite_products: { label: "Composites", emoji: "🍽️" },
  out_of_scope: { label: "Hors périmètre", emoji: "▫️" },
  unknown: { label: "Inconnu", emoji: "❔" },
};

const WWF_FG_LABELS: Record<string, { label: string; emoji: string }> = {
  FG1: { label: "Protéines", emoji: "🍗" },
  FG2: { label: "Lait & alternatives", emoji: "🧀" },
  FG3: { label: "Matières grasses", emoji: "🫒" },
  FG4: { label: "Fruits & légumes", emoji: "🥕" },
  FG5: { label: "Céréales", emoji: "🌾" },
  FG6: { label: "Tubercules / féculents", emoji: "🥔" },
  FG7: { label: "Snacks (sucre/sel/gras)", emoji: "⚠️" },
  out_of_scope: { label: "Hors périmètre", emoji: "▫️" },
  unknown: { label: "Inconnu", emoji: "❔" },
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
// Protein Tracker section
// ---------------------------------------------------------------------------
function PtSection({ pt }: { pt: PTReportSection }) {
  const plantRatioPct = formatPct(pt.plant_share_pct);
  const totalProtein = Number(pt.total_in_scope_protein_kg) || 0;
  const sortedGroups = [...pt.groups].sort(
    (a, b) => (Number(b.protein_kg) || 0) - (Number(a.protein_kg) || 0),
  );
  const topPlant = sortedGroups.find((g) => g.pt_group === "plant_based_core");

  return (
    <Card>
      <h3 className="text-base font-semibold text-forest-900">
        🌱 Protein Tracker
      </h3>
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Kpi
          label="Protéines végétales"
          value={formatKg(pt.plant_protein_kg)}
          sub={formatPct(pt.plant_share_pct)}
          tone="brand"
        />
        <Kpi
          label="Protéines animales"
          value={formatKg(pt.animal_protein_kg)}
          sub={formatPct(pt.animal_share_pct)}
          tone="warn"
        />
        <Kpi
          label="Protéines totales"
          value={formatKg(pt.total_in_scope_protein_kg)}
        />
        <Kpi label="Ratio végétal" value={plantRatioPct} tone="brand" />
      </div>

      {/* Category analysis */}
      <div className="mt-5">
        <p className="text-xs font-semibold uppercase tracking-wide text-ink-soft">
          📊 Analyse par catégorie
        </p>
        <div className="mt-2 space-y-2">
          {sortedGroups.map((g) => {
            const meta = PT_GROUP_LABELS[g.pt_group] ?? {
              label: g.pt_group,
              emoji: "•",
            };
            const proteinKg = Number(g.protein_kg) || 0;
            const share = totalProtein > 0 ? (proteinKg / totalProtein) * 100 : 0;
            return (
              <div key={g.pt_group}>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-forest-700">
                    {meta.emoji} {meta.label}
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
          ✅ Les produits{" "}
          <span className="font-semibold">végétal — cœur</span> apportent{" "}
          {formatPct(((Number(topPlant.protein_kg) || 0) / totalProtein) * 100)}{" "}
          des protéines totales.
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// WWF section
// ---------------------------------------------------------------------------
function WwfSection({ wwf }: { wwf: WWFReportSection }) {
  const totalKg = Number(wwf.total_in_scope_weight_kg) || 0;
  const compositesTotal = Number(wwf.composites_total_weight_kg) || 0;
  const buckets = [
    { key: "meat", label: "À base de viande", emoji: "🥩", kg: wwf.composites_meat_based_kg },
    { key: "seafood", label: "À base de poisson", emoji: "🐟", kg: wwf.composites_seafood_based_kg },
    { key: "veg", label: "Végétarien", emoji: "🧀", kg: wwf.composites_vegetarian_kg },
    { key: "vegan", label: "Végane", emoji: "🌱", kg: wwf.composites_vegan_kg },
  ];

  return (
    <Card>
      <h3 className="text-base font-semibold text-forest-900">
        🥕 WWF Planet-Based Diets
      </h3>
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Kpi label="Volume in-scope" value={formatKg(wwf.total_in_scope_weight_kg)} tone="brand" />
        <Kpi label="Composites" value={formatKg(wwf.composites_total_weight_kg)} />
        <Kpi
          label="Composites végé/végane"
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
          📊 Groupes alimentaires vs cible PHD
        </p>
        <div className="mt-2 space-y-2">
          {wwf.per_food_group.map((fg) => {
            const meta = WWF_FG_LABELS[fg.food_group] ?? {
              label: fg.food_group,
              emoji: "•",
            };
            const share = Number(fg.share_pct) || 0;
            const target = fg.phd_reference_share_pct;
            const gap = formatGapPts(fg.share_pct, target);
            const below = target != null && share < (Number(target) || 0);
            return (
              <div key={fg.food_group}>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-forest-700">
                    {meta.emoji} {meta.label}
                  </span>
                  <span className="text-ink-muted">
                    {formatKg(fg.weight_kg)} · {formatPct(fg.share_pct)}
                    {target != null && (
                      <span className="ml-1 text-ink-soft">
                        (cible {formatPct(target)}
                        {gap ? `, ${gap}` : ""})
                      </span>
                    )}
                  </span>
                </div>
                <Bar
                  pct={share}
                  tone={fg.food_group === "FG7" ? "warn" : "brand"}
                />
                {fg.food_group === "FG4" && below && (
                  <p className="mt-0.5 text-[11px] text-warn-700">
                    🥕 Fruits & légumes sous la cible.
                  </p>
                )}
                {fg.food_group === "FG7" && !below && share > 0 && (
                  <p className="mt-0.5 text-[11px] text-warn-700">
                    ⚠️ Snacks à surveiller.
                  </p>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Composite buckets */}
      {compositesTotal > 0 && (
        <div className="mt-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink-soft">
            🍽️ Composites par bucket (Step 1)
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
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Top-level report
// ---------------------------------------------------------------------------
export function RunReport({ doc }: { doc: ReportDocument }) {
  const both = doc.pt_section && doc.wwf_section;
  return (
    <div className="space-y-4">
      {/* Hero / executive summary */}
      <div className="overflow-hidden rounded-3xl bg-forest-hero p-6 shadow-card">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center rounded-full bg-white/15 px-2.5 py-0.5 text-[11px] font-medium uppercase tracking-wider text-mint-100 ring-1 ring-white/20">
            🎯 Résultat
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
        {doc.executive_summary && (
          <p className="mt-1 max-w-3xl text-sm leading-relaxed text-mint-100/90">
            {doc.executive_summary}
          </p>
        )}
      </div>

      {both && (
        <div className="rounded-2xl border border-line bg-white/80 px-4 py-3">
          <p className="text-sm font-semibold text-forest-900">
            📋 Ce que l&apos;on apprend
          </p>
          <p className="mt-1 text-sm text-ink-muted">
            Ce projet combine Protein Tracker (ratio protéines) et WWF
            (groupes alimentaires). Les deux analyses ci-dessous se lisent
            ensemble : le ratio végétal complète la répartition par groupe.
          </p>
        </div>
      )}

      {doc.pt_section && <PtSection pt={doc.pt_section} />}
      {doc.wwf_section && <WwfSection wwf={doc.wwf_section} />}

      {doc.recommendations && doc.recommendations.length > 0 && (
        <Card>
          <h3 className="text-base font-semibold text-forest-900">
            🎯 Priorités d&apos;action
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
