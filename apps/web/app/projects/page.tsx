"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Card, CardHeader, EmptyState, Pill, Skeleton } from "@/components/ui";
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
      <div className="overflow-hidden rounded-3xl bg-forest-hero p-7 shadow-card">
        <span className="inline-flex items-center rounded-full bg-white/15 px-2.5 py-0.5 text-[11px] font-medium uppercase tracking-wider text-mint-100 ring-1 ring-white/20">
          Projets
        </span>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-white">
          Vos projets
        </h1>
        <p className="mt-1 max-w-xl text-sm text-mint-100/90">
          Chaque projet fixe les méthodologies (Protein Tracker, WWF) et
          une période de reporting. Les imports et les calculs vivent
          dans un projet.
        </p>
      </div>

      {error && (
        <div className="mt-4 flex items-start justify-between gap-3 rounded-xl border border-warn-100 bg-warn-50 px-4 py-3 text-sm text-warn-700">
          <div>
            <div className="font-semibold">Chargement partiel</div>
            <div className="mt-0.5">{error}</div>
          </div>
          <button
            type="button"
            onClick={() => setBumper((n) => n + 1)}
            disabled={loading}
            className="shrink-0 rounded-lg border border-warn-100 bg-white px-3 py-1.5 text-sm font-medium text-warn-700 hover:bg-warn-50 disabled:opacity-50"
          >
            {loading ? "Chargement…" : "Réessayer"}
          </button>
        </div>
      )}

      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-[1fr_400px]">
        <Card>
          <CardHeader title="All projects" />
          {showInitialLoading ? (
            <div className="mt-4 space-y-2">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          ) : projects === null || projects.length === 0 ? (
            <div className="mt-4">
              <EmptyState
                title="Aucun projet pour l'instant"
                description={
                  error
                    ? "Les projets seront affichés une fois la connexion rétablie."
                    : "Créez votre premier projet à droite."
                }
              />
            </div>
          ) : (
            <ul className="mt-4 space-y-1.5">
              {projects.map((p) => (
                <li key={p.id}>
                  <Link
                    href={`/projects/${p.id}/workflow`}
                    className="group block rounded-xl border border-transparent p-3 transition-all hover:border-brand-100 hover:bg-mint-50/60 hover:shadow-soft"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-sm font-semibold text-forest-900">
                        {p.name}
                      </div>
                      <div className="flex items-center gap-1">
                        {p.methodologies_enabled.map((m) => (
                          <Pill key={m} tone="brand">{m}</Pill>
                        ))}
                      </div>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-ink-muted">
                      <span>{p.reporting_period_label}</span>
                      <span className="text-line">·</span>
                      <span>{p.upload_count} imports</span>
                      <span className="text-line">·</span>
                      <span>{p.review_queue_count} en revue</span>
                      <span className="text-line">·</span>
                      <span>{p.run_count} calculs</span>
                    </div>
                    <div className="mt-2 text-xs font-medium text-brand-700 underline-offset-2 group-hover:underline">
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
