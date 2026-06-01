"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Button, Card, CardHeader, EmptyState, Pill, Stat } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { createApi, type Project } from "@/lib/api";

export default function DashboardPage() {
  const { accessToken, loading: authLoading } = useAuth();
  const api = useMemo(() => createApi(accessToken), [accessToken]);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (authLoading) return;
    let active = true;
    api
      .listProjects()
      .then((r) => {
        if (active) setProjects(r.items);
      })
      .catch((e: Error) => {
        if (active) {
          setError(e.message);
          setProjects([]);
        }
      });
    return () => {
      active = false;
    };
  }, [api, authLoading]);

  return (
    <div className="mx-auto max-w-5xl">
      <div className="overflow-hidden rounded-3xl bg-forest-hero p-7 shadow-card">
        <span className="inline-flex items-center rounded-full bg-white/15 px-2.5 py-0.5 text-[11px] font-medium uppercase tracking-wider text-mint-100 ring-1 ring-white/20">
          Tableau de bord
        </span>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-white">
          Bienvenue sur Altera AI
        </h1>
        <p className="mt-1 max-w-xl text-sm text-mint-100/90">
          Créez un projet, importez les données retailer, classez vos
          produits et calculez les ratios de transition alimentaire.
        </p>
      </div>

      {error && (
        <div className="mt-4 rounded-xl border border-danger-100 bg-danger-50 px-4 py-3 text-sm text-danger-700">
          {error}
        </div>
      )}

      <section className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card>
          <Stat
            label="Projects"
            value={projects?.length ?? "—"}
            hint="Active projects in your organisation."
          />
        </Card>
        <Card>
          <Stat
            label="Pending reviews"
            value={projects?.reduce((s, p) => s + p.review_queue_count, 0) ?? "—"}
            hint="Across all projects."
          />
        </Card>
        <Card>
          <Stat
            label="Completed runs"
            value={projects?.reduce((s, p) => s + p.run_count, 0) ?? "—"}
            hint="Calculation runs across all projects."
          />
        </Card>
      </section>

      <section className="mt-10">
        <Card>
          <CardHeader
            title="Projects"
            subtitle="A project pins methodologies and a reporting period."
            action={
              <Link href="/projects">
                <Button variant="primary">View all</Button>
              </Link>
            }
          />
          {projects === null ? (
            <div className="mt-4 text-sm text-ink-muted">Chargement…</div>
          ) : projects.length === 0 ? (
            <div className="mt-4">
              <EmptyState
                title="No projects yet"
                description="Create a project to start ingesting retailer data."
                action={
                  <Link href="/projects">
                    <Button variant="primary">+ New project</Button>
                  </Link>
                }
              />
            </div>
          ) : (
            <ul className="mt-4 divide-y divide-line-soft">
              {projects.slice(0, 5).map((p) => (
                <li
                  key={p.id}
                  className="-mx-2 flex items-center justify-between rounded-xl px-2 py-3 transition-colors hover:bg-mint-50/60"
                >
                  <div>
                    <Link
                      href={`/projects/${p.id}`}
                      className="text-sm font-semibold text-forest-900 hover:text-brand-700"
                    >
                      {p.name}
                    </Link>
                    <div className="mt-0.5 flex items-center gap-2">
                      {p.methodologies_enabled.map((m) => (
                        <Pill key={m} tone="brand">{m}</Pill>
                      ))}
                      <span className="text-xs text-ink-muted">
                        {p.reporting_period_label}
                      </span>
                    </div>
                  </div>
                  <div className="text-xs text-ink-muted">
                    {p.upload_count} imports · {p.review_queue_count} en revue · {p.run_count} calculs
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </section>
    </div>
  );
}
