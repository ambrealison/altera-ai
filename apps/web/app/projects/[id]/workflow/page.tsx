"use client";

/**
 * Phase 34A — guided retailer workflow.
 *
 * Single-source-of-truth view of where the project stands: which steps
 * are complete, which need an action, which are blocked. The page
 * binds to GET /api/v1/projects/{id}/workflow-status and surfaces one
 * primary CTA at a time (the backend's ``next_action`` field).
 *
 * The same workflow logic powers the run-preflight guard, so the CTA
 * on this page mirrors the gate that would block a calculation.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import { Button, Card, CardHeader, Pill } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import {
  ApiError,
  createApi,
  type WorkflowBlockingReason,
  type WorkflowStatus,
  type WorkflowStep,
  type WorkflowStepStatus,
} from "@/lib/api";

const STATUS_TONE: Record<WorkflowStepStatus, "neutral" | "brand" | "warn" | "ok" | "error"> = {
  complete: "ok",
  ready: "brand",
  needs_action: "warn",
  blocked: "error",
  available: "brand",
  locked: "neutral",
  not_needed: "neutral",
  disabled: "neutral",
};

const STATUS_FR: Record<WorkflowStepStatus, string> = {
  complete: "terminé",
  ready: "prêt",
  needs_action: "à faire",
  blocked: "bloqué",
  available: "disponible",
  locked: "verrouillé",
  not_needed: "non requis",
  disabled: "désactivé",
};

function relativeHref(projectId: string, href: string | null | undefined): string | null {
  if (!href) return null;
  if (href.startsWith("/")) return href;
  return `/projects/${projectId}/${href}`;
}

export default function WorkflowPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const projectId = params.id;
  const { accessToken } = useAuth();
  const api = useMemo(() => createApi(accessToken), [accessToken]);

  const [status, setStatus] = useState<WorkflowStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runBusy, setRunBusy] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.getWorkflowStatus(projectId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Échec du chargement");
    }
  }, [api, projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function onNextAction() {
    if (!status?.next_action) return;
    const action = status.next_action.action;
    const dest = relativeHref(projectId, status.next_action.href);

    if (action === "run_calculation") {
      // The runs page already handles the structured run_not_ready
      // error; here we attempt the run inline so the workflow CTA
      // is one click.
      setRunBusy(true);
      setRunError(null);
      try {
        const run = await api.createRun(projectId, "protein_tracker");
        router.push(`/projects/${projectId}/runs/${run.id}`);
      } catch (e) {
        if (e instanceof ApiError && typeof e.detail === "object" && e.detail !== null) {
          const d = e.detail as { message?: string };
          setRunError(d.message ?? "Le calcul n’est pas encore prêt.");
        } else {
          setRunError(e instanceof Error ? e.message : "Le calcul n’est pas encore prêt.");
        }
        // Re-read status so the new blockers (if any) appear immediately.
        void refresh();
      } finally {
        setRunBusy(false);
      }
      return;
    }

    if (action === "apply_nevo" || action === "apply_ciqual") {
      setRunBusy(true);
      try {
        await api.applyNutritionReferences(projectId);
        await refresh();
      } catch (e) {
        setRunError(e instanceof Error ? e.message : "Enrichissement impossible");
      } finally {
        setRunBusy(false);
      }
      return;
    }

    if (dest) {
      router.push(dest);
    }
  }

  if (error) {
    return (
      <div className="mx-auto max-w-3xl">
        <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          {error}
        </div>
        <Link
          href={`/projects/${projectId}`}
          className="mt-4 inline-block text-sm text-brand-700 hover:underline"
        >
          ← Retour au projet
        </Link>
      </div>
    );
  }
  if (!status) {
    return <div className="text-sm text-gray-500">Chargement…</div>;
  }

  const currentStepIndex = status.steps.findIndex((s) => s.key === status.current_step);
  const totalSteps = status.steps.length;

  return (
    <div className="mx-auto max-w-4xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Parcours guidé</h1>
          <p className="mt-1 text-sm text-gray-600">
            Étape {Math.max(1, currentStepIndex + 1)} sur {totalSteps} ·{" "}
            Progression : {status.overall_progress_pct} %
          </p>
        </div>
        <Link
          href={`/projects/${projectId}`}
          className="text-sm text-brand-700 hover:underline"
        >
          ← Retour au projet
        </Link>
      </div>

      {/* Progress bar */}
      <div className="mt-4 h-2 w-full overflow-hidden rounded-full bg-gray-100">
        <div
          className="h-full bg-brand-500 transition-all"
          style={{ width: `${Math.min(100, Math.max(0, status.overall_progress_pct))}%` }}
        />
      </div>

      {/* Primary CTA — single recommended action. */}
      {status.next_action && (
        <Card className="mt-6">
          <CardHeader
            title="Action recommandée"
            subtitle="Une seule étape à la fois — l’app vous guide."
          />
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <Button onClick={onNextAction} disabled={runBusy}>
              {runBusy ? "Traitement…" : status.next_action.label}
            </Button>
            <span className="text-xs text-gray-500">
              {status.current_step.replace(/_/g, " ")}
            </span>
          </div>
          {runError && (
            <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
              {runError}
            </div>
          )}
        </Card>
      )}

      {/* Stepper */}
      <ol className="mt-6 space-y-3">
        {status.steps.map((step, idx) => (
          <StepRow
            key={step.key}
            step={step}
            index={idx + 1}
            isCurrent={step.key === status.current_step}
          />
        ))}
      </ol>

      <p className="mt-6 text-xs text-gray-500">
        Note : l’IA peut aider à sélectionner certaines références, mais ne génère
        pas de valeurs nutritionnelles. Les protéines proviennent uniquement des
        données fournies par le retailer, de NEVO, de CIQUAL ou de la validation
        manuelle.
      </p>
    </div>
  );
}

function StepRow({
  step,
  index,
  isCurrent,
}: {
  step: WorkflowStep;
  index: number;
  isCurrent: boolean;
}) {
  return (
    <li>
      <div
        className={`rounded-md border px-4 py-3 ${
          isCurrent
            ? "border-brand-300 bg-brand-50/40"
            : "border-gray-200 bg-white"
        }`}
      >
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="text-xs font-semibold text-gray-500">
              {String(index).padStart(2, "0")}
            </span>
            <span className="text-sm font-medium text-gray-900">{step.label}</span>
            <Pill tone={STATUS_TONE[step.status]}>{STATUS_FR[step.status]}</Pill>
          </div>
          {step.progress_pct > 0 && step.progress_pct < 100 && (
            <span className="text-xs text-gray-500">{step.progress_pct} %</span>
          )}
        </div>
        {Object.keys(step.counts ?? {}).length > 0 && (
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-600">
            {Object.entries(step.counts).map(([key, value]) => (
              <span key={key}>
                <span className="text-gray-500">{prettyCountLabel(key)}:</span>{" "}
                <span className="font-medium text-gray-800">{value}</span>
              </span>
            ))}
          </div>
        )}
        {step.blocking_reasons.length > 0 && (
          <ul className="mt-2 space-y-1">
            {step.blocking_reasons.map((reason) => (
              <BlockingReasonRow key={reason.code} reason={reason} />
            ))}
          </ul>
        )}
      </div>
    </li>
  );
}

function BlockingReasonRow({ reason }: { reason: WorkflowBlockingReason }) {
  return (
    <li className="text-xs text-rose-700">
      ▸ {reason.label}
      {reason.count > 0 ? ` (${reason.count})` : ""}
    </li>
  );
}

const COUNT_LABELS_FR: Record<string, string> = {
  uploads: "Imports",
  products: "Produits",
  classified: "Classifiés",
  remaining: "Restants",
  in_review: "En revue",
  unknown: "Inconnus",
  pending: "En attente",
  matched: "Correspondances",
  with_split: "Avec split plant/animal",
  no_match: "Sans correspondance",
  matched_total_only: "Total uniquement",
  eligible_rows: "Lignes éligibles",
  runs: "Calculs",
};

function prettyCountLabel(key: string): string {
  return COUNT_LABELS_FR[key] ?? key.replace(/_/g, " ");
}
