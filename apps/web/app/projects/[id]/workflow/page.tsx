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
  type ClassifySummary,
  type Methodology,
  type Run,
  type UploadResult,
  type WorkflowStatus,
  type WorkflowStep,
} from "@/lib/api";

// Phase 34E — fully inline upload + manual review inside the wizard.
import { InlineUpload } from "./_inline-upload";
import { InlineReview } from "./_inline-review";
// Phase 34F — inline category validation table.
import { ValidationTable } from "./_validation-table";

// ---------------------------------------------------------------------------
// Wizard step definitions — 9 visible steps mapped to backend step keys
// ---------------------------------------------------------------------------

// Phase 34I — 8-step flow. Deterministic classification has been
// removed from the user-facing workflow: AI is the primary classifier
// now. The deterministic rule engine code remains for tests and
// admin/debug; the normal wizard never calls it.
const WIZARD_STEPS = [
  { idx: 0, id: "import",           label: "Import",            backendKey: "upload" },
  { idx: 1, id: "methodology",      label: "Méthodologie",      backendKey: "methodology" },
  { idx: 2, id: "ai_class",         label: "Classification IA", backendKey: "ai_classification" },
  { idx: 3, id: "validation",       label: "Validation",        backendKey: "manual_classification_review" },
  { idx: 4, id: "nevo",             label: "NEVO",              backendKey: "nutrition_enrichment_nevo" },
  { idx: 5, id: "ciqual",           label: "CIQUAL + IA",       backendKey: "nutrition_enrichment_ciqual" },
  { idx: 6, id: "calculation",      label: "Calcul",            backendKey: "calculation" },
  { idx: 7, id: "report",           label: "Résultat",          backendKey: "report" },
] as const;

type WizardStepIdx = 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7;

function backendKeyToWizardIdx(key: string): WizardStepIdx {
  const found = WIZARD_STEPS.find((s) => s.backendKey === key);
  return (found?.idx ?? 0) as WizardStepIdx;
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
  wizardStep: (typeof WIZARD_STEPS)[number];
  currentIdx: WizardStepIdx;
  accessible: boolean;
  status: string;
  summary: string | null;
  onClick: () => void;
}) {
  const isActive = wizardStep.idx === currentIdx;
  const isComplete = status === "complete" || status === "not_needed";
  const isBlocked = status === "blocked";

  let circleClass = "flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold ";
  if (isActive) {
    circleClass += "bg-brand-600 text-white ring-2 ring-brand-300";
  } else if (isComplete) {
    circleClass += "bg-emerald-500 text-white";
  } else if (isBlocked) {
    circleClass += "bg-rose-100 text-rose-700";
  } else if (accessible) {
    circleClass += "bg-gray-100 text-gray-600";
  } else {
    circleClass += "bg-gray-50 text-gray-400";
  }

  const inner = isComplete && !isActive ? "✓" : String(wizardStep.idx + 1);

  const labelClass = `mt-1 text-center text-[11px] leading-tight ${
    isActive ? "font-semibold text-brand-700" : accessible ? "text-gray-600" : "text-gray-400"
  }`;

  const container = (
    <div className="flex flex-col items-center gap-0.5 min-w-[52px]">
      <div className={circleClass}>{inner}</div>
      <span className={labelClass}>{wizardStep.label}</span>
      {summary && isComplete && !isActive && (
        <span className="text-[10px] text-gray-400 text-center leading-tight max-w-[60px] truncate">
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
        <li key={r.code} className="flex items-start gap-2 text-sm text-rose-700">
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
    <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5">
      {entries.map(([k, v]) => (
        <div key={k} className="flex flex-col">
          <span className="text-[11px] font-medium uppercase tracking-wide text-gray-400">
            {COUNT_LABELS[k] ?? k.replace(/_/g, " ")}
          </span>
          <span className="text-lg font-semibold text-gray-900">{v}</span>
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
        <p className="mt-1 text-sm text-gray-600">
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
            <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
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
    wwf: "Calcule la répartition des achats alimentaires selon les groupes PHD du WWF (FG1–FG7). Requiert le poids unitaire et le volume des ventes.",
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-semibold">Méthodologie</h2>
        <p className="mt-1 text-sm text-gray-600">
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
                  <p className="mt-0.5 text-sm text-gray-500">{METHOD_DESC[m] ?? ""}</p>
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
          <p className="text-sm text-gray-600">
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

function StepAIClassification({
  step,
  latestUpload,
  methodologies,
  lastClassifyResult,
  busy,
  error,
  onRun,
  onNext,
}: {
  step: WorkflowStep;
  latestUpload: UploadResult | null;
  methodologies: string[];
  lastClassifyResult: ClassifySummary | null;
  busy: boolean;
  error: string | null;
  onRun: () => void;
  onNext: () => void;
}) {
  const isComplete = step.status === "complete";
  const isNotNeeded = step.status === "not_needed";
  const ptEnabled = methodologies.includes("protein_tracker");

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
        <h2 className="text-xl font-semibold">Classification IA</h2>
        <p className="mt-1 text-sm text-gray-600">
          {"L'IA aide à catégoriser les produits restants à partir de champs non commerciaux."}
        </p>
        <p className="mt-1 text-xs text-gray-500">
          {"Les champs commerciaux comme volumes, ventes, prix et marges ne sont pas envoyés à l'IA."}
        </p>
      </div>

      <Card>
        {aiBanner && (
          <div className="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
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
                  ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                  : "border-rose-200 bg-rose-50 text-rose-800")
              }
            >
              IA exécutée sur {lastClassifyResult.ai_attempted} produit(s) en{" "}
              {lastClassifyResult.ai_batch_count} batch(s) ·{" "}
              {lastClassifyResult.ai_accepted} classifié(s),{" "}
              {lastClassifyResult.ai_review} en validation manuelle,{" "}
              {lastClassifyResult.ai_failed} en échec.
            </div>
            {/* Phase 34F — finer breakdown when something failed. */}
            {(lastClassifyResult.ai_parse_failures > 0 ||
              lastClassifyResult.ai_unsupported_category_failures > 0 ||
              lastClassifyResult.ai_provider_errors > 0) && (
              <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
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
                    <summary className="cursor-pointer text-amber-700 hover:underline">
                      Voir un échantillon des erreurs
                    </summary>
                    <ul className="mt-1 list-disc pl-4 text-amber-700">
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
          <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
            Aucune classification IA nécessaire — tous les produits ont été classifiés
            déterministement.
          </div>
        ) : (
          <>
            <CountRow counts={step.counts} />
            <BlockerList step={step} />
          </>
        )}
        {error && (
          <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
            {error}
          </div>
        )}
        <div className="mt-4 flex flex-wrap gap-3">
          {isComplete || isNotNeeded ? (
            <Button onClick={onNext}>Continuer vers Validation</Button>
          ) : (
            <Button onClick={onRun} disabled={busy || !latestUpload || !ptEnabled}>
              {busy ? "Classification IA en cours…" : "Lancer la classification IA"}
            </Button>
          )}
        </div>
        {!latestUpload && (
          <p className="mt-2 text-xs text-gray-500">
            {"Importez d'abord un fichier à l'étape 1."}
          </p>
        )}
      </Card>
    </div>
  );
}

function StepValidation({
  projectId,
  accessToken,
  step,
  methodology,
  wwfEnabled,
  onResolved,
  onNext,
}: {
  projectId: string;
  accessToken: string | null;
  step: WorkflowStep;
  methodology: Methodology;
  wwfEnabled: boolean;
  onResolved: () => void | Promise<void>;
  onNext: () => void;
}) {
  const isNotNeeded = step.status === "not_needed";
  const pending = step.counts.pending ?? 0;

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-semibold">Validation des catégories</h2>
        <p className="mt-1 text-sm text-gray-600">
          {"Tableau de validation : voir et corriger les catégories assignées par les règles déterministes et par l'IA."}
        </p>
        <p className="mt-1 text-xs text-gray-500">
          {"Seuls les champs non commerciaux sont affichés. Volumes, ventes, prix et marges ne sont jamais utilisés pour la classification ni envoyés à l'IA."}
        </p>
      </div>

      {/* Phase 34F — full category validation table for ALL products. */}
      <ValidationTable
        projectId={projectId}
        accessToken={accessToken}
        wwfEnabled={wwfEnabled}
        onChanged={onResolved}
      />

      {/* Pending-only one-click review list, shown only while there
          are unresolved manual-review items. The table above already
          lets users override anything; this is the fast-path for the
          subset that explicitly needs a human decision. */}
      {!isNotNeeded && pending > 0 && (
        <InlineReview
          projectId={projectId}
          accessToken={accessToken}
          methodology={methodology}
          onResolved={onResolved}
        />
      )}

      <Card>
        {isNotNeeded || pending === 0 ? (
          <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
            Aucun produit en attente de validation manuelle.
          </div>
        ) : (
          <>
            <CountRow counts={step.counts} />
            <BlockerList step={step} />
          </>
        )}
        <div className="mt-3 flex flex-wrap gap-3">
          <Button
            variant={isNotNeeded || pending === 0 ? "primary" : "secondary"}
            onClick={onNext}
          >
            Continuer vers NEVO
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
        <p className="mt-1 text-sm text-gray-600">
          NEVO est utilisé en priorité car il peut fournir les protéines totales, végétales et
          animales lorsque disponibles.
        </p>
        <p className="mt-1 text-xs text-gray-500">
          {"L'IA peut aider à sélectionner une référence NEVO, mais les valeurs nutritionnelles viennent de NEVO, pas de l'IA."}
        </p>
      </div>

      <Card>
        {/* Phase 34D — hard warning when NEVO table is empty / zero matched. */}
        {lastNevoResult?.warning && (
          <div className="mb-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
            <p className="font-medium">Aucun produit n’a été enrichi par NEVO.</p>
            <p className="mt-1 text-xs">{lastNevoResult.warning}</p>
          </div>
        )}
        {lastNevoResult && (
          <div className="mb-3 text-xs text-gray-500">
            Table NEVO : {lastNevoResult.nevo_total_references} référence(s) chargée(s).
          </div>
        )}
        {isNotNeeded ? (
          <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
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
                <p className="text-xs font-semibold uppercase tracking-wide text-emerald-700">
                  {matchedProducts.length} produit(s) enrichi(s)
                </p>
                <ul className="mt-1.5 space-y-1">
                  {matchedProducts.map((r) => (
                    <li key={r.product_id} className="flex items-center justify-between text-xs text-gray-700">
                      <span>{r.product_name}</span>
                      <span className="text-gray-400 truncate max-w-[200px]">
                        → {r.reference_name ?? "NEVO"}{r.has_split ? " (split ✓)" : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {noMatchProducts.length > 0 && (
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-amber-700">
                  {noMatchProducts.length} produit(s) sans correspondance NEVO
                </p>
                <ul className="mt-1.5 space-y-1">
                  {noMatchProducts.map((r) => (
                    <li key={r.product_id} className="text-xs text-gray-500">
                      {r.product_name} — aucune référence NEVO trouvée
                    </li>
                  ))}
                </ul>
                <p className="mt-1.5 text-xs text-gray-400">
                  Ces produits seront tentés avec CIQUAL à {"l'étape"} suivante.
                </p>
              </div>
            )}
          </div>
        )}

        {error && (
          <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
            {error}
          </div>
        )}
        <div className="mt-4 flex flex-wrap gap-3">
          {isComplete || isNotNeeded ? (
            <Button onClick={onNext}>Continuer vers CIQUAL</Button>
          ) : (
            <Button onClick={onRun} disabled={busy}>
              {busy ? "Enrichissement NEVO en cours…" : "Enrichir avec NEVO"}
            </Button>
          )}
          {isComplete && (
            <Button variant="secondary" onClick={onRun} disabled={busy}>
              {busy ? "…" : "Ré-enrichir"}
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
        <p className="mt-1 text-sm text-gray-600">
          {"Uniquement pour les produits encore sans donnée protéique après NEVO. CIQUAL fournit une protéine totale. Comme CIQUAL ne fournit pas de split végétal/animal, l'IA peut aider à sélectionner une référence — qui doit être tracée."}
        </p>
      </div>

      <Card>
        {isNotNeeded ? (
          <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
            {"Tous les produits disposent d'une donnée protéique exploitable après NEVO — CIQUAL non requis."}
          </div>
        ) : isLocked ? (
          <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-600">
            {"Complétez d'abord l'étape NEVO avant d'utiliser CIQUAL."}
          </div>
        ) : (
          <>
            <CountRow counts={step.counts} />
            <BlockerList step={step} />
          </>
        )}
        {error && (
          <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
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
  busy,
  error,
  onRun,
  onRunPartial,
  onGoToStep,
}: {
  step: WorkflowStep;
  busy: boolean;
  error: string | null;
  onRun: () => void;
  onRunPartial: () => void;
  onGoToStep: (idx: WizardStepIdx) => void;
}) {
  const isReady = step.status === "ready";
  const isBlocked = step.status === "blocked";
  // Phase 34K — partial calculation. When the ONLY remaining blocker
  // is `nutrition_required`, the user can choose to run the
  // calculation on the products that already have usable nutrition.
  // The result page then discloses coverage prominently.
  const nutritionOnlyBlocker =
    isBlocked &&
    step.blocking_reasons.length > 0 &&
    step.blocking_reasons.every((r) => r.code === "nutrition_required");
  const missingNutritionCount =
    step.blocking_reasons.find((r) => r.code === "nutrition_required")?.count ??
    0;

  // Phase 34I — indexes shifted by 1 after removing the deterministic
  // step. Classification now lives at idx 2 (was 3 before AI was
  // primary); review at idx 3 (was 4); NEVO at idx 4 (was 5).
  const BLOCKER_STEP: Record<string, WizardStepIdx> = {
    no_eligible_products: 0,
    classification_required: 2,
    review_pending: 3,
    nutrition_required: 4,
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-semibold">Calcul</h2>
        <p className="mt-1 text-sm text-gray-600">
          Lance le calcul du ratio protéines végétales / totales pour tous les produits éligibles.
          Le calcul est bloqué tant que des pré-requis sont manquants.
        </p>
      </div>

      <Card>
        <h3 className="text-sm font-medium text-gray-700">Conditions requises</h3>
        <ul className="mt-2 space-y-1.5">
          {[
            { label: "Fichier importé", ok: (step.counts.eligible_rows ?? 0) > 0 || isReady },
            { label: "Classification terminée", ok: isReady || (!isBlocked) },
            { label: "Validation manuelle complète", ok: isReady },
            { label: "Données nutritionnelles disponibles", ok: isReady },
          ].map((c) => (
            <li key={c.label} className="flex items-center gap-2 text-sm">
              <span className={c.ok ? "text-emerald-600" : "text-rose-500"}>
                {c.ok ? "✓" : "✗"}
              </span>
              <span className={c.ok ? "text-gray-700" : "text-gray-500"}>{c.label}</span>
            </li>
          ))}
        </ul>

        {isBlocked && (() => {
          // Phase 34D — split blockers into two semantic panels so the
          // user understands whether they need to fix categorisation
          // or nutrition. Both groups can be present simultaneously.
          const CLASSIF_CODES = new Set([
            "classification_required",
            "review_pending",
            "no_eligible_products",
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
                <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2">
                  <p className="text-sm font-medium text-rose-700">
                    Catégorisation incomplète
                  </p>
                  <p className="mt-0.5 text-xs text-rose-600">
                    {"Certains produits n'ont pas encore de catégorie Protein Tracker validée."}
                  </p>
                  <ul className="mt-2 space-y-1.5">
                    {classifBlockers.map((r) => {
                      const targetIdx = BLOCKER_STEP[r.code];
                      return (
                        <li
                          key={r.code}
                          className="flex items-start justify-between gap-3"
                        >
                          <span className="text-xs text-rose-700">
                            ▸ {r.label}
                            {r.count > 0 ? ` (${r.count})` : ""}
                          </span>
                          {targetIdx !== undefined && (
                            <button
                              type="button"
                              onClick={() => onGoToStep(targetIdx)}
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
                <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2">
                  <p className="text-sm font-medium text-amber-800">
                    Données protéiques manquantes
                  </p>
                  <p className="mt-0.5 text-xs text-amber-700">
                    {"Certains produits sont catégorisés, mais n'ont pas encore de protéine exploitable."}
                  </p>
                  <ul className="mt-2 space-y-1.5">
                    {nutritionBlockers.map((r) => {
                      const targetIdx = BLOCKER_STEP[r.code];
                      return (
                        <li
                          key={r.code}
                          className="flex items-start justify-between gap-3"
                        >
                          <span className="text-xs text-amber-800">
                            ▸ {r.label}
                            {r.count > 0 ? ` (${r.count})` : ""}
                          </span>
                          {targetIdx !== undefined && (
                            <button
                              type="button"
                              onClick={() => onGoToStep(targetIdx)}
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
          <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
            {error}
          </div>
        )}

        {/* Phase 34K — partial-calc CTA: only when the sole blocker is
            nutrition data. Includes a warning so the user knows what
            they're agreeing to. */}
        {nutritionOnlyBlocker && (
          <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            <p className="font-medium">Données nutritionnelles incomplètes</p>
            <p className="mt-1 text-xs">
              {missingNutritionCount} produit(s) sans donnée protéique
              exploitable. Vous pouvez lancer le calcul sur les produits
              restants, mais le rapport indiquera explicitement le
              pourcentage de produits couverts.
            </p>
          </div>
        )}

        <div className="mt-4 flex flex-wrap gap-3">
          <Button onClick={onRun} disabled={!isReady || busy}>
            {busy ? "Calcul en cours…" : "Lancer le calcul"}
          </Button>
          {nutritionOnlyBlocker && (
            <Button variant="secondary" onClick={onRunPartial} disabled={busy}>
              {busy ? "…" : "Calculer sur les données disponibles"}
            </Button>
          )}
        </div>
      </Card>
    </div>
  );
}

function StepReport({
  projectId,
  step,
  latestRun,
}: {
  projectId: string;
  step: WorkflowStep;
  latestRun: Run | null;
}) {
  const hasRun = step.status === "complete" && latestRun !== null;

  const summary = latestRun?.summary as Record<string, unknown> | undefined;
  const plantRatio = summary?.plant_protein_ratio as number | undefined;
  const plantPct = plantRatio != null ? `${(plantRatio * 100).toFixed(1)} %` : null;
  // Phase 34D — surface plant_kg / animal_kg / counts inline so the user
  // does not need to leave the wizard for the headline numbers.
  const totalKg = summary?.total_protein_kg as number | undefined;
  const plantKg = summary?.plant_protein_kg as number | undefined;
  const animalKg = summary?.animal_protein_kg as number | undefined;
  const rowsIncluded = summary?.rows_included as number | undefined;
  const rowsExcluded = summary?.rows_excluded as number | undefined;
  const fmtKg = (n: number | undefined) =>
    n == null ? "—" : `${n.toLocaleString("fr-FR", { maximumFractionDigits: 1 })} kg`;
  const animalPct =
    plantRatio != null ? `${((1 - plantRatio) * 100).toFixed(1)} %` : null;

  // Phase 34K — coverage block. The backend's create_run decorates
  // the summary with a `coverage` object when the run was Protein
  // Tracker. Older runs predate this and may not have it.
  const coverage = summary?.coverage as
    | {
        total_products_start?: number;
        eligible_products_total?: number;
        products_included_in_calculation?: number;
        products_excluded_missing_nutrition?: number;
        product_coverage_pct?: number;
        volume_total_start?: string;
        volume_included_in_calculation?: string;
        volume_coverage_pct?: number;
        is_partial?: boolean;
      }
    | undefined;
  const productPct = coverage?.product_coverage_pct;
  const volumePct = coverage?.volume_coverage_pct;
  // Severity threshold: <50% high, 50–80% medium, >=80% normal.
  const coverageTone: "normal" | "medium" | "high" =
    productPct == null
      ? "normal"
      : productPct < 50
        ? "high"
        : productPct < 80
          ? "medium"
          : "normal";

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-semibold">Résultat / rapport</h2>
        <p className="mt-1 text-sm text-gray-600">
          Dernier calcul réussi et exports disponibles.
        </p>
        <p className="mt-1 text-xs text-gray-500">
          {"L'IA a aidé à sélectionner certaines références, mais n'a pas généré de valeurs"}
          nutritionnelles.
        </p>
      </div>

      {!hasRun ? (
        <Card>
          <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-600">
            {"Aucun calcul effectué. Revenez à l'étape Calcul pour lancer un premier calcul."}
          </div>
        </Card>
      ) : (
        <Card>
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-gray-700">
                Calcul du {new Date(latestRun.started_at).toLocaleDateString("fr-FR")}
              </p>
              <p className="mt-0.5 text-xs text-gray-500">
                {latestRun.rows_count} ligne(s) traitée(s) ·{" "}
                {latestRun.methodology.replace("_", " ")}
              </p>
            </div>
            {plantPct && (
              <div className="text-right">
                <p className="text-xs text-gray-500">Ratio végétal</p>
                <p className="text-2xl font-bold text-emerald-600">{plantPct}</p>
              </div>
            )}
          </div>

          {/* Phase 34K — coverage disclosure. Always shown when the
              run carries coverage metrics; styled red below 50%,
              amber at 50–80%, neutral above 80%. */}
          {coverage && coverage.is_partial && (
            <div
              className={
                "mt-4 rounded-md border px-3 py-2 text-sm " +
                (coverageTone === "high"
                  ? "border-rose-200 bg-rose-50 text-rose-800"
                  : coverageTone === "medium"
                    ? "border-amber-200 bg-amber-50 text-amber-800"
                    : "border-gray-200 bg-gray-50 text-gray-700")
              }
            >
              <p className="font-medium">
                Calcul partiel — couverture {productPct?.toFixed(1)} %
              </p>
              <p className="mt-1 text-xs">
                Le Protein Ratio a été calculé sur{" "}
                <span className="font-semibold">
                  {coverage.products_included_in_calculation}
                </span>{" "}
                des{" "}
                <span className="font-semibold">
                  {coverage.total_products_start}
                </span>{" "}
                produits initiaux ({productPct?.toFixed(1)} %)
                {volumePct != null && (
                  <>
                    , représentant{" "}
                    <span className="font-semibold">
                      {volumePct.toFixed(1)} %
                    </span>{" "}
                    du volume total
                  </>
                )}
                .{" "}
                {coverage.products_excluded_missing_nutrition} produit(s)
                exclus pour donnée protéique manquante.
              </p>
            </div>
          )}

          {/* Phase 34D — headline numbers inline so the wizard is self-sufficient */}
          <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="rounded-md border border-emerald-100 bg-emerald-50 px-3 py-2">
              <p className="text-xs text-emerald-700">Protéines végétales</p>
              <p className="mt-0.5 text-sm font-semibold text-emerald-800">
                {fmtKg(plantKg)}
              </p>
              {plantPct && (
                <p className="text-xs text-emerald-600">{plantPct}</p>
              )}
            </div>
            <div className="rounded-md border border-amber-100 bg-amber-50 px-3 py-2">
              <p className="text-xs text-amber-700">Protéines animales</p>
              <p className="mt-0.5 text-sm font-semibold text-amber-800">
                {fmtKg(animalKg)}
              </p>
              {animalPct && (
                <p className="text-xs text-amber-600">{animalPct}</p>
              )}
            </div>
            <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2">
              <p className="text-xs text-gray-600">Protéines totales</p>
              <p className="mt-0.5 text-sm font-semibold text-gray-800">
                {fmtKg(totalKg)}
              </p>
            </div>
            <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2">
              <p className="text-xs text-gray-600">Lignes</p>
              <p className="mt-0.5 text-sm font-semibold text-gray-800">
                {rowsIncluded ?? latestRun.rows_count ?? "—"}
                {rowsExcluded != null && rowsExcluded > 0 && (
                  <span className="ml-1 text-xs font-normal text-gray-500">
                    (+{rowsExcluded} exclues)
                  </span>
                )}
              </p>
            </div>
          </div>

          <div className="mt-5 flex flex-wrap gap-3">
            <Link href={`/projects/${projectId}/runs/${latestRun.id}`}>
              <Button variant="ghost">Voir le détail technique →</Button>
            </Link>
          </div>

          <p className="mt-3 text-xs text-gray-400">
            Le détail technique (admin/debug) regroupe les exports CSV/JSON/Markdown
            et l’historique d’approbation.
          </p>
        </Card>
      )}
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
  const { accessToken } = useAuth();
  const api = useMemo(() => createApi(accessToken), [accessToken]);

  const [status, setStatus] = useState<WorkflowStatus | null>(null);
  const [uploads, setUploads] = useState<UploadResult[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Which wizard step is displayed (0-8)
  const [activeIdx, setActiveIdx] = useState<WizardStepIdx | null>(null);

  // Per-step action state
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  // Phase 34C — store last classify and enrichment results for UI feedback
  const [lastClassifyResult, setLastClassifyResult] = useState<ClassifySummary | null>(null);
  const [lastNevoResult, setLastNevoResult] = useState<ApplyReferencesSummary | null>(null);

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
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Échec du chargement");
    }
  }, [api, projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Auto-select the active backend step when first loaded
  useEffect(() => {
    if (!status || activeIdx !== null) return;
    const stepParam = searchParams.get("step");
    if (stepParam) {
      const n = parseInt(stepParam, 10) - 1;
      if (n >= 0 && n <= 7) {
        setActiveIdx(n as WizardStepIdx);
        return;
      }
    }
    const bKey = status.active_step ?? status.current_step;
    setActiveIdx(backendKeyToWizardIdx(bKey));
  }, [status, activeIdx, searchParams]);

  // ----------- actions -----------

  const latestUpload: UploadResult | null = uploads[0] ?? null;
  const latestRun: Run | null = runs[0] ?? null;

  async function runAction(fn: () => Promise<void>) {
    setBusy(true);
    setActionError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      if (e instanceof ApiError && typeof e.detail === "object" && e.detail !== null) {
        const d = e.detail as { message?: string };
        setActionError(d.message ?? String(e));
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
    const next = Math.min(7, activeIdx + 1) as WizardStepIdx;
    advanceTo(next);
  }

  // Phase 34I — AI is the primary classifier. The wizard's Step 3
  // ("Classification IA") calls classify with skip_deterministic=true,
  // which makes the orchestrator route every eligible non-manually-
  // locked product straight to batched AI classification (no
  // deterministic rule keyword traps like "poulet végétal" → animal).
  function handleClassifyAI() {
    if (!latestUpload || !status) return;
    const methodology = status.methodologies_enabled[0] as "protein_tracker" | "wwf";
    void runAction(() =>
      api.classify(projectId, latestUpload.id, methodology, {
        deterministic_only: false,
        skip_deterministic: true,
      })
        .then((result) => { setLastClassifyResult(result); })
    );
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
    const methodology = status.methodologies_enabled[0] as "protein_tracker" | "wwf";
    // Phase 34E — stay in the wizard. The run summary is rendered
    // inline on the last step (idx 7 after Phase 34I); the legacy
    // run-detail page is admin-only.
    void runAction(async () => {
      await api.createRun(projectId, methodology);
      advanceTo(7);
    });
  }

  // Phase 34K — partial calculation: same as handleCreateRun but
  // passes allow_partial=true so the backend lets the run through
  // when products are missing nutrition data.
  function handleCreateRunPartial() {
    if (!status) return;
    const methodology = status.methodologies_enabled[0] as "protein_tracker" | "wwf";
    void runAction(async () => {
      await api.createRun(projectId, methodology, { allow_partial: true });
      advanceTo(7);
    });
  }

  // ----------- render -----------

  if (loadError) {
    return (
      <div className="mx-auto max-w-3xl">
        <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
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
    return <div className="text-sm text-gray-500">Chargement…</div>;
  }

  const currentStep = WIZARD_STEPS[activeIdx];
  const backendStepForActive = backendStep(status, currentStep.backendKey);

  // Compute per-wizard-step accessibility from backend step data
  function wizardStepAccessible(ws: (typeof WIZARD_STEPS)[number]): boolean {
    const bs = backendStep(status!, ws.backendKey);
    return bs?.accessible ?? false;
  }

  function wizardStepStatus(ws: (typeof WIZARD_STEPS)[number]): string {
    return backendStep(status!, ws.backendKey)?.status ?? "locked";
  }

  function wizardStepSummary(ws: (typeof WIZARD_STEPS)[number]): string | null {
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
    <div className="mx-auto max-w-4xl">
      {/* Header */}
      <div className="mb-5 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Parcours guidé</h1>
          <p className="mt-0.5 text-sm text-gray-500">
            Étape {activeIdx + 1} sur 8 · Progression : {status.overall_progress_pct} %
          </p>
        </div>
        <Link
          href={`/projects/${projectId}`}
          className="text-xs text-gray-400 hover:text-gray-600 hover:underline"
        >
          Voir le détail technique (admin)
        </Link>
      </div>

      {/* Progress bar */}
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-gray-100">
        <div
          className="h-full bg-brand-500 transition-all"
          style={{
            width: `${Math.min(100, Math.max(0, status.overall_progress_pct))}%`,
          }}
        />
      </div>

      {/* Horizontal stepper */}
      <div className="mt-5 flex items-start justify-between gap-1 overflow-x-auto pb-2">
        {WIZARD_STEPS.map((ws, i) => {
          const accessible = wizardStepAccessible(ws);
          const wsStatus = wizardStepStatus(ws);
          const summary = wizardStepSummary(ws);

          return (
            <div key={ws.id} className="flex items-center gap-1">
              <StepChip
                wizardStep={ws}
                currentIdx={activeIdx}
                accessible={accessible}
                status={wsStatus}
                summary={summary}
                onClick={() => {
                  if (accessible) advanceTo(ws.idx as WizardStepIdx);
                }}
              />
              {i < WIZARD_STEPS.length - 1 && (
                <div className="h-px w-4 shrink-0 bg-gray-200 mt-3.5" />
              )}
            </div>
          );
        })}
      </div>

      {/* Step content */}
      <div className="mt-6">
        {activeIdx === 0 && (
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
        {activeIdx === 1 && (
          <StepMethodology
            step={activeBackendStep}
            methodologies={status.methodologies_enabled}
            onNext={advanceNext}
          />
        )}
        {activeIdx === 2 && (
          <StepAIClassification
            step={activeBackendStep}
            latestUpload={latestUpload}
            methodologies={status.methodologies_enabled}
            lastClassifyResult={lastClassifyResult}
            busy={busy}
            error={actionError}
            onRun={handleClassifyAI}
            onNext={advanceNext}
          />
        )}
        {activeIdx === 3 && (
          <StepValidation
            projectId={projectId}
            accessToken={accessToken}
            step={activeBackendStep}
            methodology={
              (status.methodologies_enabled[0] as Methodology) ??
              "protein_tracker"
            }
            wwfEnabled={status.methodologies_enabled.includes("wwf")}
            onResolved={refresh}
            onNext={advanceNext}
          />
        )}
        {activeIdx === 4 && (
          <StepNEVO
            step={activeBackendStep}
            lastNevoResult={lastNevoResult}
            busy={busy}
            error={actionError}
            onRun={handleApplyNEVO}
            onNext={advanceNext}
          />
        )}
        {activeIdx === 5 && (
          <StepCIQUAL
            step={activeBackendStep}
            busy={busy}
            error={actionError}
            onRun={handleApplyCIQUAL}
            onNext={advanceNext}
          />
        )}
        {activeIdx === 6 && (
          <StepCalculation
            step={activeBackendStep}
            busy={busy}
            error={actionError}
            onRun={handleCreateRun}
            onRunPartial={handleCreateRunPartial}
            onGoToStep={advanceTo}
          />
        )}
        {activeIdx === 7 && (
          <StepReport
            projectId={projectId}
            step={activeBackendStep}
            latestRun={latestRun}
          />
        )}
      </div>

      {/* Prev / Next navigation */}
      <div className="mt-8 flex items-center justify-between border-t border-gray-100 pt-4">
        <Button
          variant="secondary"
          onClick={() => advanceTo(Math.max(0, activeIdx - 1) as WizardStepIdx)}
          disabled={activeIdx === 0}
        >
          ← Précédent
        </Button>
        <span className="text-xs text-gray-400">
          {activeIdx + 1} / 8
        </span>
        <Button
          variant="secondary"
          onClick={() => advanceTo(Math.min(7, activeIdx + 1) as WizardStepIdx)}
          disabled={activeIdx === 7}
        >
          Suivant →
        </Button>
      </div>

      <p className="mt-6 text-xs text-gray-400">
        {"Note : l'IA peut aider à sélectionner certaines références, mais ne génère pas de valeurs"}
        nutritionnelles. Les protéines proviennent uniquement des données fournies par le retailer,
        de NEVO, de CIQUAL ou de la validation manuelle.
      </p>
    </div>
  );
}
