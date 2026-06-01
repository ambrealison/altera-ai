"use client";

/**
 * Phase Product-UX-B — Templates page (operator-friendly).
 *
 * Three beautiful cards (Protein Tracker / WWF / combined), each with a
 * short description, who-it's-for, a badge, and CSV + Excel download
 * buttons pointing at the static, import-verified assets under
 * ``/public/templates/``. No required/optional field chips, no
 * copy-headers — keep it simple for supermarket users.
 */

import { Card, Pill } from "@/components/ui";
import { useT } from "@/lib/i18n";

type Kind = "protein_tracker" | "wwf" | "combined";

const ASSET_BASE = "/templates";
const FILES: Record<Kind, { csv: string; xlsx: string }> = {
  protein_tracker: {
    csv: `${ASSET_BASE}/altera_template_protein_tracker.csv`,
    xlsx: `${ASSET_BASE}/altera_template_protein_tracker.xlsx`,
  },
  wwf: {
    csv: `${ASSET_BASE}/altera_template_wwf.csv`,
    xlsx: `${ASSET_BASE}/altera_template_wwf.xlsx`,
  },
  combined: {
    csv: `${ASSET_BASE}/altera_template_combined.csv`,
    xlsx: `${ASSET_BASE}/altera_template_combined.xlsx`,
  },
};

function TemplateCard({
  kind,
  emoji,
  titleKey,
  whoKey,
  enablesKey,
  badge,
  badgeTone,
}: {
  kind: Kind;
  emoji: string;
  titleKey: string;
  whoKey: string;
  enablesKey: string;
  badge: string;
  badgeTone: "brand" | "warn" | "ok";
}) {
  const t = useT();
  const files = FILES[kind];
  return (
    <Card className="flex flex-col">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-2xl" aria-hidden>
            {emoji}
          </span>
          <h3 className="text-base font-semibold text-forest-900">
            {t(titleKey)}
          </h3>
        </div>
        <Pill tone={badgeTone}>{badge}</Pill>
      </div>

      <p className="mt-3 text-sm text-ink-muted">{t(enablesKey)}</p>
      <p className="mt-2 text-xs text-ink-soft">
        <span className="font-medium text-forest-700">
          {t("templates.who")} :
        </span>{" "}
        {t(whoKey)}
      </p>

      <div className="mt-auto flex flex-wrap gap-2 pt-4">
        <a
          href={files.csv}
          download
          className="inline-flex items-center gap-1.5 rounded-xl bg-brand-600 px-3.5 py-1.5 text-sm font-medium text-white shadow-soft transition-all hover:bg-brand-700 active:scale-[0.98]"
        >
          ↓ {t("templates.downloadCsv")}
        </a>
        <a
          href={files.xlsx}
          download
          className="inline-flex items-center gap-1.5 rounded-xl border border-line bg-white px-3.5 py-1.5 text-sm font-medium text-forest-700 transition-colors hover:bg-mint-50"
        >
          ↓ {t("templates.downloadExcel")}
        </a>
      </div>
    </Card>
  );
}

export default function TemplatesPage() {
  const t = useT();
  return (
    <div className="mx-auto max-w-6xl">
      <div className="overflow-hidden rounded-3xl bg-forest-hero p-7 shadow-card">
        <span className="inline-flex items-center rounded-full bg-white/15 px-2.5 py-0.5 text-[11px] font-medium uppercase tracking-wider text-mint-100 ring-1 ring-white/20">
          {t("templates.eyebrow")}
        </span>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-white">
          {t("templates.title")}
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-mint-100/90">
          {t("templates.subtitle")}
        </p>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <TemplateCard
          kind="protein_tracker"
          emoji="🌱"
          titleKey="templates.pt.title"
          whoKey="templates.pt.who"
          enablesKey="templates.pt.enables"
          badge={t("templates.badge.ratio")}
          badgeTone="ok"
        />
        <TemplateCard
          kind="wwf"
          emoji="🥕"
          titleKey="templates.wwf.title"
          whoKey="templates.wwf.who"
          enablesKey="templates.wwf.enables"
          badge={t("templates.badge.groups")}
          badgeTone="brand"
        />
        <TemplateCard
          kind="combined"
          emoji="📊"
          titleKey="templates.combined.title"
          whoKey="templates.combined.who"
          enablesKey="templates.combined.enables"
          badge={t("templates.badge.complete")}
          badgeTone="warn"
        />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Card>
          <h3 className="text-sm font-semibold text-forest-900">
            🔒 {t("templates.privacy.title")}
          </h3>
          <p className="mt-1 text-sm text-ink-muted">
            {t("templates.privacy.body")}
          </p>
        </Card>
        <Card>
          <h3 className="text-sm font-semibold text-forest-900">
            ✅ {t("templates.tip.title")}
          </h3>
          <p className="mt-1 text-sm text-ink-muted">
            {t("templates.tip.body")}
          </p>
        </Card>
        <Card>
          <h3 className="text-sm font-semibold text-forest-900">
            🥕 {t("templates.wwfScope.title")}
          </h3>
          <p className="mt-1 text-sm text-ink-muted">
            {t("templates.wwfScope.body")}
          </p>
        </Card>
        <Card>
          <h3 className="text-sm font-semibold text-forest-900">
            🧪 {t("templates.nevoNote.title")}
          </h3>
          <p className="mt-1 text-sm text-ink-muted">
            {t("templates.nevoNote.body")}
          </p>
        </Card>
      </div>
    </div>
  );
}
