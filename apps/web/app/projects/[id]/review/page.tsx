"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  createApi,
  PT_GROUP_OPTIONS,
  WWF_FOOD_GROUP_OPTIONS,
  type DecisionType,
  type ManualReviewReason,
  type ManualReviewStatus,
  type Methodology,
  type ReviewFilters,
  type ReviewItem,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { Button, Card, CardHeader, EmptyState, Pill } from "@/components/ui";

const REASON_OPTIONS: ManualReviewReason[] = [
  "low_confidence",
  "ai_parse_failed",
  "ai_provider_error",
  "rule_collision",
  "contradiction_detected",
  "requested",
];

const STATUS_OPTIONS: ManualReviewStatus[] = [
  "in_queue",
  "reviewing",
  "accepted",
  "changed",
  "deferred",
];

const METHODOLOGY_OPTIONS: Methodology[] = ["protein_tracker", "wwf"];

export default function ReviewPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const projectId = params.id;
  const { accessToken, isAltera } = useAuth();
  const api = useMemo(() => createApi(accessToken), [accessToken]);

  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Filters — only used by Altera staff
  const [filterMethodology, setFilterMethodology] = useState<Methodology | "">("");
  const [filterStatus, setFilterStatus] = useState<ManualReviewStatus | "">("");
  const [filterReason, setFilterReason] = useState<ManualReviewReason | "">("");
  const [filterSearch, setFilterSearch] = useState("");
  const [sortOrder, setSortOrder] = useState<"oldest" | "newest">("oldest");

  const refresh = useCallback(async () => {
    const filters: ReviewFilters = {};
    if (filterMethodology) filters.methodology = filterMethodology;
    if (filterStatus) filters.status = filterStatus;
    if (filterReason) filters.reason = filterReason;
    if (filterSearch.trim()) filters.product_search = filterSearch.trim();
    if (sortOrder !== "oldest") filters.sort = sortOrder;
    try {
      setItems(await api.listReview(projectId, filters));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    }
  }, [api, projectId, filterMethodology, filterStatus, filterReason, filterSearch, sortOrder]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <div className="mx-auto max-w-4xl">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Manual review</h1>
        <Button variant="ghost" onClick={() => router.push(`/projects/${projectId}`)}>
          ← Back to project
        </Button>
      </div>
      <p className="mt-1 text-sm text-gray-600">
        {isAltera
          ? "Products the rules engine could not classify confidently. Reviewers see only non-commercial fields."
          : "These products are being reviewed by the Altera methodology team. No action is required from you."}
      </p>

      {error && (
        <div className="mt-4 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {error}
        </div>
      )}

      {/* Filters — Altera staff only */}
      {isAltera && (
        <div className="mt-4 flex flex-wrap items-end gap-3 rounded-md border border-gray-200 bg-gray-50 p-3">
          <label className="text-sm">
            <div className="text-xs font-medium text-gray-600">Methodology</div>
            <select
              value={filterMethodology}
              onChange={(e) => setFilterMethodology(e.target.value as Methodology | "")}
              className="mt-1 rounded border border-gray-300 px-2 py-1 text-sm"
            >
              <option value="">All</option>
              {METHODOLOGY_OPTIONS.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </label>

          <label className="text-sm">
            <div className="text-xs font-medium text-gray-600">Status</div>
            <select
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value as ManualReviewStatus | "")}
              className="mt-1 rounded border border-gray-300 px-2 py-1 text-sm"
            >
              <option value="">All</option>
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </label>

          <label className="text-sm">
            <div className="text-xs font-medium text-gray-600">Reason</div>
            <select
              value={filterReason}
              onChange={(e) => setFilterReason(e.target.value as ManualReviewReason | "")}
              className="mt-1 rounded border border-gray-300 px-2 py-1 text-sm"
            >
              <option value="">All</option>
              {REASON_OPTIONS.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </label>

          <label className="grow text-sm">
            <div className="text-xs font-medium text-gray-600">Search</div>
            <input
              type="text"
              value={filterSearch}
              onChange={(e) => setFilterSearch(e.target.value)}
              placeholder="Product name or ID…"
              className="mt-1 w-full rounded border border-gray-300 px-2 py-1 text-sm"
            />
          </label>

          <label className="text-sm">
            <div className="text-xs font-medium text-gray-600">Sort</div>
            <select
              value={sortOrder}
              onChange={(e) => setSortOrder(e.target.value as "oldest" | "newest")}
              className="mt-1 rounded border border-gray-300 px-2 py-1 text-sm"
            >
              <option value="oldest">Oldest first</option>
              <option value="newest">Newest first</option>
            </select>
          </label>
        </div>
      )}

      {items === null ? (
        <div className="mt-6 text-sm text-gray-500">Loading…</div>
      ) : items.length === 0 ? (
        <div className="mt-6">
          <EmptyState
            title="No items"
            description={
              filterMethodology || filterStatus || filterReason || filterSearch
                ? "No items match the current filters."
                : "Everything in this project is currently classified."
            }
            action={
              filterMethodology || filterStatus || filterReason || filterSearch ? (
                <Button
                  variant="secondary"
                  onClick={() => {
                    setFilterMethodology("");
                    setFilterStatus("");
                    setFilterReason("");
                    setFilterSearch("");
                  }}
                >
                  Clear filters
                </Button>
              ) : (
                <Button onClick={() => router.push(`/projects/${projectId}/runs`)}>
                  Run calculation →
                </Button>
              )
            }
          />
        </div>
      ) : isAltera ? (
        <div className="mt-6 space-y-3">
          {items.map((item) => (
            <ReviewRow
              key={`${item.product_id}-${item.methodology}`}
              item={item}
              projectId={projectId}
              accessToken={accessToken}
              onAfter={refresh}
            />
          ))}
        </div>
      ) : (
        <div className="mt-6 space-y-3">
          {items.map((item) => (
            <ClientReviewRow key={`${item.product_id}-${item.methodology}`} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}

function ReviewRow({
  item,
  projectId,
  accessToken,
  onAfter,
}: {
  item: ReviewItem;
  projectId: string;
  accessToken: string | null;
  onAfter: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [choice, setChoice] = useState<string>(() => defaultChoice(item.methodology));
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);

  async function submit(decision: DecisionType) {
    setBusy(true);
    setErr(null);
    try {
      const api = createApi(accessToken);
      await api.submitDecision(projectId, item.product_id, item.methodology, {
        decision,
        to_category: decision === "changed" ? choice : undefined,
        reason: reason || undefined,
      });
      onAfter();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Decision failed");
    } finally {
      setBusy(false);
    }
  }

  const options =
    item.methodology === "protein_tracker"
      ? PT_GROUP_OPTIONS
      : WWF_FOOD_GROUP_OPTIONS;

  return (
    <Card>
      <CardHeader
        title={item.product_name}
        subtitle={`${item.external_product_id}${item.brand ? " · " + item.brand : ""}`}
        action={<Pill tone="warn">{item.reason}</Pill>}
      />
      <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
        <Pill tone="brand">{item.methodology}</Pill>
        <Pill tone={item.status === "in_queue" ? "neutral" : "ok"}>{item.status}</Pill>
        <span className="text-xs text-gray-500">
          current: {item.current_category ?? "—"}
        </span>
        {item.confidence !== null && (
          <span className="text-xs text-gray-400">
            confidence: {(item.confidence * 100).toFixed(0)}%
          </span>
        )}
      </div>
      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className="text-sm">
          <div className="text-xs font-medium text-gray-700">Change to</div>
          <select
            value={choice}
            onChange={(e) => setChoice(e.target.value)}
            className="mt-1 rounded-md border border-gray-300 px-2 py-1 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          >
            {options.map((o) => (
              <option key={o} value={o}>{o}</option>
            ))}
          </select>
        </label>
        <label className="grow text-sm">
          <div className="text-xs font-medium text-gray-700">Reason (optional)</div>
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          />
        </label>
      </div>
      <div className="mt-4 flex gap-2">
        <Button onClick={() => submit("changed")} disabled={busy}>
          {busy ? "Saving…" : "Change to selected"}
        </Button>
        <Button variant="secondary" onClick={() => submit("accepted")} disabled={busy}>
          Accept current
        </Button>
        <Button variant="ghost" onClick={() => submit("deferred")} disabled={busy}>
          Defer
        </Button>
      </div>
      {err && (
        <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
          {err}
        </div>
      )}
    </Card>
  );
}

function ClientReviewRow({ item }: { item: ReviewItem }) {
  return (
    <Card>
      <CardHeader
        title={item.product_name}
        subtitle={`${item.external_product_id}${item.brand ? " · " + item.brand : ""}`}
        action={<Pill tone="warn">{item.reason}</Pill>}
      />
      <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
        <Pill tone="brand">{item.methodology}</Pill>
        <span className="text-xs text-gray-500">
          current: {item.current_category ?? "—"}
        </span>
        <span className="text-xs text-gray-400 italic">In review by Altera</span>
      </div>
    </Card>
  );
}

function defaultChoice(methodology: Methodology): string {
  return methodology === "protein_tracker" ? "plant_based_core" : "FG1";
}
