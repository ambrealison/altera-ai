"use client";

/**
 * Phase Product-UX-E — report cache by run_id (PART F/G/H).
 *
 * The guided workflow re-mounts the Result step every time the user
 * navigates back to it, which previously refetched and re-rendered the
 * full report from scratch ("reload" feeling). The report for a given
 * run is immutable (a new calculation produces a NEW run_id), so we
 * cache the fetched ReportDocument in a module-level map keyed by
 * run_id. The map outlives component unmount/remount for the lifetime
 * of the tab session — no localStorage, no DB migration, no cross-run
 * leakage (run_id is globally unique), no cross-user persistence (a
 * full reload clears it).
 *
 * Behaviour:
 *   - cache hit  -> render instantly, NO refetch, NO loading screen.
 *   - cache miss -> loading skeleton, then fetch + cache, or a fatal
 *                   error (retryable) if the fetch fails with no cache.
 *   - run_id changes (new calculation) -> fetch + cache the new run.
 *   - language switch -> labels come from t(); data is cached and the
 *     effect does not depend on language, so no refetch occurs.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { createApi, type ReportDocument } from "@/lib/api";

// Module-level cache: survives Result-step unmount/remount within the
// tab session. Keyed by run_id (UUID), so entries never collide across
// runs and a new calculation simply misses and fetches fresh.
const _reportCache = new Map<string, ReportDocument>();

/** Test/escape hatch — not used in the UI. */
export function _clearRunReportCache(): void {
  _reportCache.clear();
}

export interface RunReportState {
  /** The report to render, or null while first-loading / on fatal error. */
  report: ReportDocument | null;
  /** First load only (no cached report yet). Never true on a cache hit. */
  loading: boolean;
  /** Fatal: the fetch failed AND there is no cached report to fall back on. */
  error: boolean;
  /** True when the shown report came straight from cache (instant). */
  fromCache: boolean;
  /** Re-attempt the fetch (used by the error state's Retry button). */
  retry: () => void;
}

export function useRunReport(
  projectId: string,
  runId: string | null,
  accessToken: string | null,
): RunReportState {
  const api = useMemo(() => createApi(accessToken), [accessToken]);

  const initial = runId ? _reportCache.get(runId) ?? null : null;
  const [report, setReport] = useState<ReportDocument | null>(initial);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [fromCache, setFromCache] = useState(initial !== null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!runId) {
      setReport(null);
      setLoading(false);
      setError(false);
      setFromCache(false);
      return;
    }

    // Cache hit — render instantly, do not refetch (the report for a
    // completed run is immutable; a new run gets a new run_id).
    const hit = _reportCache.get(runId);
    if (hit && reloadKey === 0) {
      setReport(hit);
      setLoading(false);
      setError(false);
      setFromCache(true);
      return;
    }

    let active = true;
    setLoading(true);
    setError(false);
    setFromCache(false);
    setReport(null);
    api
      .getReport(projectId, runId)
      .then((d) => {
        if (!active) return;
        _reportCache.set(runId, d);
        setReport(d);
        setLoading(false);
      })
      .catch((err) => {
        if (!active) return;
        // Surface, don't swallow — log the backend error and show a
        // retryable fatal error (there is no cached report here).
        console.error("Failed to load run report", err);
        setLoading(false);
        setError(true);
      });
    return () => {
      active = false;
    };
  }, [api, projectId, runId, reloadKey]);

  const retry = useCallback(() => setReloadKey((k) => k + 1), []);

  return { report, loading, error, fromCache, retry };
}
