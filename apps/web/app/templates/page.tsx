"use client";

/**
 * Phase Product-UX-A — Templates page (replaces the old "Data
 * Requirements"). Concise, retailer-facing: pick a methodology,
 * download a CSV whose headers match Altera's auto-mapping, and import
 * without renaming columns. Display-only — no API/mapping logic here.
 */

import { useState } from "react";
import { Card, Pill } from "@/components/ui";
import { useT } from "@/lib/i18n";
import {
  TEMPLATES,
  templateToCsv,
  type TemplateDef,
  type TemplateKind,
} from "@/lib/templates";

const FILENAMES: Record<TemplateKind, string> = {
  protein_tracker: "altera_template_protein_tracker.csv",
  wwf: "altera_template_wwf.csv",
  combined: "altera_template_protein_tracker_wwf.csv",
};

function download(def: TemplateDef) {
  const csv = templateToCsv(def);
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = FILENAMES[def.kind];
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function TemplateCard({
  def,
  titleKey,
  whenKey,
}: {
  def: TemplateDef;
  titleKey: string;
  whenKey: string;
}) {
  const t = useT();
  const [copied, setCopied] = useState(false);

  async function copyHeaders() {
    try {
      await navigator.clipboard.writeText(def.headers.join(","));
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard unavailable — no-op */
    }
  }

  return (
    <Card className="flex flex-col">
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-base font-semibold text-forest-900">
          {t(titleKey)}
        </h3>
        <Pill tone="brand">{def.requiredFields.length} requis</Pill>
      </div>
      <p className="mt-1 text-xs text-ink-muted">{t(whenKey)}</p>

      <div className="mt-3">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-soft">
          {t("templates.required")}
        </div>
        <div className="mt-1.5 flex flex-wrap gap-1">
          {def.requiredFields.map((f) => (
            <span
              key={f}
              className="rounded-md bg-mint-100 px-1.5 py-0.5 font-mono text-[11px] text-brand-700 ring-1 ring-brand-200"
            >
              {f}
            </span>
          ))}
        </div>
      </div>

      <div className="mt-3">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-soft">
          {t("templates.optional")}
        </div>
        <div className="mt-1.5 flex flex-wrap gap-1">
          {def.optionalFields.map((f) => (
            <span
              key={f}
              className="rounded-md bg-line-soft px-1.5 py-0.5 font-mono text-[11px] text-ink-muted"
            >
              {f}
            </span>
          ))}
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-2 pt-1">
        <button
          type="button"
          onClick={() => download(def)}
          className="inline-flex items-center gap-1.5 rounded-xl bg-brand-600 px-3.5 py-1.5 text-sm font-medium text-white shadow-soft transition-all hover:bg-brand-700 active:scale-[0.98]"
        >
          ↓ {t("templates.download")}
        </button>
        <button
          type="button"
          onClick={() => void copyHeaders()}
          className="inline-flex items-center rounded-xl border border-line bg-white px-3 py-1.5 text-sm font-medium text-forest-700 transition-colors hover:bg-mint-50"
        >
          {copied ? t("templates.copied") : t("templates.copyHeaders")}
        </button>
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
          def={TEMPLATES.protein_tracker}
          titleKey="templates.pt.title"
          whenKey="templates.pt.when"
        />
        <TemplateCard
          def={TEMPLATES.wwf}
          titleKey="templates.wwf.title"
          whenKey="templates.wwf.when"
        />
        <TemplateCard
          def={TEMPLATES.combined}
          titleKey="templates.combined.title"
          whenKey="templates.combined.when"
        />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Card>
          <h3 className="text-sm font-semibold text-forest-900">
            {t("templates.privacy.title")}
          </h3>
          <p className="mt-1 text-sm text-ink-muted">
            {t("templates.privacy.body")}
          </p>
        </Card>
        <Card>
          <h3 className="text-sm font-semibold text-forest-900">
            {t("templates.tip.title")}
          </h3>
          <p className="mt-1 text-sm text-ink-muted">
            {t("templates.tip.body")}
          </p>
        </Card>
      </div>
    </div>
  );
}
