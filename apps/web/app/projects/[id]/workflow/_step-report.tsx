"use client";

/**
 * Phase Product-UX-E — guided workflow Result step.
 *
 * Extracted from page.tsx so the report cache (useRunReport) and the
 * fully-translated report surface live in one place. The full report is
 * the only user-facing result; the cache makes it appear instantly when
 * the user navigates back to this step (no reload feeling). The
 * technical detail link is admin-only.
 */

import { useMemo, useState } from "react";

import { Button, Card } from "@/components/ui";
import { RunReport } from "@/components/RunReport";
import type { Run, WorkflowStep } from "@/lib/api";
import { createApi } from "@/lib/api";
import { useI18n, useT } from "@/lib/i18n";
import { useRunReport } from "@/lib/use-run-report";

export function StepReport({
  projectId,
  accessToken,
  step,
  latestRun,
}: {
  projectId: string;
  accessToken: string | null;
  step: WorkflowStep;
  latestRun: Run | null;
}) {
  const t = useT();
  const { lang } = useI18n();
  const api = useMemo(() => createApi(accessToken), [accessToken]);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const hasRun = step.status === "complete" && latestRun !== null;
  const runId = hasRun && latestRun ? latestRun.id : null;
  const { report, error, retry } = useRunReport(projectId, runId, accessToken);

  async function downloadExcel() {
    setDownloading(true);
    setDownloadError(null);
    try {
      await api.downloadCategorizedExport(projectId, lang);
    } catch (e) {
      setDownloadError(e instanceof Error ? e.message : t("report.export.error"));
    } finally {
      setDownloading(false);
    }
  }

  // No successful run yet — invite the user back to the calculation step.
  if (!hasRun) {
    return (
      <div className="space-y-5">
        <div>
          <h2 className="text-xl font-semibold">{t("report.step.title")}</h2>
          <p className="mt-1 text-sm text-ink-muted">
            {t("report.step.subtitleAfterRun")}
          </p>
        </div>
        <Card>
          <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-ink-muted">
            {t("report.step.noRun")}
          </div>
        </Card>
      </div>
    );
  }

  // Cached or freshly loaded report — the only user-facing result.
  // Phase Product-UX-F — no technical-detail link in the guided flow
  // (PT-only, WWF-only, or PT+WWF). The /runs/:id technical page remains
  // reachable directly for admins via the project page.
  if (report) {
    return (
      <div className="space-y-5">
        {/* Export CTA — download the categorised catalogue (.xlsx): all
            products + categories + one analysis sheet per methodology. */}
        <Card>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h3 className="text-base font-semibold text-forest-900">
                📊 {t("report.export.title")}
              </h3>
              <p className="mt-0.5 text-sm text-ink-muted">
                {t("report.export.body")}
              </p>
            </div>
            <Button
              variant="primary"
              onClick={() => void downloadExcel()}
              disabled={downloading}
            >
              {downloading
                ? t("report.export.downloading")
                : `⬇️ ${t("report.export.button")}`}
            </Button>
          </div>
          {downloadError && (
            <div className="mt-3 rounded-xl border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-700">
              {downloadError}
            </div>
          )}
        </Card>

        <RunReport doc={report} />
      </div>
    );
  }

  // Fatal error (no cached report to fall back on).
  if (error) {
    return (
      <div className="space-y-5">
        <div>
          <h2 className="text-xl font-semibold">{t("report.step.title")}</h2>
        </div>
        <Card>
          <div className="rounded-xl border border-danger-100 bg-danger-50 px-4 py-3 text-sm text-danger-700">
            <p className="font-medium">{t("report.step.loadErrorTitle")}</p>
            <p className="mt-1 text-xs">{t("report.step.loadErrorBody")}</p>
          </div>
          <div className="mt-4">
            <Button variant="secondary" onClick={retry}>
              {t("common.retry")}
            </Button>
          </div>
        </Card>
      </div>
    );
  }

  // Loading — beautiful skeleton while the report is fetched.
  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-semibold">{t("report.step.title")}</h2>
        <p className="mt-1 text-sm text-ink-muted">{t("report.step.preparing")}</p>
      </div>
      <div className="overflow-hidden rounded-3xl bg-forest-hero p-6 shadow-card">
        <div className="h-3 w-24 animate-pulse rounded-full bg-white/20" />
        <div className="mt-3 h-6 w-2/3 animate-pulse rounded-lg bg-white/25" />
        <div className="mt-2 h-3 w-1/2 animate-pulse rounded-full bg-white/15" />
      </div>
      <Card>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-16 animate-pulse rounded-2xl bg-line-soft/60 ring-1 ring-line"
            />
          ))}
        </div>
        <div className="mt-5 space-y-2">
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-3 w-full animate-pulse rounded-full bg-line-soft/60"
            />
          ))}
        </div>
      </Card>
    </div>
  );
}
