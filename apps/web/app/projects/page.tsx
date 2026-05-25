"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Card, CardHeader, EmptyState, Pill } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { createApi, type Project } from "@/lib/api";
import { NewProjectForm } from "./NewProjectForm";

/**
 * Projects list page.
 *
 * Phase 34P: this page is the workspace landing — it must NEVER appear
 * empty after a transient backend failure (e.g. a previous classify
 * run timed out, the API restarted, the network blipped). To guarantee
 * that:
 *
 *  - We track three independent states: ``loading`` (first fetch in
 *    flight), ``error`` (last fetch failed), and ``projects`` (the most
 *    recent successful payload). The list is rendered from ``projects``
 *    even when ``error`` is set, so a refresh failure shows a banner
 *    + retry button on top of the previous data instead of wiping the
 *    workspace.
 *  - The retry button re-runs the fetch without reloading the page.
 *  - The error banner shows a short, plain-French message; no stack
 *    traces are surfaced to the user.
 */
export default function ProjectsPage() {
  const { accessToken, loading: authLoading } = useAuth();
  const api = useMemo(() => createApi(accessToken), [accessToken]);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [bumper, setBumper] = useState(0);

  const fetchProjects = useCallback(
    (signal?: AbortSignal) => {
      setLoading(true);
      return api
        .listProjects()
        .then((r) => {
          if (signal?.aborted) return;
          setProjects(r.items);
          setError(null);
        })
        .catch((e: Error) => {
          if (signal?.aborted) return;
          // Preserve the last-known projects so the workspace stays
          // visible. Surface a short user-facing message above the list.
          setError(
            e.message?.startsWith("TypeError")
              ? "Impossible de joindre le serveur. Réessayez."
              : e.message || "Le chargement des projets a échoué.",
          );
        })
        .finally(() => {
          if (!signal?.aborted) setLoading(false);
        });
    },
    [api],
  );

  useEffect(() => {
    if (authLoading) return;
    const ctrl = new AbortController();
    void fetchProjects(ctrl.signal);
    return () => ctrl.abort();
  }, [authLoading, fetchProjects, bumper]);

  const showInitialLoading = projects === null && loading && !error;

  return (
    <div className="mx-auto max-w-5xl">
      <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
      <p className="mt-1 text-sm text-gray-600">
        Each project pins methodologies (Protein Tracker, WWF) and a reporting
        period. Uploads and runs live inside one project.
      </p>

      {error && (
        <div className="mt-4 flex items-start justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <div>
            <div className="font-medium">Chargement partiel</div>
            <div className="mt-0.5">{error}</div>
          </div>
          <button
            type="button"
            onClick={() => setBumper((n) => n + 1)}
            disabled={loading}
            className="shrink-0 rounded-md border border-amber-300 bg-white px-3 py-1.5 text-sm font-medium text-amber-900 hover:bg-amber-100 disabled:opacity-50"
          >
            {loading ? "Chargement…" : "Réessayer"}
          </button>
        </div>
      )}

      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-[1fr_400px]">
        <Card>
          <CardHeader title="All projects" />
          {showInitialLoading ? (
            <div className="mt-4 text-sm text-gray-500">Loading…</div>
          ) : projects === null || projects.length === 0 ? (
            <div className="mt-4">
              <EmptyState
                title="No projects yet"
                description={
                  error
                    ? "Les projets seront affichés une fois la connexion rétablie."
                    : "Create your first project on the right."
                }
              />
            </div>
          ) : (
            <ul className="mt-4 divide-y divide-gray-100">
              {projects.map((p) => (
                <li key={p.id} className="py-3">
                  <Link
                    href={`/projects/${p.id}/workflow`}
                    className="block rounded-md p-2 -m-2 hover:bg-gray-50"
                  >
                    <div className="flex items-center justify-between">
                      <div className="text-sm font-medium text-brand-700">
                        {p.name}
                      </div>
                      <div className="flex items-center gap-1">
                        {p.methodologies_enabled.map((m) => (
                          <Pill key={m} tone="brand">{m}</Pill>
                        ))}
                      </div>
                    </div>
                    <div className="mt-1 flex items-center gap-3 text-xs text-gray-500">
                      <span>{p.reporting_period_label}</span>
                      <span>·</span>
                      <span>{p.upload_count} uploads</span>
                      <span>·</span>
                      <span>{p.review_queue_count} in review</span>
                      <span>·</span>
                      <span>{p.run_count} runs</span>
                    </div>
                    <div className="mt-2 text-xs text-brand-600 underline-offset-2 hover:underline">
                      Ouvrir le parcours guidé →
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader
            title="New project"
            subtitle="A project carries the enabled methodologies and reporting period."
          />
          <div className="mt-4">
            <NewProjectForm onCreated={() => setBumper((n) => n + 1)} />
          </div>
        </Card>
      </div>
    </div>
  );
}
