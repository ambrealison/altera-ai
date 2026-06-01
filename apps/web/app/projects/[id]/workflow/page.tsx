"use client";

/**
 * Phase 34B — Guided retailer wizard (9-step, full-page content per step).
 *
 * Replaces the Phase 34A technical status overview with a true wizard:
 *   1. Import CSV
 *   2. Méthodologie
 *   3. Classification déterministe
 *   4. Classification IA
 *   5. Validation manuelle
 *   6. Enrichissement NEVO
 *   7. Fallback CIQUAL + IA
 *   8. Calcul
 *   9. Résultat / rapport
 *
 * Each step shows full-page content with one primary CTA. The horizontal
 * stepper at the top shows all 9 steps; completed steps are clickable.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";

import { Button, Card, Pill } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import {
  ApiError,
  createApi,
  type ApplyReferencesSummary,
  type CalculationPreflightResponse,
  type ClassificationJob,
  type ClassifySummary,
  CLASSIFICATION_JOB_TERMINAL_STATUSES,
  type MethodologyClassificationCounts,
  type Methodology,
  type ReportDocument,
  type Run,
  type UploadResult,
  type WorkflowStatus,
  type WorkflowStep,
} from "@/lib/api";

// Phase Product-UX-B — shared in-workflow result report.
import { RunReport } from "@/components/RunReport";
// Phase 34E — fully inline upload + manual review inside the wizard.
import { InlineUpload } from "./_inline-upload";
// Phase 34F — inline category validation table.
import { ValidationTable } from "./_validation-table";
// Phase 34L — inline nutrition validation table.
import { NutritionTable } from "./_nutrition-table";

// ---------------------------------------------------------------------------
// Wizard step definitions — 9 visible steps mapped to backend step keys
// ---------------------------------------------------------------------------

// Phase 34L — CIQUAL removed from the normal user path (it gives total
// protein only, not the plant/animal split Protein Tracker needs).
// A new "Validation nutritionnelle" step takes its place so the user
// can inspect and complete protein data before calculation. CIQUAL
// code remains in the backend for admin/debug.
//
// Phase WWF-G — wizard is now methodology-aware. NEVO + Nutrition
// Validation are *PT-only* steps and are filtered out for WWF-only
// projects so a WWF user never sees protein-flavoured copy or
// blockers. Labels on the classification / validation / calculation /
// report steps also shift to the WWF flavour for WWF-only projects.

type WizardStepDef = {
  id: string;
  label: string;
  backendKey: string;
  /** Step is rendered when ANY of these methodologies are enabled.
   *  ``"any"`` is shorthand for "always render". */
  methodologyGate: "any" | "protein_tracker" | "wwf";
};

const ALL_WIZARD_STEPS: readonly WizardStepDef[] = [
  { id: "import",        label: "Import",                       backendKey: "upload",                        methodologyGate: "any" },
  { id: "methodology",   label: "Méthodologie",                 backendKey: "methodology",                   methodologyGate: "any" },
  { id: "ai_class",      label: "Classification IA",            backendKey: "ai_classification",             methodologyGate: "any" },
  { id: "validation",    label: "Validation",                   backendKey: "manual_classification_review",  methodologyGate: "any" },
  { id: "nevo",          label: "NEVO",                         backendKey: "nutrition_enrichment_nevo",     methodologyGate: "protein_tracker" },
  { id: "nutrition_val", label: "Validation nutritionnelle",    backendKey: "nutrition_validation",          methodologyGate: "protein_tracker" },
  { id: "calculation",   label: "Calcul",                       backendKey: "calculation",                   methodologyGate: "any" },
  { id: "report",        label: "Résultat",                     backendKey: "report",                        methodologyGate: "any" },
] as const;

/** Phase WWF-G — wwf-flavoured labels override the PT defaults when
 *  the project is WWF-only. Keys match ``WizardStepDef.id``. */
const WWF_ONLY_STEP_LABELS: Record<string, string> = {
  ai_class: "Catégorisation WWF",
  validation: "Validation WWF",
  calculation: "Calcul WWF",
  report: "Rapport WWF",
};

/** Build the visible wizard step list for a given set of enabled
 *  methodologies. Returns numbered (re-indexed) step entries. */
function buildWizardSteps(
  methodologies: readonly string[],
): readonly (WizardStepDef & { idx: number })[] {
  const ptOn = methodologies.includes("protein_tracker");
  const wwfOn = methodologies.includes("wwf");
  const wwfOnly = wwfOn && !ptOn;
  return ALL_WIZARD_STEPS.filter((s) => {
    if (s.methodologyGate === "any") return true;
    if (s.methodologyGate === "protein_tracker") return ptOn;
    if (s.methodologyGate === "wwf") return wwfOn;
    return true;
  }).map((s, idx) => ({
    ...s,
    label: wwfOnly && WWF_ONLY_STEP_LABELS[s.id]
      ? WWF_ONLY_STEP_LABELS[s.id]
      : s.label,
    idx,
  }));
}

// Type alias kept for the existing component contracts; the actual
// index range is now dynamic (4-step minimum, 8-step maximum).
type WizardStepIdx = number;

function backendKeyToWizardIdx(
  visibleSteps: readonly (WizardStepDef & { idx: number })[],
  key: string,
): WizardStepIdx {
  const found = visibleSteps.find((s) => s.backendKey === key);
  return found?.idx ?? 0;
}

function backendStep(status: WorkflowStatus, key: string): WorkflowStep | undefined {
  return status.steps.find((s) => s.key === key);
}

// ---------------------------------------------------------------------------
// Stepper chip — one per visible wizard step
// ---------------------------------------------------------------------------

function StepChip({
  wizardStep,
  currentIdx,
  accessible,
  status,
  summary,
  onClick,
}: {
  wizardStep: WizardStepDef & { idx: number };
  currentIdx: WizardStepIdx;
  accessible: boolean;
  status: string;
  summary: string | null;
  onClick: () => void;
}) {
  const isActive = wizardStep.idx === currentIdx;
  const isComplete = status === "complete" || status === "not_needed";
  const isBlocked = status === "blocked";

  let circleClass = "flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold transition-all duration-150 ";
  if (isActive) {
    circleClass += "bg-brand-600 text-white shadow-soft ring-4 ring-brand-100";
  } else if (isComplete) {
    circleClass += "bg-brand-500 text-white";
  } else if (isBlocked) {
    circleClass += "bg-danger-50 text-danger-700 ring-1 ring-danger-100";
  } else if (accessible) {
    circleClass += "bg-white text-ink-muted ring-1 ring-line";
  } else {
    circleClass += "bg-line-soft text-ink-soft";
  }

  const inner = isComplete && !isActive ? "✓" : String(wizardStep.idx + 1);

  const labelClass = `mt-1.5 text-center text-[11px] leading-tight ${
    isActive ? "font-semibold text-brand-700" : accessible ? "text-forest-700" : "text-ink-soft"
  }`;

  const container = (
    <div className="flex flex-col items-center gap-0.5 min-w-[52px]">
      <div className={circleClass}>{inner}</div>
      <span className={labelClass}>{wizardStep.label}</span>
      {summary && isComplete && !isActive && (
        <span className="text-[10px] text-ink-soft text-center leading-tight max-w-[60px] truncate">
          {summary}
        </span>
      )}
    </div>
  );

  if (!accessible) {
    return <div className="opacity-50 cursor-not-allowed">{container}</div>;
  }

  return (
    <button
      type="button"
      onClick={onClick}
      className="focus:outline-none"
      title={accessible ? wizardStep.label : "Étape verrouillée"}
    >
      {container}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Blocking reason list
// ---------------------------------------------------------------------------

function BlockerList({ step }: { step: WorkflowStep }) {
  if (!step.blocking_reasons.length) return null;
  return (
    <ul className="mt-3 space-y-1.5">
      {step.blocking_reasons.map((r) => (
        <li
          key={r.code}
          className="flex items-start gap-2 rounded-lg bg-danger-50 px-3 py-1.5 text-sm text-danger-700 ring-1 ring-danger-100"
        >
          <span className="mt-0.5 shrink-0">▸</span>
          <span>
            {r.label}
            {r.count > 0 ? ` (${r.count})` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Count badge row
// ---------------------------------------------------------------------------

const COUNT_LABELS: Record<string, string> = {
  uploads: "Imports",
  products: "Produits",
  classified: "Classifiés",
  remaining: "Restants",
  in_review: "En revue",
  unknown: "Inconnus",
  pending: "En attente",
  matched: "Correspondances NEVO",
  with_split: "Avec split plant/animal",
  no_match: "Sans correspondance",
  matched_total_only: "Correspondances CIQUAL",
  eligible_rows: "Lignes éligibles",
  runs: "Calculs",
};

function CountRow({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).filter(([, v]) => v > 0);
  if (!entries.length) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {entries.map(([k, v]) => (
        <div
          key={k}
          className="rounded-xl border border-line bg-mint-50/60 px-3 py-2"
        >
          <span className="block text-[11px] font-medium uppercase tracking-wide text-ink-soft">
            {COUNT_LABELS[k] ?? k.replace(/_/g, " ")}
          </span>
          <span className="text-lg font-semibold text-forest-900">{v}</span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step content panels — one per wizard step
// ---------------------------------------------------------------------------

function StepImport({
  projectId,
  accessToken,
  step,
  latestUpload,
  methodologies,
  onUploaded,
  onNext,
}: {
  projectId: string;
  accessToken: string | null;
  step: WorkflowStep;
  latestUpload: UploadResult | null;
  methodologies: string[];
  onUploaded: () => void | Promise<void>;
  onNext: () => void;
}) {
  const isComplete = step.status === "complete";
  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-semibold">Importer le fichier CSV</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Chargez le fichier produits du retailer. Altera vérifiera le mapping des colonnes
          automatiquement et génèrera les identifiants manquants.
        </p>
      </div>

      <InlineUpload
        projectId={projectId}
        accessToken={accessToken}
        methodologies={methodologies}
        latestUpload={latestUpload}
        onUploaded={onUploaded}
      />

      {isComplete && latestUpload && (
        <Card>
          <CountRow counts={step.counts} />
          {latestUpload.warnings.length > 0 && (
            <div className="mt-2 rounded-xl border border-warn-100 bg-warn-50 px-3 py-2 text-xs text-warn-700">
              {latestUpload.warnings.length} avertissement(s) à l’import.
            </div>
          )}
          <div className="mt-3 flex flex-wrap gap-3">
            <Button onClick={onNext}>Continuer vers Méthodologie</Button>
          </div>
        </Card>
      )}
    </div>
  );
}

function StepMethodology({
  step,
  methodologies,
  onNext,
}: {
  step: WorkflowStep;
  methodologies: string[];
  onNext: () => void;
}) {
  const isComplete = step.status === "complete";

  const METHOD_LABELS: Record<string, string> = {
    protein_tracker: "Protein Tracker",
    wwf: "WWF Planet-Based Diets",
  };

  const METHOD_DESC: Record<string, string> = {
    protein_tracker:
      "Calcule le ratio protéines végétales / protéines totales à partir des données d'achat et de nutrition.",
    wwf: "Step 1 (niveau produit) : classe les achats alimentaires selon les groupes PHD du WWF (FG1–FG7) et affecte les produits composés à leur poids total dans les buckets meat-based, seafood-based, vegetarian ou vegan. Requiert le poids unitaire et le volume des ventes. Le Step 2 ingrédient-level (recettes marque propre) n'est pas encore activé.",
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-semibold">Méthodologie</h2>
        <p className="mt-1 text-sm text-ink-muted">
          La méthodologie détermine le type de calcul effectué sur les produits importés.
        </p>
      </div>

      {isComplete ? (
        <div className="space-y-3">
          {methodologies.map((m) => (
            <Card key={m}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="font-medium text-gray-900">{METHOD_LABELS[m] ?? m}</p>
                  <p className="mt-0.5 text-sm text-ink-soft">{METHOD_DESC[m] ?? ""}</p>
                </div>
                <Pill tone="ok">Activée</Pill>
              </div>
            </Card>
          ))}
          <div className="flex flex-wrap gap-3 pt-2">
            <Button onClick={onNext}>Continuer vers la classification</Button>
          </div>
        </div>
      ) : (
        <Card>
          <p className="text-sm text-ink-muted">
            La méthodologie est définie à la création du projet. Retournez aux paramètres du projet
            pour la modifier.
          </p>
          <div className="mt-4">
            <Button variant="secondary" disabled>
              Aucune méthodologie sélectionnée
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}

// Phase 34I — StepDeterministic was removed from the user-facing
// wizard. AI is the primary classifier now (Step 2 in the new
// numbering). The deterministic rule engine remains in the codebase
// for tests and admin/debug, reachable only by passing
// deterministic_only=true to /uploads/{uid}/classify.

// Phase 34R — chunked-job progress UI. Renders a progress bar, the
// running counts, and a French-language status badge. The component
// is presentational: data comes entirely from the `job` prop, which
// the wizard refreshes by polling ``POST /advance``.
function ClassificationJobProgress({ job }: { job: ClassificationJob }) {
  const pct = Math.max(0, Math.min(100, Math.round(job.progress_pct)));
  const tone =
    job.status === "completed"
      ? "border-brand-200 bg-mint-100 text-brand-700"
      : job.status === "completed_with_errors"
      ? "border-warn-100 bg-warn-50 text-warn-700"
      : job.status === "failed"
      ? "border-danger-100 bg-danger-50 text-danger-700"
      : job.status === "cancelled"
      ? "border-gray-200 bg-gray-50 text-forest-700"
      : "border-brand-200 bg-brand-50 text-brand-900";
  const badge =
    job.status === "queued"
      ? "En file d'attente"
      : job.status === "running"
      ? "En cours…"
      : job.status === "completed"
      ? "Terminé"
      : job.status === "completed_with_errors"
      ? "Terminé avec erreurs"
      : job.status === "failed"
      ? "Échec"
      : "Annulé";
  return (
    <div className={`rounded-md border px-3 py-2 text-sm ${tone}`}>
      <div className="flex items-center justify-between">
        <div className="font-medium">{badge}</div>
        <div className="text-xs opacity-70">
          {job.processed_products}/{job.total_products} ·{" "}
          {pct.toFixed(0)}%
        </div>
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-white/60">
        <div
          className="h-full rounded-full bg-current opacity-60 transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="mt-2 text-xs font-medium">
        {job.categorized_total} catégorisé(s) · {job.accepted_total} accepté(s)
        · {job.review_required_total} à vérifier · {job.failed_total} échec
      </div>
      {(job.retry_batches > 0 ||
        job.out_of_scope_total > 0 ||
        job.unknown_total > 0) && (
        <div className="mt-1 text-xs opacity-80">
          {job.retry_batches > 0 && (
            <>
              {job.retry_batches} retry
              {job.recovered_rows > 0 && (
                <> ({job.recovered_rows} récupéré(s))</>
              )}
            </>
          )}
          {job.out_of_scope_total > 0 && (
            <> · {job.out_of_scope_total} hors périmètre</>
          )}
          {job.unknown_total > 0 && (
            <> · {job.unknown_total} inconnu(s)</>
          )}
        </div>
      )}
      {(job.status === "running" || job.status === "queued") && (
        <div className="mt-2 text-xs opacity-80">
          {"Vous pouvez laisser cette page ouverte — la progression est sauvegardée."}
        </div>
      )}
      {job.error_message && (
        <div className="mt-2 text-xs">
          <strong>{job.error_code ?? "Erreur"} :</strong> {job.error_message}
        </div>
      )}
    </div>
  );
}

function StepAIClassification({
  step,
  latestUpload,
  methodologies,
  primaryMethodology,
  lastClassifyResult,
  currentJob,
  jobError,
  busy,
  error,
  onRun,
  onResume,
  onRetryFailed,
  onNext,
}: {
  step: WorkflowStep;
  latestUpload: UploadResult | null;
  methodologies: string[];
  /** Phase WWF-G — methodology the AI classifier will run against
   *  (wwf for WWF-only, protein_tracker otherwise). */
  primaryMethodology: Methodology;
  lastClassifyResult: ClassifySummary | null;
  // Phase 34R — async classification job state. When non-null, the
  // step renders a progress bar and disables duplicate-click.
  currentJob: ClassificationJob | null;
  jobError: string | null;
  busy: boolean;
  error: string | null;
  onRun: () => void;
  // Phase 35A — resume an existing non-terminal job (used by both
  // the auto-resume on mount and the "Reprendre" button after a
  // 5-failures dead-end).
  onResume: (jobId: string) => void;
  onRetryFailed: () => void;
  onNext: () => void;
}) {
  const isComplete = step.status === "complete";
  const isNotNeeded = step.status === "not_needed";
  const ptEnabled = methodologies.includes("protein_tracker");
  const wwfEnabled = methodologies.includes("wwf");
  const wwfOnly = wwfEnabled && !ptEnabled;
  // Phase WWF-G — the classify button is gated on the *primary*
  // methodology being enabled (always true for PT or WWF projects,
  // but defence-in-depth: a project with neither enabled would
  // disable the CTA).
  const classifyEnabled = primaryMethodology === "wwf" ? wwfEnabled : ptEnabled;

  // Phase 34D — map machine-readable ai_disabled_reason to a French message
  // that explicitly names the env vars an admin must check. The banner is
  // shown both before any run (so the user knows what to expect) and
  // after a run that returned ai_enabled=false.
  const aiReason = lastClassifyResult?.ai_disabled_reason ?? null;
  const aiWasOff =
    lastClassifyResult !== null && !lastClassifyResult.ai_enabled;
  const aiBanner: string | null = aiWasOff
    ? aiReason === "deterministic_only"
      ? "Classification IA volontairement désactivée pour cette exécution (mode déterministe seul)."
      : aiReason === "classifier_disabled"
      ? "Classification IA indisponible : ALTERA_AI_CLASSIFIER_ENABLED n’est pas activé sur ce serveur."
      : aiReason === "provider_disabled"
      ? "Classification IA indisponible : ALTERA_AI_PROVIDER vaut 'disabled'."
      : aiReason === "provider_misconfigured"
      ? "Classification IA indisponible : OPENAI_API_KEY est manquant (provider OpenAI sélectionné)."
      : "Classification IA indisponible — vérifier ALTERA_AI_CLASSIFIER_ENABLED, ALTERA_AI_PROVIDER, et OPENAI_API_KEY sur le serveur. Les produits non reconnus partent en validation manuelle."
    : null;

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-semibold">
          {wwfOnly ? "Catégorisation WWF" : "Classification IA"}
        </h2>
        <p className="mt-1 text-sm text-ink-muted">
          {wwfOnly
            ? "Cette étape classe les produits en groupes alimentaires WWF (FG1–FG7) et identifie les produits composites."
            : "L'IA aide à catégoriser les produits restants à partir de champs non commerciaux."}
        </p>
        <p className="mt-1 text-xs text-ink-soft">
          {"Les champs commerciaux comme volumes, ventes, prix et marges ne sont pas envoyés à l'IA."}
        </p>
      </div>

      <Card>
        {aiBanner && (
          <div className="mb-3 rounded-xl border border-warn-100 bg-warn-50 px-3 py-2 text-sm text-warn-700">
            {aiBanner}
          </div>
        )}
        {/* Phase 34D — surface AI run counts so the step is never silent. */}
        {lastClassifyResult && lastClassifyResult.ai_enabled && (
          <div className="mb-3 space-y-2">
            <div
              className={
                "rounded-md border px-3 py-2 text-sm " +
                (lastClassifyResult.ai_accepted > 0
                  ? "border-brand-200 bg-mint-100 text-brand-700"
                  : "border-danger-100 bg-danger-50 text-danger-700")
              }
            >
              {/* Phase 34Q — coverage-oriented copy. A product with
                  a proposed category that needs review is still
                  *categorized*; the wizard must never imply otherwise. */}
              <div className="font-medium">
                {lastClassifyResult.categorized_total} catégorisé(s) ·{" "}
                {lastClassifyResult.accepted_total} accepté(s) ·{" "}
                {lastClassifyResult.review_required_total} à vérifier ·{" "}
                {lastClassifyResult.ai_failed} échec.
              </div>
              <div className="mt-1 text-xs opacity-80">
                IA exécutée sur {lastClassifyResult.ai_attempted} produit(s) en{" "}
                {lastClassifyResult.ai_batch_count} batch(s)
                {lastClassifyResult.ai_retry_batches > 0 && (
                  <>
                    {" "}+ {lastClassifyResult.ai_retry_batches} retry
                    {lastClassifyResult.ai_recovered_rows > 0 && (
                      <> ({lastClassifyResult.ai_recovered_rows} récupéré(s))</>
                    )}
                  </>
                )}
                {lastClassifyResult.out_of_scope_total > 0 && (
                  <>
                    {" "}· {lastClassifyResult.out_of_scope_total} hors périmètre
                  </>
                )}
                {lastClassifyResult.unknown_total > 0 && (
                  <> · {lastClassifyResult.unknown_total} inconnu(s)</>
                )}
              </div>
            </div>
            {/* Phase 34F — finer breakdown when something failed. */}
            {(lastClassifyResult.ai_parse_failures > 0 ||
              lastClassifyResult.ai_unsupported_category_failures > 0 ||
              lastClassifyResult.ai_provider_errors > 0) && (
              <div className="rounded-xl border border-warn-100 bg-warn-50 px-3 py-2 text-xs text-warn-700">
                <p className="font-medium">Diagnostics IA :</p>
                <ul className="mt-1 list-disc pl-4">
                  {lastClassifyResult.ai_parse_failures > 0 && (
                    <li>
                      {lastClassifyResult.ai_parse_failures} réponse(s) IA non
                      analysables (JSON invalide / id manquant)
                    </li>
                  )}
                  {lastClassifyResult.ai_unsupported_category_failures > 0 && (
                    <li>
                      {
                        lastClassifyResult.ai_unsupported_category_failures
                      }{" "}
                      catégorie(s) inconnue(s) renvoyée(s) par le modèle
                    </li>
                  )}
                  {lastClassifyResult.ai_provider_errors > 0 && (
                    <li>
                      {lastClassifyResult.ai_provider_errors} erreur(s)
                      fournisseur (réseau / 5xx / clé invalide)
                    </li>
                  )}
                </ul>
                {lastClassifyResult.ai_sample_errors.length > 0 && (
                  <details className="mt-1">
                    <summary className="cursor-pointer text-warn-700 hover:underline">
                      Voir un échantillon des erreurs
                    </summary>
                    <ul className="mt-1 list-disc pl-4 text-warn-700">
                      {lastClassifyResult.ai_sample_errors
                        .slice(0, 5)
                        .map((m, i) => (
                          <li key={i} className="break-all">
                            {m}
                          </li>
                        ))}
                    </ul>
                  </details>
                )}
              </div>
            )}
          </div>
        )}
        {isNotNeeded ? (
          <div className="rounded-xl border border-brand-200 bg-mint-100 px-3 py-2 text-sm text-brand-700">
            Aucune classification IA nécessaire — tous les produits ont été classifiés
            déterministement.
          </div>
        ) : (
          <>
            <CountRow counts={step.counts} />
            <BlockerList step={step} />
          </>
        )}
        {/* Phase 34R — async job progress UI. Renders only while a
            classification job is active OR has just finished. */}
        {currentJob && (
          <div className="mb-3 space-y-2">
            <ClassificationJobProgress job={currentJob} />
            {jobError && (
              <div className="rounded-xl border border-warn-100 bg-warn-50 px-3 py-2 text-xs text-warn-700">
                {jobError}
              </div>
            )}
          </div>
        )}
        {error && (
          <div className="mt-3 rounded-xl border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-700">
            {error}
          </div>
        )}
        <div className="mt-4 flex flex-wrap gap-3">
          {isComplete || isNotNeeded ? (
            <Button onClick={onNext}>Continuer vers Validation</Button>
          ) : currentJob &&
            (currentJob.status === "queued" || currentJob.status === "running") &&
            busy ? (
            // Loop is actively polling — disable to prevent duplicates.
            <Button disabled>
              {`Classification en cours… (${currentJob.processed_products}/${currentJob.total_products})`}
            </Button>
          ) : currentJob &&
            (currentJob.status === "queued" || currentJob.status === "running") &&
            !busy ? (
            // Phase 35A — non-terminal job exists but the poll loop
            // is NOT running (e.g. 5 consecutive network failures
            // stopped it, or the user just navigated back to the
            // step). Offer Reprendre instead of leaving them stuck.
            <Button onClick={() => onResume(currentJob.job_id)}>
              {`Reprendre la classification (${currentJob.processed_products}/${currentJob.total_products})`}
            </Button>
          ) : currentJob && currentJob.status === "completed_with_errors" ? (
            <>
              <Button onClick={onRetryFailed} disabled={busy}>
                {`Réessayer ${currentJob.failed_product_count} échec(s)`}
              </Button>
              <Button onClick={onNext} variant="secondary">
                Continuer vers Validation
              </Button>
            </>
          ) : currentJob && currentJob.status === "completed" ? (
            // Phase Product-UX-B — the job finished cleanly. For
            // WWF-only projects the backend ``ai_classification`` step
            // is PT-based (pt_total == 0 → locked), so ``isComplete``
            // never trips and the old code fell through to "Lancer la
            // catégorisation WWF". Drive the user FORWARD here, with
            // reclassify as a secondary action only.
            <>
              <Button onClick={onNext}>Continuer vers Validation</Button>
              <Button onClick={onRun} variant="secondary" disabled={busy}>
                {wwfOnly
                  ? "Reclassifier WWF"
                  : "Reclassifier"}
              </Button>
            </>
          ) : currentJob && currentJob.status === "failed" ? (
            <Button
              onClick={onRun}
              disabled={busy || !latestUpload || !classifyEnabled}
            >
              {wwfOnly
                ? "Réessayer la catégorisation WWF"
                : "Réessayer la classification IA"}
            </Button>
          ) : (
            <Button
              onClick={onRun}
              disabled={busy || !latestUpload || !classifyEnabled}
            >
              {busy
                ? wwfOnly
                  ? "Catégorisation WWF en cours…"
                  : "Classification IA en cours…"
                : wwfOnly
                ? "Lancer la catégorisation WWF"
                : "Lancer la classification IA"}
            </Button>
          )}
        </div>
        {!latestUpload && (
          <p className="mt-2 text-xs text-ink-soft">
            {"Importez d'abord un fichier à l'étape 1."}
          </p>
        )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Phase WWF-H — dual classification card panel for PT+WWF projects.
// Renders one card per methodology so the user can run PT and WWF
// classification independently, with separate Run/Resume CTAs and
// per-methodology progress derived from
// ``WorkflowStatus.classification_by_methodology``.
// ---------------------------------------------------------------------------

function MethodologyClassificationCard({
  methodology,
  counts,
  currentJob,
  busy,
  error,
  latestUpload,
  onRun,
  onResume,
  onRetryFailed,
  onOpenValidation,
}: {
  methodology: Methodology;
  counts: MethodologyClassificationCounts | undefined;
  currentJob: ClassificationJob | null;
  busy: boolean;
  error: string | null;
  latestUpload: UploadResult | null;
  onRun: () => void;
  onResume: (jobId: string) => void;
  onRetryFailed: () => void;
  onOpenValidation: () => void;
}) {
  const isWwf = methodology === "wwf";
  const title = isWwf
    ? "Catégorisation WWF"
    : "Catégorisation Protein Tracker";
  const description = isWwf
    ? "Classe les produits en groupes alimentaires WWF (FG1–FG7), sous-groupes et composites."
    : "Classe les produits en groupes Protein Tracker (plant-based core, animal core, composite, etc.).";
  const runLabel = isWwf
    ? "Lancer la catégorisation WWF"
    : "Lancer la catégorisation Protein Tracker";
  const resumeLabelBase = isWwf
    ? "Reprendre la catégorisation WWF"
    : "Reprendre la catégorisation Protein Tracker";
  const validationLabel = isWwf
    ? "Voir la validation WWF"
    : "Voir la validation Protein Tracker";

  const total = counts?.total ?? 0;
  const classified = counts?.classified ?? 0;
  const pending = counts?.pending ?? 0;
  const needsReview = counts?.needs_review ?? 0;
  const unknown = counts?.unknown ?? 0;
  const status = counts?.status ?? "locked";
  const pct = total > 0 ? Math.round((classified / total) * 100) : 0;

  // Phase WWF-Q — discriminate "Terminée" (clean) from "Terminée
  // avec erreurs" (job finished but some rows are unknown / failed).
  // The backend's ``counts.status = complete`` is true as soon as
  // ``classified == total`` — it doesn't consider unknowns or job
  // failed_product_count. So we also look at the live job state +
  // unknown count.
  const isRunning =
    currentJob &&
    (currentJob.status === "queued" || currentJob.status === "running");
  const canResume = currentJob && isRunning;
  const failedRows = currentJob?.failed_product_count ?? 0;
  const jobErrored =
    currentJob?.status === "completed_with_errors" ||
    currentJob?.status === "failed";

  // Phase WWF-Q2 — counter dedup. The bug report showed
  // "49 réussies / 102 échouées" on a 100-row dataset because the
  // previous formula did ``unknown + failedRows``, double-counting
  // the same rows: a row stored as ``wwf_food_group=UNKNOWN`` IS
  // the same row the job counted as ``failed_product_count``.
  //
  // The invariant we want is:
  //
  //   successCount + unresolvedCount == total
  //
  // where:
  //   successCount   = rows with a real food group (FG1..FG7 or
  //                    out_of_scope), i.e. classified-not-unknown.
  //   unresolvedCount = everything else (unknown + parse-failed +
  //                     not-yet-classified), derived from total so
  //                     it can never exceed total.
  //
  // We pick max(...) to be defensive if the backend hasn't fully
  // refreshed yet and ``unknown`` is briefly > 0 while
  // ``classified`` still lags.
  const successCount = Math.max(0, classified - unknown);
  const unresolvedCount = Math.max(
    0,
    Math.min(total, total - successCount),
  );
  const hasPartialFailures =
    unresolvedCount > 0 || failedRows > 0 || jobErrored;
  const isCompleteClean =
    status === "complete" && !isRunning && !hasPartialFailures;
  const isCompleteWithErrors =
    status === "complete" && !isRunning && hasPartialFailures;

  // Phase PT-WWF-S2 — split the "complete" pill into three states so
  // a job with manual-review rows is no longer labelled as an error.
  //  - "Terminée"            : all rows classified + accepted (no
  //                            review queue).
  //  - "Terminée · à valider": all rows classified, some in review.
  //  - "Terminée avec erreurs": at least one row is unresolved
  //                              (unknown / failed / job errored).
  let pillTone: "ok" | "warn" | "neutral" = "neutral";
  let pillLabel = "À lancer";
  if (isCompleteClean) {
    if (needsReview > 0) {
      pillTone = "warn";
      pillLabel = "Terminée · à valider";
    } else {
      pillTone = "ok";
      pillLabel = "Terminée";
    }
  } else if (isCompleteWithErrors) {
    pillTone = "warn";
    pillLabel = "Terminée avec erreurs";
  } else if (isRunning) {
    pillTone = "warn";
    pillLabel = "En cours";
  } else if (status === "locked") {
    pillTone = "neutral";
    pillLabel = "Verrouillée";
  } else {
    pillTone = "warn";
    pillLabel = "À lancer";
  }

  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-forest-900">{title}</h3>
          <p className="mt-1 text-xs text-ink-muted">{description}</p>
        </div>
        <Pill tone={pillTone}>{pillLabel}</Pill>
      </div>

      {total > 0 && (
        <div className="mt-3 text-xs text-ink-muted">
          <div className="flex items-center justify-between">
            <span>
              {isCompleteWithErrors ? (
                <>
                  {/* Phase WWF-Q2 — deduplicated counters. success +
                      unresolved == total, no overlap. */}
                  {successCount} réussies / {unresolvedCount} à résoudre
                  {needsReview > 0 && <> · {needsReview} en revue</>}
                </>
              ) : (
                <>
                  {classified}/{total} catégorisé(s)
                  {needsReview > 0 && <> · {needsReview} en revue</>}
                  {pending > 0 && <> · {pending} en attente</>}
                </>
              )}
            </span>
            <span className="font-semibold text-forest-700">{pct}%</span>
          </div>
          <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-line-soft">
            <div
              className={
                "h-full rounded-full transition-all duration-500 ease-out " +
                (isCompleteWithErrors
                  ? "bg-gradient-to-r from-warn-400 to-warn-500"
                  : "bg-gradient-to-r from-brand-400 to-brand-600")
              }
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}

      {/* Phase WWF-K — show the inner job progress box ONLY while
          the job is active. Once the job is terminal, the header
          counts above (driven by classification_by_methodology) are
          the source of truth — the stale per-batch counters in the
          job response are confusing (they showed "0 catégorisé · 0
          accepté · 0 à vérifier" alongside "100/100" in the header). */}
      {currentJob && isRunning && (
        <div className="mt-3">
          <ClassificationJobProgress job={currentJob} />
        </div>
      )}
      {/* Phase WWF-Q — Terminal state with errors banner. Fires when
          (a) the job itself reported completed_with_errors / failed,
          OR (b) the job reported completed but some rows landed as
          ``unknown`` (the user's complaint: "100/100 catégorisé · 51
          en échec" was displayed as a green 'Terminée'). */}
      {!isRunning && hasPartialFailures && (
        <div className="mt-3 rounded-xl border border-warn-100 bg-warn-50 px-3 py-2 text-xs text-warn-700">
          {currentJob?.status === "failed"
            ? `Échec · ${currentJob.error_message ?? "erreur inconnue"}.`
            : (() => {
                // Phase WWF-Q2 — dedup: the same row can appear as
                // both "failed" in the job and "unknown" in the
                // stored classification (the readable fallback stores
                // an unknown classification AND the job's count still
                // ticks the failed counter). Don't add them; use the
                // deduplicated unresolvedCount from the header.
                return (
                  <>
                    Terminée avec erreurs · {unresolvedCount} ligne(s) à
                    résoudre
                    .
                  </>
                );
              })()}
        </div>
      )}

      {error && (
        <div className="mt-3 rounded-xl border border-danger-100 bg-danger-50 px-3 py-2 text-xs text-danger-700">
          {error}
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        {canResume ? (
          <>
            <Button
              onClick={() => onResume(currentJob!.job_id)}
              disabled={busy}
            >
              {`${resumeLabelBase} (${currentJob!.processed_products}/${currentJob!.total_products})`}
            </Button>
            {classified > 0 && (
              <Button variant="secondary" onClick={onOpenValidation}>
                {validationLabel}
              </Button>
            )}
          </>
        ) : isCompleteWithErrors ? (
          // True unresolved rows — retry is primary, validation next.
          <>
            <Button onClick={onRetryFailed} disabled={busy}>
              {`Réessayer ${unresolvedCount} ligne(s)`}
            </Button>
            <Button variant="secondary" onClick={onOpenValidation}>
              {validationLabel}
            </Button>
          </>
        ) : isCompleteClean ? (
          // Phase Product-UX-B — finished cleanly. Guide to validation;
          // reclassify is a secondary action, never the headline.
          <>
            <Button onClick={onOpenValidation}>{validationLabel}</Button>
            <Button variant="secondary" onClick={onRun} disabled={busy}>
              {isWwf ? "Reclassifier WWF" : "Reclassifier"}
            </Button>
          </>
        ) : (
          <Button onClick={onRun} disabled={busy || !latestUpload}>
            {busy ? "…" : runLabel}
          </Button>
        )}
      </div>
    </Card>
  );
}

function StepAIClassificationDual({
  latestUpload,
  ptCounts,
  wwfCounts,
  ptCurrentJob,
  wwfCurrentJob,
  // Phase WWF-Q — split busy per methodology so the two jobs are
  // fully independent.
  busyPt,
  busyWwf,
  ptError,
  wwfError,
  onRunPT,
  onRunWWF,
  onRunBoth,
  onResumePT,
  onResumeWWF,
  onRetryFailedPT,
  onRetryFailedWWF,
  onOpenValidation,
  onNext,
}: {
  latestUpload: UploadResult | null;
  ptCounts: MethodologyClassificationCounts | undefined;
  wwfCounts: MethodologyClassificationCounts | undefined;
  ptCurrentJob: ClassificationJob | null;
  wwfCurrentJob: ClassificationJob | null;
  busyPt: boolean;
  busyWwf: boolean;
  ptError: string | null;
  wwfError: string | null;
  onRunPT: () => void;
  onRunWWF: () => void;
  /** Phase WWF-K — one-click "Lancer les deux catégorisations". */
  onRunBoth: () => void;
  onResumePT: (jobId: string) => void;
  onResumeWWF: (jobId: string) => void;
  onRetryFailedPT: () => void;
  onRetryFailedWWF: () => void;
  onOpenValidation: (methodology: Methodology) => void;
  onNext: () => void;
}) {
  const ptDone = ptCounts?.status === "complete";
  const wwfDone = wwfCounts?.status === "complete";
  // Allow continuing to the next step as soon as at least one of the
  // two methodologies has finished — the user can come back to run
  // the other one later. PT and WWF classification are independent,
  // so blocking the wizard on both being done would be unhelpful.
  const canContinue = ptDone || wwfDone;

  // Phase WWF-K — one-click "lancer les deux". The button label
  // adapts to which methodologies still need running.
  const ptRunning =
    ptCurrentJob &&
    (ptCurrentJob.status === "queued" || ptCurrentJob.status === "running");
  const wwfRunning =
    wwfCurrentJob &&
    (wwfCurrentJob.status === "queued" || wwfCurrentJob.status === "running");
  const anyRunning = ptRunning || wwfRunning;
  const neitherStarted = !ptCurrentJob && !wwfCurrentJob;
  const ptNeeds = !ptDone;
  const wwfNeeds = !wwfDone;
  const bothNeed = ptNeeds && wwfNeeds;
  const runBothLabel = bothNeed
    ? "Lancer les deux catégorisations"
    : ptNeeds
    ? "Lancer la catégorisation Protein Tracker restante"
    : wwfNeeds
    ? "Lancer la catégorisation WWF restante"
    : "Tout est terminé";
  const runBothDisabled = Boolean(
    busyPt ||
      busyWwf ||
      !latestUpload ||
      (!ptNeeds && !wwfNeeds) ||
      anyRunning,
  );
  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-semibold">
          Classification IA — Protein Tracker + WWF
        </h2>
        <p className="mt-1 text-sm text-ink-muted">
          Ce projet a deux méthodologies activées. Vous pouvez lancer
          les deux catégorisations en un clic, ou les piloter
          indépendamment via les cartes ci-dessous.
        </p>
        <p className="mt-1 text-xs text-ink-soft">
          {"Les champs commerciaux (volumes, ventes, prix, marges) ne sont jamais envoyés à l'IA."}
        </p>
      </div>

      {/* Phase WWF-K — primary one-click CTA for PT+WWF. Per-
          methodology cards below remain available for advanced use
          (resume, retry-failed, open validation). */}
      {(neitherStarted || bothNeed || ptNeeds || wwfNeeds) && (
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={onRunBoth} disabled={runBothDisabled}>
            {runBothLabel}
          </Button>
          <span className="text-xs text-ink-soft">
            {bothNeed
              ? "Lance les deux jobs en parallèle. Vous pouvez fermer cette page — chaque job est sauvegardé et reprenable."
              : ptNeeds
              ? "WWF est terminée. Cliquez pour lancer Protein Tracker."
              : wwfNeeds
              ? "Protein Tracker est terminée. Cliquez pour lancer WWF."
              : ""}
          </span>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <MethodologyClassificationCard
          methodology="protein_tracker"
          counts={ptCounts}
          currentJob={ptCurrentJob}
          busy={busyPt}
          error={ptError}
          latestUpload={latestUpload}
          onRun={onRunPT}
          onResume={onResumePT}
          onRetryFailed={onRetryFailedPT}
          onOpenValidation={() => onOpenValidation("protein_tracker")}
        />
        <MethodologyClassificationCard
          methodology="wwf"
          counts={wwfCounts}
          currentJob={wwfCurrentJob}
          busy={busyWwf}
          error={wwfError}
          latestUpload={latestUpload}
          onRun={onRunWWF}
          onResume={onResumeWWF}
          onRetryFailed={onRetryFailedWWF}
          onOpenValidation={() => onOpenValidation("wwf")}
        />
      </div>

      {/* Phase WWF-Q — surface unresolved partial failures BEFORE the
          "Continuer vers Validation" CTA so the operator can't
          accidentally proceed thinking everything is clean.

          Phase WWF-Q2 — deduplicated counters. Computing
          ``total - successCount`` (rather than ``unknown + failed``)
          guarantees the displayed count never exceeds the project's
          eligible products. */}
      {(() => {
        const ptTotal = ptCounts?.total ?? 0;
        const wwfTotal = wwfCounts?.total ?? 0;
        const ptUnknown = ptCounts?.unknown ?? 0;
        const wwfUnknown = wwfCounts?.unknown ?? 0;
        const ptSuccess = Math.max(
          0,
          (ptCounts?.classified ?? 0) - ptUnknown,
        );
        const wwfSuccess = Math.max(
          0,
          (wwfCounts?.classified ?? 0) - wwfUnknown,
        );
        const ptUnresolved = Math.max(0, ptTotal - ptSuccess);
        const wwfUnresolved = Math.max(0, wwfTotal - wwfSuccess);
        const ptFailed = ptCurrentJob?.failed_product_count ?? 0;
        const wwfFailed = wwfCurrentJob?.failed_product_count ?? 0;
        const ptHasErrors =
          ptCurrentJob?.status === "completed_with_errors" ||
          ptCurrentJob?.status === "failed" ||
          ptUnresolved > 0 ||
          ptFailed > 0;
        const wwfHasErrors =
          wwfCurrentJob?.status === "completed_with_errors" ||
          wwfCurrentJob?.status === "failed" ||
          wwfUnresolved > 0 ||
          wwfFailed > 0;
        if (!ptHasErrors && !wwfHasErrors) return null;
        return (
          <div className="rounded-xl border border-warn-100 bg-warn-50 px-3 py-2 text-xs text-warn-700">
            <div className="font-medium">
              {ptHasErrors && wwfHasErrors
                ? "Les deux catégorisations ont des lignes à résoudre."
                : ptHasErrors
                ? `La catégorisation Protein Tracker a ${ptUnresolved} ligne(s) à résoudre.`
                : `La catégorisation WWF a ${wwfUnresolved} ligne(s) à résoudre.`}
            </div>
            <div className="mt-1 text-warn-700">
              Vous pouvez continuer vers la validation pour les corriger
              manuellement, ou cliquer sur « Réessayer » pour relancer les
              lignes en échec.
            </div>
          </div>
        );
      })()}

      <div className="flex flex-wrap gap-3">
        <Button onClick={onNext} variant={canContinue ? "primary" : "secondary"}>
          Continuer vers Validation
        </Button>
      </div>
    </div>
  );
}

function StepValidation({
  projectId,
  accessToken,
  step,
  methodology,
  wwfEnabled,
  ptEnabled,
  wwfOnly,
  onResolved,
  onNext,
}: {
  projectId: string;
  accessToken: string | null;
  step: WorkflowStep;
  methodology: Methodology;
  wwfEnabled: boolean;
  /** Phase WWF-I — true when PT is enabled on the project; lets the
   *  validation table hide the PT toggle for WWF-only projects. */
  ptEnabled: boolean;
  /** Phase WWF-G — when true, the "Continuer" CTA skips NEVO and
   *  goes straight to Calcul WWF. */
  wwfOnly: boolean;
  onResolved: () => void | Promise<void>;
  onNext: () => void;
}) {
  const isNotNeeded = step.status === "not_needed";
  const pending = step.counts.pending ?? 0;

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-semibold">
          {wwfOnly ? "Validation WWF" : "Validation des catégories"}
        </h2>
        <p className="mt-1 text-sm text-ink-muted">
          {wwfOnly
            ? "Tableau de validation WWF : inspectez les groupes alimentaires (FG1–FG7), les sous-groupes et les buckets composites attribués par l'IA et les règles déterministes."
            : "Tableau de validation : voir et corriger les catégories assignées par les règles déterministes et par l'IA."}
        </p>
        <p className="mt-1 text-xs text-ink-soft">
          {"Seuls les champs non commerciaux sont affichés. Volumes, ventes, prix et marges ne sont jamais utilisés pour la classification ni envoyés à l'IA."}
        </p>
      </div>

      {/* Phase 34F — full category validation table for ALL products.
          Phase WWF-I — pass ``ptEnabled`` so the table can hide the
          PT toggle for WWF-only projects and auto-default to WWF. */}
      <ValidationTable
        projectId={projectId}
        accessToken={accessToken}
        wwfEnabled={wwfEnabled}
        ptEnabled={ptEnabled}
        onChanged={onResolved}
      />

      {/* Phase UX-Validation-S — the legacy "InlineReview" lower
          panel + extra Card has been removed. Validation now happens
          exclusively from the ``ValidationTable`` above; review-only
          state is no longer a calculation blocker so we simply move
          on. */}
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="text-sm text-ink-muted">
            {isNotNeeded || pending === 0 ? (
              <span className="text-brand-700">
                Aucun produit en attente de validation manuelle.
              </span>
            ) : (
              <span>
                {pending} produit(s) à vérifier — la validation manuelle
                est recommandée mais non bloquante.
              </span>
            )}
          </div>
          <Button variant="primary" onClick={onNext}>
            {wwfOnly ? "Continuer vers Calcul WWF" : "Continuer vers NEVO"}
          </Button>
        </div>
      </Card>
    </div>
  );
}

function StepNEVO({
  step,
  lastNevoResult,
  busy,
  error,
  onRun,
  onNext,
}: {
  step: WorkflowStep;
  lastNevoResult: ApplyReferencesSummary | null;
  busy: boolean;
  error: string | null;
  onRun: () => void;
  onNext: () => void;
}) {
  const isComplete = step.status === "complete";
  const isNotNeeded = step.status === "not_needed";

  const noMatchProducts = lastNevoResult?.product_results.filter(
    (r) => r.outcome === "no_match"
  ) ?? [];
  const matchedProducts = lastNevoResult?.product_results.filter(
    (r) => r.outcome === "nevo_matched"
  ) ?? [];

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-semibold">Enrichissement NEVO</h2>
        <p className="mt-1 text-sm text-ink-muted">
          NEVO est utilisé en priorité car il peut fournir les protéines totales, végétales et
          animales lorsque disponibles.
        </p>
        <p className="mt-1 text-xs text-ink-soft">
          {"L'IA peut aider à sélectionner une référence NEVO, mais les valeurs nutritionnelles viennent de NEVO, pas de l'IA."}
        </p>
      </div>

      <Card>
        {/* Phase 34D — hard warning when NEVO table is empty / zero matched. */}
        {lastNevoResult?.warning && (
          <div className="mb-3 rounded-xl border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-700">
            <p className="font-medium">Aucun produit n’a été enrichi par NEVO.</p>
            <p className="mt-1 text-xs">{lastNevoResult.warning}</p>
          </div>
        )}
        {lastNevoResult && (
          <div className="mb-3 text-xs text-ink-soft">
            Table NEVO : {lastNevoResult.nevo_total_references} référence(s) chargée(s).
          </div>
        )}
        {isNotNeeded ? (
          <div className="rounded-xl border border-brand-200 bg-mint-100 px-3 py-2 text-sm text-brand-700">
            {"Tous les produits disposent déjà d'une donnée protéique du retailer — NEVO non requis."}
          </div>
        ) : (
          <>
            <CountRow counts={step.counts} />
            <BlockerList step={step} />
          </>
        )}

        {/* Per-product results after running NEVO */}
        {lastNevoResult && (matchedProducts.length > 0 || noMatchProducts.length > 0) && (
          <div className="mt-4 space-y-3">
            {matchedProducts.length > 0 && (
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-brand-700">
                  {matchedProducts.length} produit(s) enrichi(s)
                </p>
                <ul className="mt-1.5 space-y-1">
                  {matchedProducts.map((r) => (
                    <li key={r.product_id} className="flex items-center justify-between text-xs text-forest-700">
                      <span>{r.product_name}</span>
                      <span className="text-ink-soft truncate max-w-[200px]">
                        → {r.reference_name ?? "NEVO"}{r.has_split ? " (split ✓)" : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {noMatchProducts.length > 0 && (
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-warn-700">
                  {noMatchProducts.length} produit(s) sans correspondance NEVO
                </p>
                <ul className="mt-1.5 space-y-1">
                  {noMatchProducts.map((r) => (
                    <li key={r.product_id} className="text-xs text-ink-soft">
                      {r.product_name} — aucune référence NEVO trouvée
                    </li>
                  ))}
                </ul>
                <p className="mt-1.5 text-xs text-ink-soft">
                  Ces produits seront tentés avec CIQUAL à {"l'étape"} suivante.
                </p>
              </div>
            )}
          </div>
        )}

        {error && (
          <div className="mt-3 rounded-xl border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-700">
            {error}
          </div>
        )}
        {/* Phase 34M — once NEVO has been attempted (step.status is
            "complete"), the primary CTA is "Continuer", and the
            re-run button is labelled "Relancer NEVO" so the user
            knows the first run already happened. */}
        <div className="mt-4 flex flex-wrap gap-3">
          {isComplete || isNotNeeded ? (
            <>
              <Button onClick={onNext}>
                Continuer vers la validation nutritionnelle
              </Button>
              {isComplete && (
                <Button variant="secondary" onClick={onRun} disabled={busy}>
                  {busy ? "…" : "Relancer NEVO"}
                </Button>
              )}
            </>
          ) : (
            <Button onClick={onRun} disabled={busy}>
              {busy ? "Enrichissement NEVO en cours…" : "Enrichir avec NEVO"}
            </Button>
          )}
        </div>
      </Card>
    </div>
  );
}

function StepCIQUAL({
  step,
  busy,
  error,
  onRun,
  onNext,
}: {
  step: WorkflowStep;
  busy: boolean;
  error: string | null;
  onRun: () => void;
  onNext: () => void;
}) {
  const isComplete = step.status === "complete";
  const isNotNeeded = step.status === "not_needed";
  const isLocked = step.status === "locked";

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-semibold">Fallback CIQUAL + IA</h2>
        <p className="mt-1 text-sm text-ink-muted">
          {"Uniquement pour les produits encore sans donnée protéique après NEVO. CIQUAL fournit une protéine totale. Comme CIQUAL ne fournit pas de split végétal/animal, l'IA peut aider à sélectionner une référence — qui doit être tracée."}
        </p>
      </div>

      <Card>
        {isNotNeeded ? (
          <div className="rounded-xl border border-brand-200 bg-mint-100 px-3 py-2 text-sm text-brand-700">
            {"Tous les produits disposent d'une donnée protéique exploitable après NEVO — CIQUAL non requis."}
          </div>
        ) : isLocked ? (
          <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-ink-muted">
            {"Complétez d'abord l'étape NEVO avant d'utiliser CIQUAL."}
          </div>
        ) : (
          <>
            <CountRow counts={step.counts} />
            <BlockerList step={step} />
          </>
        )}
        {error && (
          <div className="mt-3 rounded-xl border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-700">
            {error}
          </div>
        )}
        <div className="mt-4 flex flex-wrap gap-3">
          {isComplete || isNotNeeded ? (
            <Button onClick={onNext}>Continuer vers Calcul</Button>
          ) : isLocked ? (
            <Button variant="secondary" disabled>
              {"NEVO d'abord"}
            </Button>
          ) : (
            <Button onClick={onRun} disabled={busy}>
              {busy ? "CIQUAL en cours…" : "Essayer CIQUAL + IA"}
            </Button>
          )}
          {isComplete && (
            <Button variant="secondary" onClick={onRun} disabled={busy}>
              {busy ? "…" : "Ré-enrichir CIQUAL"}
            </Button>
          )}
          <Button variant="ghost" onClick={onNext}>
            Continuer sans CIQUAL →
          </Button>
        </div>
      </Card>
    </div>
  );
}

function StepCalculation({
  step,
  preflight,
  busy,
  error,
  wwfOnly,
  onRun,
  onRunPartial,
  onGoToStep,
  goToStepById,
}: {
  step: WorkflowStep;
  preflight: CalculationPreflightResponse | null;
  busy: boolean;
  error: string | null;
  /** Phase WWF-G — when true, swap PT (protéines/NEVO) labels for
   *  WWF (volumes/groupes alimentaires) labels. */
  wwfOnly: boolean;
  onRun: () => void;
  onRunPartial: () => void;
  onGoToStep: (idx: WizardStepIdx) => void;
  /** Phase WWF-G — step indices are now methodology-dependent, so
   *  the calculation step navigates by stable id instead of by a
   *  hard-coded numeric index. */
  goToStepById: (id: string) => void;
}) {
  const isReady = step.status === "ready";
  const isBlocked = step.status === "blocked";
  // Phase 34N — the preflight endpoint is now the single source of
  // truth for "how many products will be in the next run". If it
  // disagrees with workflow.status's blockers we trust the preflight
  // because it walks the same data the calculation engine walks.
  const readyRows = preflight?.products_ready_for_calculation ?? 0;
  const partialAllowed = readyRows > 0;
  // Phase 34K — partial calculation. When the ONLY remaining blocker
  // is `nutrition_required`, the user can choose to run the
  // calculation on the products that already have usable nutrition.
  // The result page then discloses coverage prominently.
  const nutritionOnlyBlocker =
    isBlocked &&
    step.blocking_reasons.length > 0 &&
    step.blocking_reasons.every((r) => r.code === "nutrition_required");
  const missingNutritionCount =
    preflight?.products_missing_nutrition ??
    step.blocking_reasons.find((r) => r.code === "nutrition_required")?.count ??
    0;

  // Phase WWF-G — navigate by stable step id so the mapping is
  // resilient to WWF-only mode (which drops NEVO + Nutrition).
  const BLOCKER_STEP_ID: Record<string, string> = {
    no_eligible_products: "import",
    classification_required: "ai_class",
    review_pending: "validation",
    nutrition_required: "nevo",
    // Phase Product-UX-A — WWF blocker codes route to the same steps.
    no_eligible_products_wwf: "import",
    classification_required_wwf: "ai_class",
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-semibold">
          {wwfOnly ? "Calcul WWF" : "Calcul"}
        </h2>
        <p className="mt-1 text-sm text-ink-muted">
          {wwfOnly
            ? "Lance le calcul des volumes WWF par groupe alimentaire (FG1–FG7) et la répartition des composites selon les buckets Step 1. Le calcul est bloqué tant que des pré-requis sont manquants."
            : "Lance le calcul du ratio protéines végétales / totales pour tous les produits éligibles. Le calcul est bloqué tant que des pré-requis sont manquants."}
        </p>
      </div>

      <Card>
        <h3 className="text-sm font-semibold text-forest-900">Conditions requises</h3>
        {preflight && (
          <p className="mt-1 text-xs text-ink-muted">
            {preflight.products_ready_for_calculation} sur{" "}
            {preflight.total_products} produit(s) prêt(s) pour le calcul.
            {preflight.products_missing_nutrition > 0 && (
              <>
                {" "}
                {preflight.products_missing_nutrition} sans donnée
                protéique exploitable.
              </>
            )}
          </p>
        )}
        <ul className="mt-2 space-y-1.5">
          {(() => {
            // Phase Hotfix-Validation — manual review is no longer a
            // hard prerequisite. The checklist now distinguishes three
            // marker types:
            //   * "ok" (✓ emerald) — requirement met.
            //   * "warn" (◷ amber) — non-blocking review backlog.
            //   * "missing" (✗ rose) — true blocker; calc disabled.
            type Marker = "ok" | "warn" | "missing";
            const reviewOnly = step.counts.review_only ?? 0;
            // Phase Product-UX-A — methodology-aware checklist. WWF-only
            // projects never require protein nutrition, so the
            // nutrition condition is replaced by a volume/weight one
            // and all wording stays WWF-specific.
            const requiresNutrition = preflight?.requires_nutrition !== false;
            const items: { label: string; marker: Marker }[] = [
              {
                label: "Fichier importé",
                marker:
                  (preflight?.total_products ?? 0) > 0 ? "ok" : "missing",
              },
              {
                label: wwfOnly
                  ? "Classification WWF terminée"
                  : "Classification terminée",
                marker:
                  preflight !== null &&
                  preflight.classified_products === preflight.total_products
                    ? "ok"
                    : "missing",
              },
              {
                label:
                  reviewOnly > 0
                    ? `Validation manuelle — ${reviewOnly} à vérifier (non bloquant)`
                    : "Validation manuelle complète",
                marker: reviewOnly > 0 ? "warn" : "ok",
              },
              requiresNutrition
                ? {
                    label: "Données nutritionnelles disponibles",
                    marker: readyRows > 0 ? "ok" : "missing",
                  }
                : {
                    label: "Données de volume / poids disponibles",
                    marker: readyRows > 0 ? "ok" : "missing",
                  },
            ];
            return items.map((c) => {
              const iconColor =
                c.marker === "ok"
                  ? "text-brand-600"
                  : c.marker === "warn"
                    ? "text-warn-600"
                    : "text-danger-500";
              const labelColor =
                c.marker === "ok"
                  ? "text-forest-700"
                  : c.marker === "warn"
                    ? "text-warn-700"
                    : "text-ink-soft";
              const icon =
                c.marker === "ok" ? "✓" : c.marker === "warn" ? "◷" : "✗";
              return (
                <li
                  key={c.label}
                  className="flex items-center gap-2 text-sm"
                >
                  <span className={iconColor}>{icon}</span>
                  <span className={labelColor}>{c.label}</span>
                </li>
              );
            });
          })()}
        </ul>

        {isBlocked && (() => {
          // Phase 34D — split blockers into two semantic panels so the
          // user understands whether they need to fix categorisation
          // or nutrition. Both groups can be present simultaneously.
          // Phase UX-Validation-S — ``review_pending`` is no longer
          // a blocker (the backend emits it only as a non-blocking
          // ``review_only`` count on the step). The amber warning
          // below renders that count separately.
          // Phase Product-UX-A — recognise WWF blocker codes too so a
          // WWF-only project's blockers render (and never under PT
          // wording).
          const CLASSIF_CODES = new Set([
            "classification_required",
            "no_eligible_products",
            "classification_required_wwf",
            "no_eligible_products_wwf",
          ]);
          const NUTRITION_CODES = new Set(["nutrition_required"]);
          const classifBlockers = step.blocking_reasons.filter((r) =>
            CLASSIF_CODES.has(r.code)
          );
          const nutritionBlockers = step.blocking_reasons.filter((r) =>
            NUTRITION_CODES.has(r.code)
          );
          return (
            <div className="mt-4 space-y-3">
              {classifBlockers.length > 0 && (
                <div className="rounded-xl border border-danger-100 bg-danger-50 px-3 py-2">
                  <p className="text-sm font-medium text-danger-700">
                    Catégorisation incomplète
                  </p>
                  <p className="mt-0.5 text-xs text-danger-600">
                    {wwfOnly
                      ? "Certains produits n'ont pas encore de groupe alimentaire WWF."
                      : "Certains produits n'ont pas encore de catégorie Protein Tracker validée."}
                  </p>
                  <ul className="mt-2 space-y-1.5">
                    {classifBlockers.map((r) => {
                      const targetId = BLOCKER_STEP_ID[r.code];
                      return (
                        <li
                          key={r.code}
                          className="flex items-start justify-between gap-3"
                        >
                          <span className="text-xs text-danger-700">
                            ▸ {r.label}
                            {r.count > 0 ? ` (${r.count})` : ""}
                          </span>
                          {targetId !== undefined && (
                            <button
                              type="button"
                              onClick={() => goToStepById(targetId)}
                              className="shrink-0 text-xs text-brand-600 hover:underline"
                            >
                              Corriger →
                            </button>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
              {nutritionBlockers.length > 0 && (
                <div className="rounded-xl border border-warn-100 bg-warn-50 px-3 py-2">
                  <p className="text-sm font-medium text-warn-700">
                    Données protéiques manquantes
                  </p>
                  <p className="mt-0.5 text-xs text-warn-700">
                    {"Certains produits sont catégorisés, mais n'ont pas encore de protéine exploitable."}
                  </p>
                  <ul className="mt-2 space-y-1.5">
                    {nutritionBlockers.map((r) => {
                      const targetId = BLOCKER_STEP_ID[r.code];
                      return (
                        <li
                          key={r.code}
                          className="flex items-start justify-between gap-3"
                        >
                          <span className="text-xs text-warn-700">
                            ▸ {r.label}
                            {r.count > 0 ? ` (${r.count})` : ""}
                          </span>
                          {targetId !== undefined && (
                            <button
                              type="button"
                              onClick={() => goToStepById(targetId)}
                              className="shrink-0 text-xs text-brand-600 hover:underline"
                            >
                              Corriger →
                            </button>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
            </div>
          );
        })()}

        <CountRow counts={step.counts} />

        {error && (
          <div className="mt-3 rounded-xl border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-700">
            {error}
          </div>
        )}

        {/* Phase UX-Validation-S — non-blocking "review backlog"
            notice. ``review_only`` is the count of products with an
            AI/deterministic classification still queued for human
            review. It does NOT block the calculation, but the analyst
            should know the backlog exists. */}
        {(step.counts.review_only ?? 0) > 0 && (
          <div className="mt-3 rounded-xl border border-warn-100 bg-warn-50 px-3 py-2 text-sm text-warn-700">
            <p className="font-medium">
              {step.counts.review_only} produit(s) encore à vérifier
            </p>
            <p className="mt-1 text-xs">
              Le calcul peut être lancé avec les catégories actuelles.
              Les corrections manuelles affineront le résultat lors du
              prochain calcul.{" "}
              <button
                type="button"
                onClick={() => goToStepById(BLOCKER_STEP_ID["review_pending"] ?? -1)}
                className="text-brand-700 hover:underline"
              >
                Voir les produits à vérifier →
              </button>
            </p>
          </div>
        )}

        {/* Phase 34K/N — partial-calc CTA. The preflight tells us
            exactly how many products will be in the run; if any are
            missing nutrition we show the warning + the secondary
            partial-calc button. */}
        {(nutritionOnlyBlocker || missingNutritionCount > 0) && (
          <div className="mt-3 rounded-xl border border-warn-100 bg-warn-50 px-3 py-2 text-sm text-warn-700">
            <p className="font-medium">Données nutritionnelles incomplètes</p>
            <p className="mt-1 text-xs">
              {missingNutritionCount} produit(s) sans donnée protéique
              exploitable. {readyRows} produit(s) prêts seront inclus
              dans le calcul. Le rapport indiquera explicitement le
              pourcentage de produits couverts.
            </p>
          </div>
        )}

        {preflight && preflight.sample_exclusion_reasons.length > 0 && (
          <details className="mt-3 text-xs text-ink-soft">
            <summary className="cursor-pointer hover:text-forest-700">
              Voir un échantillon des produits exclus
            </summary>
            <ul className="mt-1 list-disc pl-4">
              {preflight.sample_exclusion_reasons.slice(0, 10).map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </details>
        )}

        <div className="mt-4 flex flex-wrap gap-3">
          <Button onClick={onRun} disabled={!isReady || busy}>
            {busy ? "Calcul en cours…" : "Lancer le calcul"}
          </Button>
          {(nutritionOnlyBlocker || missingNutritionCount > 0) && (
            <Button
              variant="secondary"
              onClick={onRunPartial}
              disabled={busy || !partialAllowed}
            >
              {busy
                ? "…"
                : partialAllowed
                  ? "Calculer sur les données disponibles"
                  : "Aucun produit exploitable"}
            </Button>
          )}
        </div>
      </Card>
    </div>
  );
}

function StepReport({
  projectId,
  accessToken,
  step,
  latestRun,
  isAltera,
}: {
  projectId: string;
  accessToken: string | null;
  step: WorkflowStep;
  latestRun: Run | null;
  isAltera: boolean;
}) {
  const hasRun = step.status === "complete" && latestRun !== null;

  // Phase Product-UX-B/D — fetch the full ReportDocument so the guided
  // result step shows the beautiful report inline (no forced click-out
  // to /runs/:id). We track explicit loading/error states so we never
  // silently degrade to the old compact summary when the full report
  // should be available (Phase Product-UX-D).
  const reportApi = useMemo(() => createApi(accessToken), [accessToken]);
  const [report, setReport] = useState<ReportDocument | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  useEffect(() => {
    let active = true;
    if (!latestRun) {
      setReport(null);
      setReportLoading(false);
      setReportError(false);
      return;
    }
    setReportLoading(true);
    setReportError(false);
    reportApi
      .getReport(projectId, latestRun.id)
      .then((d) => {
        if (!active) return;
        setReport(d);
        setReportLoading(false);
      })
      .catch((err) => {
        if (!active) return;
        // Surface, don't swallow: log the backend error and show an
        // actionable message rather than the old compact summary.
        console.error("Failed to load guided report", err);
        setReport(null);
        setReportError(true);
        setReportLoading(false);
      });
    return () => {
      active = false;
    };
  }, [reportApi, projectId, latestRun, reloadKey]);

  // No successful run yet — invite the user back to the calculation step.
  if (!hasRun) {
    return (
      <div className="space-y-5">
        <div>
          <h2 className="text-xl font-semibold">Résultat / rapport</h2>
          <p className="mt-1 text-sm text-ink-muted">
            Le rapport complet s’affiche ici après un calcul réussi.
          </p>
        </div>
        <Card>
          <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-ink-muted">
            {"Aucun calcul effectué. Revenez à l'étape Calcul pour lancer un premier calcul."}
          </div>
        </Card>
      </div>
    );
  }

  // Phase Product-UX-D — the full guided report is the ONLY user-facing
  // result. We render it as soon as it loads; while it loads we show a
  // skeleton; if it truly fails we show an actionable error (never a
  // silent fall-back to a compact summary).
  if (report) {
    return (
      <div className="space-y-5">
        <RunReport doc={report} />
        {/* The technical/export detail page is an admin/debug surface;
            keep it out of the normal client flow. */}
        {isAltera && (
          <div className="border-t border-gray-100 pt-3">
            <Link href={`/projects/${projectId}/runs/${latestRun!.id}`}>
              <Button variant="ghost" className="text-xs">
                Détail technique (admin) →
              </Button>
            </Link>
            <p className="mt-1 text-xs text-ink-soft">
              Exports CSV/JSON/Markdown et historique d’approbation.
            </p>
          </div>
        )}
      </div>
    );
  }

  if (reportError) {
    return (
      <div className="space-y-5">
        <div>
          <h2 className="text-xl font-semibold">Résultat / rapport</h2>
        </div>
        <Card>
          <div className="rounded-xl border border-danger-100 bg-danger-50 px-4 py-3 text-sm text-danger-700">
            <p className="font-medium">
              Le rapport complet n’a pas pu être chargé.
            </p>
            <p className="mt-1 text-xs">
              Une erreur est survenue lors de la génération du rapport pour ce
              calcul. Réessayez dans un instant ; si le problème persiste,
              relancez un calcul depuis l’étape Calcul.
            </p>
          </div>
          <div className="mt-4">
            <Button variant="secondary" onClick={() => setReloadKey((k) => k + 1)}>
              Réessayer
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
        <h2 className="text-xl font-semibold">Résultat / rapport</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Préparation de votre rapport…
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

// ---------------------------------------------------------------------------
// Main wizard page
// ---------------------------------------------------------------------------

export default function WorkflowWizardPage() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const projectId = params.id;
  const { accessToken, isAltera } = useAuth();
  const api = useMemo(() => createApi(accessToken), [accessToken]);

  const [status, setStatus] = useState<WorkflowStatus | null>(null);
  const [uploads, setUploads] = useState<UploadResult[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Which wizard step is displayed (0-8)
  const [activeIdx, setActiveIdx] = useState<WizardStepIdx | null>(null);

  // Per-step action state
  const [busy, setBusy] = useState(false);
  // Phase WWF-Q — separate busy flags per methodology so PT and WWF
  // classification jobs are fully independent. Before this fix the
  // single ``busy`` flag was set/cleared by both handlers: when PT's
  // pollJob finished and called setBusy(false), the UI re-enabled the
  // WWF Run button mid-run, which the user perceived as WWF "stopping"
  // even though its own poll loop was still alive. The shared flag
  // also blocked WWF retry-failed while PT was still polling.
  const [busyPt, setBusyPt] = useState(false);
  const [busyWwf, setBusyWwf] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  // Phase 34C — store last classify and enrichment results for UI feedback
  const [lastClassifyResult, setLastClassifyResult] = useState<ClassifySummary | null>(null);
  const [lastNevoResult, setLastNevoResult] = useState<ApplyReferencesSummary | null>(null);
  // Phase 34N — calculation preflight diagnostic. Fetched on every
  // refresh; null until the first fetch completes.
  const [preflight, setPreflight] = useState<CalculationPreflightResponse | null>(null);
  // Phase 34R — async classification job state. The wizard polls
  // POST /advance every 2s until the status is terminal; ``jobError``
  // carries a transient polling failure that should NOT wipe the
  // current job state (see L below).
  const [currentJob, setCurrentJob] = useState<ClassificationJob | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  // Phase WWF-H — independent WWF job state for PT+WWF projects. The
  // PT card uses ``currentJob``; the WWF card uses ``currentJobWwf``.
  // Single-methodology projects keep using ``currentJob`` only.
  const [currentJobWwf, setCurrentJobWwf] = useState<ClassificationJob | null>(
    null,
  );
  const [jobErrorWwf, setJobErrorWwf] = useState<string | null>(null);
  const [actionErrorWwf, setActionErrorWwf] = useState<string | null>(null);

  // ----------- data loading -----------

  const refresh = useCallback(async () => {
    try {
      const [s, u, r] = await Promise.all([
        api.getWorkflowStatus(projectId),
        api.listUploads(projectId),
        api.listRuns(projectId),
      ]);
      setStatus(s);
      setUploads(u.items ?? []);
      setRuns(r.items ?? []);
      // Phase 34N — the preflight is best-effort: a 403 / 404 means
      // the user can't reach the project's preflight yet (e.g. the
      // upload step isn't complete). We swallow the error so the
      // wizard still renders.
      try {
        // Phase Product-UX-A — request the preflight for the project's
        // calculation methodology. WWF-only projects must use the WWF
        // preflight (no Protein Tracker / nutrition requirements).
        const methos = s.methodologies_enabled ?? [];
        const prefMethodology =
          methos.includes("wwf") && !methos.includes("protein_tracker")
            ? "wwf"
            : "protein_tracker";
        const pf = await api.getCalculationPreflight(
          projectId,
          prefMethodology,
        );
        setPreflight(pf);
      } catch {
        setPreflight(null);
      }
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Échec du chargement");
    }
  }, [api, projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Phase WWF-G — methodology-aware visible step list. Memoised so
  // identity is stable across renders. WWF-only projects drop NEVO
  // + Nutrition Validation; the calculation/report labels also shift.
  const visibleSteps = useMemo(
    () => buildWizardSteps(status?.methodologies_enabled ?? []),
    [status?.methodologies_enabled],
  );
  const maxIdx = Math.max(0, visibleSteps.length - 1);
  const ptEnabled =
    status?.methodologies_enabled.includes("protein_tracker") ?? true;
  const wwfEnabled = status?.methodologies_enabled.includes("wwf") ?? false;
  const wwfOnly = wwfEnabled && !ptEnabled;
  /** Phase WWF-H — true for PT+WWF projects; triggers the dual
   *  classification panel on the AI step. */
  const ptWwfMode = ptEnabled && wwfEnabled;
  /** Methodology to use for AI classification + calculation routes.
   *  WWF-only → wwf; otherwise prefer Protein Tracker (which is the
   *  primary methodology in PT+WWF projects too — WWF gets its own
   *  CTA via the dual classification panel). */
  const primaryMethodology: Methodology = wwfOnly ? "wwf" : "protein_tracker";
  /** Phase WWF-H — per-methodology counts straight from the backend.
   *  Empty for PT-only / WWF-only projects' inverse methodology. */
  const ptClassificationCounts =
    status?.classification_by_methodology?.protein_tracker;
  const wwfClassificationCounts =
    status?.classification_by_methodology?.wwf;

  // Auto-select the active backend step when first loaded
  useEffect(() => {
    if (!status || activeIdx !== null) return;
    const stepParam = searchParams.get("step");
    if (stepParam) {
      const n = parseInt(stepParam, 10) - 1;
      if (n >= 0 && n <= maxIdx) {
        setActiveIdx(n as WizardStepIdx);
        return;
      }
    }
    const bKey = status.active_step ?? status.current_step;
    setActiveIdx(backendKeyToWizardIdx(visibleSteps, bKey));
  }, [status, activeIdx, searchParams, visibleSteps, maxIdx]);

  // ----------- actions -----------

  const latestUpload: UploadResult | null = uploads[0] ?? null;
  const latestRun: Run | null = runs[0] ?? null;

  // Phase 35A — auto-detect an active classification job on the AI
  // classification step. If the user closed the tab or got disconnected
  // after the wizard hit "Trop d'échecs réseau consécutifs", this picks
  // the job back up the moment they return to the AI step.
  //
  // Phase WWF-G — the AI step's index now depends on visibleSteps; look
  // it up by id instead of hard-coding 2.
  const aiStepIdx = visibleSteps.find((s) => s.id === "ai_class")?.idx ?? 2;
  useEffect(() => {
    if (activeIdx !== aiStepIdx) return;
    if (currentJob) return; // already have one
    if (!latestUpload || !status) return;
    let cancelled = false;
    void api
      .getActiveClassificationJob(
        projectId,
        latestUpload.id,
        primaryMethodology,
      )
      .then((job) => {
        if (cancelled || !job) return;
        setCurrentJob(job);
      })
      .catch(() => {
        // Silent — 404 is the "nothing to resume" case which the
        // client already converts to null. Any other failure is a
        // transient backend issue we shouldn't surface here.
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIdx, aiStepIdx, latestUpload?.id, primaryMethodology]);

  // Phase WWF-H — parallel auto-detect for the WWF classification job
  // when the project is PT+WWF. The PT card uses ``currentJob``; the
  // WWF card uses ``currentJobWwf``. We only probe in PT+WWF mode —
  // single-methodology projects already covered by the block above.
  useEffect(() => {
    if (!ptWwfMode) return;
    if (activeIdx !== aiStepIdx) return;
    if (currentJobWwf) return;
    if (!latestUpload || !status) return;
    let cancelled = false;
    void api
      .getActiveClassificationJob(projectId, latestUpload.id, "wwf")
      .then((job) => {
        if (cancelled || !job) return;
        setCurrentJobWwf(job);
      })
      .catch(() => {
        // Silent — see PT block above.
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIdx, aiStepIdx, latestUpload?.id, ptWwfMode]);

  async function runAction(fn: () => Promise<void>) {
    // Phase 34P — guard against duplicate invocation if the user clicks
    // the action button twice (e.g. while the network is slow). Without
    // this guard a second classify call could fire and stomp the first
    // run's state. The component buttons already gate on ``busy`` but
    // we also gate here as a defence-in-depth.
    if (busy) return;
    setBusy(true);
    setActionError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      if (e instanceof ApiError && typeof e.detail === "object" && e.detail !== null) {
        const d = e.detail as { message?: string; error_code?: string };
        // Phase 34P+U — map known failure codes to friendly French.
        const friendly =
          d.error_code === "classify_failed"
            ? "La classification IA a échoué côté serveur. Réessayez ou contactez l'équipe Altera."
            : d.error_code === "upload_not_found"
            ? "Fichier introuvable — il a peut-être été supprimé. Re-importez le CSV."
            : d.error_code === "classify_invalid_request"
            ? `Requête invalide : ${d.message ?? "vérifier les options"}`
            : d.error_code === "zero_usable_nutrition"
            ? "Aucun produit ne dispose de données protéiques exploitables. Complétez au moins une ligne dans la validation nutritionnelle (ou exécutez NEVO si ce n'est pas encore fait)."
            : d.error_code === "run_not_ready"
            ? `Le calcul ne peut pas être lancé : ${d.message ?? "des étapes restent à compléter"}.`
            : d.error_code === "response_serialization_failed"
            ? "Le serveur a renvoyé une réponse invalide. L'équipe Altera a été notifiée — réessayez dans quelques instants."
            : d.error_code === "classification_job_conflict"
            ? "Une autre exécution est en cours. Patientez quelques secondes puis réessayez."
            : d.message;
        setActionError(friendly ?? String(e));
      } else if (e instanceof Error && e.message.includes("Failed to fetch")) {
        setActionError(
          "Impossible de joindre le serveur. Vérifiez votre connexion puis réessayez.",
        );
      } else {
        setActionError(e instanceof Error ? e.message : "Erreur inattendue");
      }
    } finally {
      setBusy(false);
    }
  }

  function advanceTo(idx: WizardStepIdx) {
    setActiveIdx(idx);
    setActionError(null);
  }

  function advanceNext() {
    if (activeIdx === null) return;
    const next = Math.min(maxIdx, activeIdx + 1) as WizardStepIdx;
    advanceTo(next);
  }

  // Phase WWF-G — navigate by stable step id (used by the calculation
  // step's "Corriger" blocker links). Falls through silently if the
  // requested step isn't visible (e.g. nutrition_required blocker on a
  // WWF-only project should never appear, but defence-in-depth: a
  // missing id is a no-op).
  function goToStepById(id: string) {
    const target = visibleSteps.find((s) => s.id === id);
    if (target) advanceTo(target.idx as WizardStepIdx);
  }

  // Phase 34R — Chunked async classification.
  // Synchronous classify is gone for 100+ row CSVs because Render's
  // HTTP timeout (~30-60s) is far shorter than what a real OpenAI
  // run takes at scale (1050 rows ~= 5+ minutes). The new flow:
  //
  //   1. POST  /classification-jobs              → create + return job_id
  //   2. POST  /classification-jobs/:id/advance  → process next ~25 products
  //   3. Loop step 2 every ~2s until status is terminal.
  //
  // The browser drives the loop; if the user closes the tab the job
  // halts at the last persisted batch and resumes on next visit.
  const pollJob = useCallback(
    async (jobId: string) => {
      // Each tick advances by one batch then sleeps 1.5s. If a tick
      // throws (network blip), we surface a transient warning but
      // keep the previous job state — the loop retries.
      let consecutiveFailures = 0;
      // Outer loop is intentional — we want to keep polling until
      // terminal even if individual ticks fail.
      // eslint-disable-next-line no-constant-condition
      while (true) {
        try {
          const updated = await api.advanceClassificationJob(projectId, jobId);
          setCurrentJob(updated);
          setJobError(null);
          consecutiveFailures = 0;
          if (
            CLASSIFICATION_JOB_TERMINAL_STATUSES.includes(updated.status)
          ) {
            await refresh();
            return;
          }
        } catch (e) {
          consecutiveFailures += 1;
          setJobError(
            "Connexion temporairement interrompue. Nouvelle tentative…",
          );
          if (consecutiveFailures >= 5) {
            // Phase 35C — improved dead-end message. The job state
            // is persisted server-side; the user just has to click
            // "Reprendre la classification" (which the StepAI block
            // now renders thanks to ``currentJob`` being non-terminal
            // and ``busy`` being false after this return).
            setJobError(
              "Connexion interrompue. Le traitement est sauvegardé et peut être repris.",
            );
            return;
          }
          // Catch-all: wait a bit longer between retries.
          await new Promise((r) => setTimeout(r, 3000));
          continue;
        }
        await new Promise((r) => setTimeout(r, 1500));
      }
    },
    [api, projectId, refresh],
  );

  // Phase 35A — resume the existing active job instead of creating
  // a duplicate. Used by:
  //   - Step 4 mount when a non-terminal job was found.
  //   - The 5-failures dead-end ("Reprendre la classification" button).
  //   - The 409 ``classification_job_active`` short-circuit.
  function handleResumeClassify(jobId: string) {
    // Phase WWF-Q — PT-specific busy guard so resuming WWF in
    // parallel doesn't trip on a PT poll loop still in flight.
    if (busyPt) return;
    setBusyPt(true);
    setActionError(null);
    setJobError(null);
    api
      .getClassificationJob(projectId, jobId)
      .then(async (job) => {
        setCurrentJob(job);
        if (!CLASSIFICATION_JOB_TERMINAL_STATUSES.includes(job.status)) {
          await pollJob(job.job_id);
        }
      })
      .catch((e: Error) => {
        setActionError(e.message ?? "Impossible de reprendre la classification.");
      })
      .finally(() => {
        setBusyPt(false);
      });
  }

  function handleClassifyAI() {
    if (!latestUpload || !status) return;
    if (busyPt) return; // Phase WWF-Q — PT-specific duplicate-click guard
    // Phase WWF-G — primary methodology is WWF for WWF-only projects,
    // PT otherwise (PT-only or PT+WWF).
    const methodology: Methodology = primaryMethodology;
    setBusyPt(true);
    setActionError(null);
    setJobError(null);
    api
      .createClassificationJob(projectId, latestUpload.id, {
        methodology,
        overwrite: false,
        only_missing_or_failed: true,
      })
      .then(async (job) => {
        setCurrentJob(job);
        await pollJob(job.job_id);
      })
      .catch((e: Error) => {
        if (e instanceof ApiError && typeof e.detail === "object" && e.detail !== null) {
          const d = e.detail as {
            message?: string;
            error_code?: string;
            job_id?: string;
            active_classification_jobs?: number;
            active_ingestion_jobs?: number;
          };
          // Phase 35B — map new heavy-job / resume error codes.
          if (d.error_code === "classification_job_active" && d.job_id) {
            // Server says a job is already active for this exact
            // upload+methodology. Auto-resume it instead of erroring.
            void handleResumeClassify(d.job_id);
            return;
          }
          if (d.error_code === "heavy_job_in_progress") {
            // Phase 36A — this 409 now means a heavy job is ACTIVELY
            // running (last advance < 2 min ago). Paused/idle jobs
            // no longer trip the guard; if the wizard hits this, the
            // platform really is processing something else right now.
            setActionError(
              "Un traitement volumineux est actuellement en cours sur la " +
                "plateforme. Il peut provenir d'une autre organisation. " +
                "Réessayez dans quelques minutes — un traitement en pause " +
                "sur votre fichier reste reprenable.",
            );
            return;
          }
          setActionError(d.message ?? String(e));
        } else if (e.message?.includes("Failed to fetch")) {
          setActionError(
            "Impossible de joindre le serveur. Vérifiez votre connexion puis réessayez.",
          );
        } else {
          setActionError(e.message ?? "Échec de la classification IA.");
        }
      })
      .finally(() => {
        setBusyPt(false);
      });
  }

  function handleRetryFailed() {
    if (!currentJob) return;
    if (busyPt) return;
    setBusyPt(true);
    setActionError(null);
    setJobError(null);
    api
      .retryFailedClassificationJob(projectId, currentJob.job_id)
      .then(async (job) => {
        setCurrentJob(job);
        await pollJob(job.job_id);
      })
      .catch((e: Error) => {
        setActionError(e.message ?? "Échec lors du redémarrage de la classification IA.");
      })
      .finally(() => {
        setBusyPt(false);
      });
  }

  // ---------------------------------------------------------------------
  // Phase WWF-H — WWF-specific job handlers used by the dual
  // classification panel on PT+WWF projects. Each is a thin variant of
  // the PT handler that targets methodology="wwf" and pushes state
  // into ``currentJobWwf`` instead of ``currentJob``.
  // ---------------------------------------------------------------------

  const pollJobWwf = useCallback(
    async (jobId: string) => {
      let consecutiveFailures = 0;
      // eslint-disable-next-line no-constant-condition
      while (true) {
        try {
          const updated = await api.advanceClassificationJob(projectId, jobId);
          setCurrentJobWwf(updated);
          setJobErrorWwf(null);
          consecutiveFailures = 0;
          if (CLASSIFICATION_JOB_TERMINAL_STATUSES.includes(updated.status)) {
            await refresh();
            return;
          }
        } catch {
          consecutiveFailures += 1;
          setJobErrorWwf(
            "Connexion temporairement interrompue. Nouvelle tentative…",
          );
          if (consecutiveFailures >= 5) {
            setJobErrorWwf(
              "Connexion interrompue. Le traitement WWF est sauvegardé et peut être repris.",
            );
            return;
          }
          await new Promise((r) => setTimeout(r, 3000));
          continue;
        }
        await new Promise((r) => setTimeout(r, 1500));
      }
    },
    [api, projectId, refresh],
  );

  function handleResumeClassifyWwf(jobId: string) {
    // Phase WWF-Q — WWF-specific busy guard so PT's poll loop can
    // be in flight without blocking the WWF user actions.
    if (busyWwf) return;
    setBusyWwf(true);
    setActionErrorWwf(null);
    setJobErrorWwf(null);
    api
      .getClassificationJob(projectId, jobId)
      .then(async (job) => {
        setCurrentJobWwf(job);
        if (!CLASSIFICATION_JOB_TERMINAL_STATUSES.includes(job.status)) {
          await pollJobWwf(job.job_id);
        }
      })
      .catch((e: Error) => {
        setActionErrorWwf(
          e.message ?? "Impossible de reprendre la catégorisation WWF.",
        );
      })
      .finally(() => {
        setBusyWwf(false);
      });
  }

  function handleClassifyWwf() {
    if (!latestUpload || !status) return;
    if (busyWwf) return; // Phase WWF-Q — WWF-specific guard
    setBusyWwf(true);
    setActionErrorWwf(null);
    setJobErrorWwf(null);
    api
      .createClassificationJob(projectId, latestUpload.id, {
        methodology: "wwf",
        overwrite: false,
        only_missing_or_failed: true,
      })
      .then(async (job) => {
        setCurrentJobWwf(job);
        await pollJobWwf(job.job_id);
      })
      .catch((e: Error) => {
        if (
          e instanceof ApiError &&
          typeof e.detail === "object" &&
          e.detail !== null
        ) {
          const d = e.detail as {
            message?: string;
            error_code?: string;
            job_id?: string;
          };
          if (d.error_code === "classification_job_active" && d.job_id) {
            void handleResumeClassifyWwf(d.job_id);
            return;
          }
          setActionErrorWwf(d.message ?? String(e));
        } else if (e.message?.includes("Failed to fetch")) {
          setActionErrorWwf(
            "Impossible de joindre le serveur. Vérifiez votre connexion puis réessayez.",
          );
        } else {
          setActionErrorWwf(
            e.message ?? "Échec de la catégorisation WWF.",
          );
        }
      })
      .finally(() => {
        setBusyWwf(false);
      });
  }

  function handleRetryFailedWwf() {
    if (!currentJobWwf) return;
    if (busyWwf) return;
    setBusyWwf(true);
    setActionErrorWwf(null);
    setJobErrorWwf(null);
    api
      .retryFailedClassificationJob(projectId, currentJobWwf.job_id)
      .then(async (job) => {
        setCurrentJobWwf(job);
        await pollJobWwf(job.job_id);
      })
      .catch((e: Error) => {
        setActionErrorWwf(
          e.message ?? "Échec lors du redémarrage de la catégorisation WWF.",
        );
      })
      .finally(() => {
        setBusyWwf(false);
      });
  }

  // Phase WWF-K — one-click "lancer les deux catégorisations" for
  // PT+WWF projects. The orchestrator fires PT and WWF in parallel:
  // each has its own state (currentJob vs currentJobWwf), its own
  // poll loop, and its own error slot, so the two jobs progress
  // independently. The browser's two HTTP connections are the
  // implicit concurrency limit — no extra OpenAI requests per row.
  function handleClassifyBoth() {
    if (!latestUpload || !status) return;
    // Phase WWF-Q — each methodology owns its own busy guard; no
    // shared check here. Each handler self-checks busyPt / busyWwf.
    const ptDone = ptClassificationCounts?.status === "complete";
    const wwfDone = wwfClassificationCounts?.status === "complete";
    if (!ptDone) handleClassifyAI();
    if (!wwfDone) handleClassifyWwf();
  }

  function handleOpenValidation(methodology: Methodology) {
    // Phase WWF-H — methodology-specific validation navigation. We
    // jump to the Validation step and pass the methodology hint via a
    // query parameter so the ValidationTable can default to the right
    // view if it supports filtering.
    const target = visibleSteps.find((s) => s.id === "validation");
    if (target) advanceTo(target.idx as WizardStepIdx);
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      url.searchParams.set("methodology", methodology);
      window.history.replaceState({}, "", url.toString());
    }
  }

  function handleApplyNEVO() {
    void runAction(() =>
      api.applyNutritionReferences(projectId, { providers: ["nevo"] })
        .then((result) => { setLastNevoResult(result); })
    );
  }

  function handleApplyCIQUAL() {
    void runAction(() =>
      api.applyNutritionReferences(projectId, { providers: ["ciqual"] }).then(() => {})
    );
  }

  function handleCreateRun() {
    if (!status) return;
    // Phase WWF-G — primary methodology drives the run; index of the
    // report step is now dynamic (last step in visibleSteps).
    const methodology: Methodology = primaryMethodology;
    void runAction(async () => {
      await api.createRun(projectId, methodology);
      goToStepById("report");
    });
  }

  // Phase 34K — partial calculation: same as handleCreateRun but
  // passes allow_partial=true so the backend lets the run through
  // when products are missing nutrition data.
  function handleCreateRunPartial() {
    if (!status) return;
    const methodology: Methodology = primaryMethodology;
    void runAction(async () => {
      await api.createRun(projectId, methodology, { allow_partial: true });
      goToStepById("report");
    });
  }

  // ----------- render -----------

  if (loadError) {
    return (
      <div className="mx-auto max-w-3xl">
        <div className="rounded-xl border border-danger-100 bg-danger-50 px-4 py-3 text-sm text-danger-700">
          {loadError}
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

  if (!status || activeIdx === null) {
    return <div className="text-sm text-ink-soft">Chargement…</div>;
  }

  // Phase WWF-G — index into the methodology-aware visible step list.
  // If the previously-selected index is now out of range (e.g. a WWF
  // project re-opened the workflow page after a methodology change),
  // we fall back to the first visible step.
  const safeActiveIdx = Math.min(activeIdx, maxIdx);
  const currentStep = visibleSteps[safeActiveIdx] ?? visibleSteps[0];
  const activeStepId = currentStep.id;
  const backendStepForActive = backendStep(status, currentStep.backendKey);

  // Compute per-wizard-step accessibility from backend step data
  function wizardStepAccessible(
    ws: WizardStepDef & { idx: number },
  ): boolean {
    const bs = backendStep(status!, ws.backendKey);
    return bs?.accessible ?? false;
  }

  function wizardStepStatus(ws: WizardStepDef & { idx: number }): string {
    return backendStep(status!, ws.backendKey)?.status ?? "locked";
  }

  function wizardStepSummary(
    ws: WizardStepDef & { idx: number },
  ): string | null {
    return backendStep(status!, ws.backendKey)?.summary ?? null;
  }

  const activeBackendStep = backendStepForActive ?? ({
    key: currentStep.backendKey,
    label: currentStep.label,
    status: "locked",
    progress_pct: 0,
    counts: {},
    blocking_reasons: [],
    accessible: false,
    editable: false,
    summary: null,
  } as WorkflowStep);

  return (
    // Phase UX-Validation-S — widened container so the validation
    // table (PT + WWF side-by-side columns) can use the available
    // page width instead of being cramped inside a 4xl card.
    <div className="mx-auto w-full max-w-7xl px-4">
      {/* Header — premium hero band with the workflow context. */}
      <div className="mb-5 overflow-hidden rounded-3xl bg-forest-hero p-6 shadow-card">
        <div className="flex items-start justify-between gap-4">
          <div>
            <span className="inline-flex items-center rounded-full bg-white/15 px-2.5 py-0.5 text-[11px] font-medium uppercase tracking-wider text-mint-100 ring-1 ring-white/20">
              {wwfOnly ? "WWF Planet-Based Diets" : "Parcours guidé"}
            </span>
            <h1 className="mt-2 text-2xl font-semibold tracking-tight text-white">
              {wwfOnly
                ? "Parcours WWF Planet-Based Diets"
                : "Parcours guidé"}
            </h1>
            <p className="mt-1 text-sm text-mint-100/90">
              Étape {safeActiveIdx + 1} sur {visibleSteps.length} ·
              Progression {status.overall_progress_pct} %
            </p>
          </div>
          <Link
            href={`/projects/${projectId}`}
            className="shrink-0 rounded-lg px-2.5 py-1 text-xs text-mint-100/70 transition-colors hover:bg-white/10 hover:text-white"
          >
            Détail technique →
          </Link>
        </div>
        {/* Progress bar inside the hero band. */}
        <div className="mt-5 h-2 w-full overflow-hidden rounded-full bg-white/15">
          <div
            className="h-full rounded-full bg-gradient-to-r from-lime-200 to-brand-400 transition-all duration-500 ease-out"
            style={{
              width: `${Math.min(100, Math.max(0, status.overall_progress_pct))}%`,
            }}
          />
        </div>
      </div>

      {/* Horizontal stepper — methodology-aware (Phase WWF-G). */}
      <div className="mt-5 flex items-start justify-between gap-1 overflow-x-auto pb-2">
        {visibleSteps.map((ws, i) => {
          const accessible = wizardStepAccessible(ws);
          const wsStatus = wizardStepStatus(ws);
          const summary = wizardStepSummary(ws);

          return (
            <div key={ws.id} className="flex items-center gap-1">
              <StepChip
                wizardStep={ws}
                currentIdx={safeActiveIdx}
                accessible={accessible}
                status={wsStatus}
                summary={summary}
                onClick={() => {
                  if (accessible) advanceTo(ws.idx as WizardStepIdx);
                }}
              />
              {i < visibleSteps.length - 1 && (
                <div className="h-px w-4 shrink-0 bg-line mt-4" />
              )}
            </div>
          );
        })}
      </div>

      {/* Step content — Phase WWF-G renders by stable step id so WWF-only
          and PT-only flows can share the same switch. */}
      <div className="mt-6">
        {activeStepId === "import" && (
          <StepImport
            projectId={projectId}
            accessToken={accessToken}
            step={activeBackendStep}
            latestUpload={latestUpload}
            methodologies={status.methodologies_enabled}
            onUploaded={refresh}
            onNext={advanceNext}
          />
        )}
        {activeStepId === "methodology" && (
          <StepMethodology
            step={activeBackendStep}
            methodologies={status.methodologies_enabled}
            onNext={advanceNext}
          />
        )}
        {activeStepId === "ai_class" && ptWwfMode && (
          <StepAIClassificationDual
            latestUpload={latestUpload}
            ptCounts={ptClassificationCounts}
            wwfCounts={wwfClassificationCounts}
            ptCurrentJob={currentJob}
            wwfCurrentJob={currentJobWwf}
            busyPt={busyPt}
            busyWwf={busyWwf}
            ptError={actionError ?? jobError}
            wwfError={actionErrorWwf ?? jobErrorWwf}
            onRunPT={handleClassifyAI}
            onRunWWF={handleClassifyWwf}
            onRunBoth={handleClassifyBoth}
            onResumePT={handleResumeClassify}
            onResumeWWF={handleResumeClassifyWwf}
            onRetryFailedPT={handleRetryFailed}
            onRetryFailedWWF={handleRetryFailedWwf}
            onOpenValidation={handleOpenValidation}
            onNext={advanceNext}
          />
        )}
        {activeStepId === "ai_class" && !ptWwfMode && (
          <StepAIClassification
            step={activeBackendStep}
            latestUpload={latestUpload}
            methodologies={status.methodologies_enabled}
            primaryMethodology={primaryMethodology}
            lastClassifyResult={lastClassifyResult}
            currentJob={currentJob}
            jobError={jobError}
            // Phase WWF-Q — single-methodology mode forwards the
            // methodology-specific busy flag (the dual-card mode is
            // handled by the StepAIClassificationDual branch above).
            busy={busyPt}
            error={actionError}
            onRun={handleClassifyAI}
            onResume={handleResumeClassify}
            onRetryFailed={handleRetryFailed}
            onNext={advanceNext}
          />
        )}
        {activeStepId === "validation" && (
          <StepValidation
            projectId={projectId}
            accessToken={accessToken}
            step={activeBackendStep}
            methodology={primaryMethodology}
            wwfEnabled={wwfEnabled}
            ptEnabled={ptEnabled}
            wwfOnly={wwfOnly}
            onResolved={refresh}
            onNext={advanceNext}
          />
        )}
        {activeStepId === "nevo" && (
          <StepNEVO
            step={activeBackendStep}
            lastNevoResult={lastNevoResult}
            busy={busy}
            error={actionError}
            onRun={handleApplyNEVO}
            onNext={advanceNext}
          />
        )}
        {activeStepId === "nutrition_val" && (
          <div className="space-y-5">
            <div>
              <h2 className="text-xl font-semibold">
                Validation nutritionnelle
              </h2>
              <p className="mt-1 text-sm text-ink-muted">
                Inspectez les valeurs protéiques attribuées par NEVO et
                complétez manuellement les produits restants.
              </p>
              <p className="mt-1 text-xs text-ink-soft">
                {"L'IA ne génère jamais de valeurs protéiques. Les "}
                valeurs proviennent du CSV retailer, de NEVO, ou de la
                saisie manuelle.
              </p>
            </div>
            <NutritionTable
              projectId={projectId}
              accessToken={accessToken}
              onChanged={refresh}
            />
            <Card>
              <CountRow counts={activeBackendStep.counts} />
              <div className="mt-3 flex flex-wrap gap-3">
                <Button onClick={advanceNext}>Continuer vers Calcul</Button>
              </div>
            </Card>
          </div>
        )}
        {activeStepId === "calculation" && (
          <StepCalculation
            step={activeBackendStep}
            preflight={preflight}
            busy={busy}
            error={actionError}
            wwfOnly={wwfOnly}
            onRun={handleCreateRun}
            onRunPartial={handleCreateRunPartial}
            onGoToStep={advanceTo}
            goToStepById={goToStepById}
          />
        )}
        {activeStepId === "report" && (
          <StepReport
            projectId={projectId}
            accessToken={accessToken}
            step={activeBackendStep}
            latestRun={latestRun}
            isAltera={isAltera}
          />
        )}
      </div>

      {/* Prev / Next navigation */}
      <div className="mt-8 flex items-center justify-between border-t border-gray-100 pt-4">
        <Button
          variant="secondary"
          onClick={() =>
            advanceTo(Math.max(0, safeActiveIdx - 1) as WizardStepIdx)
          }
          disabled={safeActiveIdx === 0}
        >
          ← Précédent
        </Button>
        <span className="text-xs text-ink-soft">
          {safeActiveIdx + 1} / {visibleSteps.length}
        </span>
        <Button
          variant="secondary"
          onClick={() =>
            advanceTo(
              Math.min(maxIdx, safeActiveIdx + 1) as WizardStepIdx,
            )
          }
          disabled={safeActiveIdx === maxIdx}
        >
          Suivant →
        </Button>
      </div>

      {/* Phase WWF-G — privacy footer adapts to methodology context. */}
      <p className="mt-6 text-xs text-ink-soft">
        {wwfOnly
          ? "Note : la catégorisation WWF utilise uniquement les descripteurs non commerciaux (nom, marque, catégorie retailer, ingrédients). Les volumes, ventes et prix ne sont jamais envoyés à l'IA."
          : "Note : l'IA peut aider à sélectionner certaines références, mais ne génère pas de valeurs nutritionnelles. Les protéines proviennent uniquement des données fournies par le retailer, de NEVO, de CIQUAL ou de la validation manuelle."}
      </p>
    </div>
  );
}
