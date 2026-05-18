"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Card, CardHeader, EmptyState, Pill } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { createApi, type Project } from "@/lib/api";
import { NewProjectForm } from "./NewProjectForm";

export default function ProjectsPage() {
  const { accessToken, loading: authLoading } = useAuth();
  const api = useMemo(() => createApi(accessToken), [accessToken]);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [bumper, setBumper] = useState(0);

  useEffect(() => {
    if (authLoading) return;
    let active = true;
    api
      .listProjects()
      .then((r) => {
        if (active) setProjects(r.items);
      })
      .catch((e: Error) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [api, authLoading, bumper]);

  return (
    <div className="mx-auto max-w-5xl">
      <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
      <p className="mt-1 text-sm text-gray-600">
        Each project pins methodologies (Protein Tracker, WWF) and a reporting
        period. Uploads and runs live inside one project.
      </p>

      {error && (
        <div className="mt-4 rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          {error}
        </div>
      )}

      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-[1fr_400px]">
        <Card>
          <CardHeader title="All projects" />
          {projects === null ? (
            <div className="mt-4 text-sm text-gray-500">Loading…</div>
          ) : projects.length === 0 ? (
            <div className="mt-4">
              <EmptyState
                title="No projects yet"
                description="Create your first project on the right."
              />
            </div>
          ) : (
            <ul className="mt-4 divide-y divide-gray-100">
              {projects.map((p) => (
                <li key={p.id} className="py-3">
                  <Link
                    href={`/projects/${p.id}`}
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
