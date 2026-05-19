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
      <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
      <p className="mt-1 text-sm text-gray-600">
        Welcome to Altera AI. Create a project, upload retailer data, classify,
        and run the methodology calculation.
      </p>

      {error && (
        <div className="mt-4 rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
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
            <div className="mt-4 text-sm text-gray-500">Loading…</div>
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
            <ul className="mt-4 divide-y divide-gray-100">
              {projects.slice(0, 5).map((p) => (
                <li key={p.id} className="flex items-center justify-between py-3">
                  <div>
                    <Link
                      href={`/projects/${p.id}`}
                      className="text-sm font-medium text-brand-700 hover:underline"
                    >
                      {p.name}
                    </Link>
                    <div className="mt-0.5 flex items-center gap-2">
                      {p.methodologies_enabled.map((m) => (
                        <Pill key={m} tone="brand">{m}</Pill>
                      ))}
                      <span className="text-xs text-gray-500">
                        {p.reporting_period_label}
                      </span>
                    </div>
                  </div>
                  <div className="text-xs text-gray-500">
                    {p.upload_count} uploads · {p.review_queue_count} in review · {p.run_count} runs
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
