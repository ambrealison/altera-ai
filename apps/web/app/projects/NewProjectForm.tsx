"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Field } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { createApi, type Methodology } from "@/lib/api";

export function NewProjectForm({ onCreated }: { onCreated?: () => void }) {
  const router = useRouter();
  const { accessToken } = useAuth();
  const [name, setName] = useState("");
  const [period, setPeriod] = useState("FY 2024");
  const [pt, setPt] = useState(true);
  const [wwf, setWwf] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const methodologies: Methodology[] = [];
    if (pt) methodologies.push("protein_tracker");
    if (wwf) methodologies.push("wwf");
    if (methodologies.length === 0) {
      setError("Pick at least one methodology.");
      return;
    }
    setBusy(true);
    try {
      const api = createApi(accessToken);
      const project = await api.createProject({
        name,
        methodologies_enabled: methodologies,
        reporting_period_label: period,
      });
      onCreated?.();
      router.push(`/projects/${project.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <Field label="Project name">
        <input
          type="text"
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="FY 2024 review"
          className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
        />
      </Field>
      <Field label="Reporting period label">
        <input
          type="text"
          required
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
          className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
        />
      </Field>
      <Field label="Methodologies enabled" hint="At least one is required.">
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={pt}
              onChange={(e) => setPt(e.target.checked)}
              className="rounded border-gray-300"
            />
            Protein Tracker (GPA &amp; ProVeg)
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={wwf}
              onChange={(e) => setWwf(e.target.checked)}
              className="rounded border-gray-300"
            />
            WWF Planet-Based Diets
          </label>
        </div>
      </Field>
      {error && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
          {error}
        </div>
      )}
      <Button type="submit" disabled={busy}>
        {busy ? "Creating…" : "Create project"}
      </Button>
    </form>
  );
}
