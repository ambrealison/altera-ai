"use client";

/**
 * Phase Product-UX-E / Result-Dual — guided workflow Result step.
 *
 * Runs are per-methodology, so the Result step fetches the LATEST Protein
 * Tracker report AND the LATEST WWF report (``GET /reports/latest``) and
 * shows BOTH: their sections are merged into one ReportDocument so
 * ``RunReport`` renders its PT/WWF toggle when both exist. PT and WWF data
 * stay separate (each section comes from its own run); only the section
 * fields are combined for display — never the metrics. If a methodology is
 * enabled but has no run yet, a clear missing-state notice is shown instead
 * of silently hiding it.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { Button, Card } from "@/components/ui";
import { RunReport } from "@/components/RunReport";
import type { LatestReports, ReportDocument, Run, WorkflowStep } from "@/lib/api";
import { createApi, EXPORT_NETWORK_ERROR } from "@/lib/api";
import { useI18n, useT } from "@/lib/i18n";

/** Merge the latest PT and WWF report docs into one document carrying both
 *  sections, so RunReport can toggle between them. Metrics are NOT combined:
 *  each section is taken verbatim from its own methodology's run. */
function mergeReports(
  pt: ReportDocument | null,
  wwf: ReportDocument | null,
): ReportDocument | null {
  const base = pt ?? wwf;
  if (!base) return null;
  return {
    ...base,
    pt_section: pt?.pt_section ?? null,
    wwf_section: wwf?.wwf_section ?? null,
    recommendations: [
      ...(pt?.recommendations ?? []),
      ...(wwf?.recommendations ?? []),
    ],
  };
}

export function StepReport({
  projectId,
  accessToken,
  step,
  latestRun,
  ptEnabled = true,
  wwfEnabled = false,
}: {
  projectId: string;
  accessToken: string | null;
  step: WorkflowStep;
  latestRun: Run | null;
  ptEnabled?: boolean;
  wwfEnabled?: boolean;
}) {
  const t = useT();
  const { lang } = useI18n();
  const api = useMemo(() => createApi(accessToken), [accessToken]);
  const hasRun = step.status === "complete" && latestRun !== null;

  const [data, setData] = useState<LatestReports | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!hasRun) return;
    setLoading(true);
    setError(false);
    try {
      const r = await api.getLatestReports(projectId);
      setData(r);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [api, projectId, hasRun]);

  useEffect(() => {
    void load();
  }, [load]);

  async function downloadExcel() {
    setDownloading(true);
    setDownloadError(null);
    try {
      await api.downloadCategorizedExport(projectId, lang);
    } catch (e) {
      // Network/CORS/timeout → localised message; a backend error carries a
      // useful detail string we surface verbatim; anything else → generic.
      const msg = e instanceof Error ? e.message : "";
      setDownloadError(
        msg === EXPORT_NETWORK_ERROR
          ? t("report.export.networkError")
          : msg || t("report.export.error"),
      );
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

  // First load — skeleton.
  if (loading && !data) {
    return (
      <div className="space-y-5">
        <div>
          <h2 className="text-xl font-semibold">{t("report.step.title")}</h2>
          <p className="mt-1 text-sm text-ink-muted">
            {t("report.step.preparing")}
          </p>
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
        </Card>
      </div>
    );
  }

  // Fatal error (fetch failed, nothing to show).
  if (error && !data) {
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
            <Button variant="secondary" onClick={() => void load()}>
              {t("common.retry")}
            </Button>
          </div>
        </Card>
      </div>
    );
  }

  const merged = mergeReports(
    data?.protein_tracker ?? null,
    data?.wwf ?? null,
  );
  const ptMissing = ptEnabled && !data?.protein_tracker;
  const wwfMissing = wwfEnabled && !data?.wwf;

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

      {/* Missing-methodology notice — never silently hide a methodology that
          is enabled but has no run yet. */}
      {(ptMissing || wwfMissing) && (
        <Card>
          <div className="rounded-xl border border-warn-100 bg-warn-50 px-4 py-3 text-sm text-warn-700">
            {ptMissing && <p>{t("report.missing.pt")}</p>}
            {wwfMissing && <p>{t("report.missing.wwf")}</p>}
          </div>
        </Card>
      )}

      {merged ? (
        <RunReport doc={merged} />
      ) : (
        <Card>
          <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-ink-muted">
            {t("report.step.noRun")}
          </div>
        </Card>
      )}
    </div>
  );
}
