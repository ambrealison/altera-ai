"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  createApi,
  PT_GROUP_OPTIONS,
  WWF_FOOD_GROUP_OPTIONS,
  type BulkReviewAction,
  type DecisionType,
  type ManualReviewReason,
  type ManualReviewStatus,
  type Methodology,
  type Project,
  type ReviewFilters,
  type ReviewItem,
  type ReviewPriority,
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

const PRIORITY_OPTIONS: ReviewPriority[] = ["low", "medium", "high", "critical"];

const PRIORITY_TONE: Record<ReviewPriority, "neutral" | "warn" | "ok" | "brand"> = {
  low: "neutral",
  medium: "brand",
  high: "warn",
  critical: "warn",
};

export default function ReviewPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const projectId = params.id;
  const { accessToken, isAltera } = useAuth();
  const api = useMemo(() => createApi(accessToken), [accessToken]);

  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Filters — only used by Altera staff
  const [filterMethodology, setFilterMethodology] = useState<Methodology | "">("");
  const [filterStatus, setFilterStatus] = useState<ManualReviewStatus | "">("");
  const [filterReason, setFilterReason] = useState<ManualReviewReason | "">("");
  const [filterPriority, setFilterPriority] = useState<ReviewPriority | "">("");
  const [filterSearch, setFilterSearch] = useState("");
  const [sortOrder, setSortOrder] = useState<"oldest" | "newest" | "priority">("oldest");

  // Bulk selection — only used by Altera staff
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [bulkPtGroup, setBulkPtGroup] = useState<string>(PT_GROUP_OPTIONS[0]);

  const refresh = useCallback(async () => {
    const filters: ReviewFilters = {};
    if (filterMethodology) filters.methodology = filterMethodology;
    if (filterStatus) filters.status = filterStatus;
    if (filterReason) filters.reason = filterReason;
    if (filterPriority) filters.priority_level = filterPriority;
    if (filterSearch.trim()) filters.product_search = filterSearch.trim();
    if (sortOrder !== "oldest") filters.sort = sortOrder;
    try {
      const [reviewData, proj] = await Promise.all([
        api.listReview(projectId, filters),
        api.getProject(projectId),
      ]);
      setItems(reviewData.items);
      setProject(proj);
      setSelected(new Set()); // clear selection on refresh
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    }
  }, [api, projectId, filterMethodology, filterStatus, filterReason, filterPriority, filterSearch, sortOrder]);

  const handleBulkAction = useCallback(
    async (action: BulkReviewAction) => {
      if (selected.size === 0 || !items) return;
      const methodology = filterMethodology || (items[0]?.methodology ?? "protein_tracker");
      setBulkBusy(true);
      setBulkError(null);
      try {
        await api.bulkAction(projectId, {
          action,
          methodology: methodology as Methodology,
          product_ids: Array.from(selected),
          to_pt_group: action === "bulk_change_pt_group" ? bulkPtGroup : undefined,
        });
        await refresh();
      } catch (e) {
        setBulkError(e instanceof Error ? e.message : "Bulk action failed");
      } finally {
        setBulkBusy(false);
      }
    },
    [api, projectId, selected, items, filterMethodology, bulkPtGroup, refresh],
  );

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
      <p className="mt-1 text-sm text-ink-muted">
        {isAltera
          ? "Products the rules engine could not classify confidently. Reviewers see only non-commercial fields."
          : "These products are being reviewed by the Altera methodology team. No action is required from you."}
      </p>

      {error && (
        <div className="mt-4 rounded-md border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-700">
          {error}
        </div>
      )}

      {/* Filters — Altera staff only */}
      {isAltera && (
        <div className="mt-4 flex flex-wrap items-end gap-3 rounded-md border border-gray-200 bg-gray-50 p-3">
          <label className="text-sm">
            <div className="text-xs font-medium text-ink-muted">Methodology</div>
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
            <div className="text-xs font-medium text-ink-muted">Status</div>
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
            <div className="text-xs font-medium text-ink-muted">Reason</div>
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

          <label className="text-sm">
            <div className="text-xs font-medium text-ink-muted">Priority</div>
            <select
              value={filterPriority}
              onChange={(e) => setFilterPriority(e.target.value as ReviewPriority | "")}
              className="mt-1 rounded border border-gray-300 px-2 py-1 text-sm"
            >
              <option value="">All</option>
              {PRIORITY_OPTIONS.map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </label>

          <label className="grow text-sm">
            <div className="text-xs font-medium text-ink-muted">Search</div>
            <input
              type="text"
              value={filterSearch}
              onChange={(e) => setFilterSearch(e.target.value)}
              placeholder="Product name or ID…"
              className="mt-1 w-full rounded border border-gray-300 px-2 py-1 text-sm"
            />
          </label>

          <label className="text-sm">
            <div className="text-xs font-medium text-ink-muted">Sort</div>
            <select
              value={sortOrder}
              onChange={(e) => setSortOrder(e.target.value as "oldest" | "newest" | "priority")}
              className="mt-1 rounded border border-gray-300 px-2 py-1 text-sm"
            >
              <option value="oldest">Oldest first</option>
              <option value="newest">Newest first</option>
              <option value="priority">Priority (critical first)</option>
            </select>
          </label>
        </div>
      )}

      {items === null ? (
        <div className="mt-6 text-sm text-ink-soft">Loading…</div>
      ) : items.length === 0 ? (
        <div className="mt-6">
          <EmptyState
            title="No items"
            description={
              filterMethodology || filterStatus || filterReason || filterPriority || filterSearch
                ? "No items match the current filters."
                : (project?.unclassified_pt_count ?? 0) > 0
                ? `No items awaiting manual review, but ${project!.unclassified_pt_count} product${project!.unclassified_pt_count === 1 ? "" : "s"} have not been classified yet.`
                : "Everything in this project is classified. Ready to run a calculation."
            }
            action={
              filterMethodology || filterStatus || filterReason || filterPriority || filterSearch ? (
                <Button
                  variant="secondary"
                  onClick={() => {
                    setFilterMethodology("");
                    setFilterStatus("");
                    setFilterReason("");
                    setFilterPriority("");
                    setFilterSearch("");
                  }}
                >
                  Clear filters
                </Button>
              ) : (project?.unclassified_pt_count ?? 0) > 0 ? (
                <Button onClick={() => router.push(`/projects/${projectId}/upload`)}>
                  Go to Upload &amp; Classify
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
          {/* Bulk selection toolbar */}
          <div className="flex flex-wrap items-center gap-3 rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm">
            <label className="flex items-center gap-1.5 cursor-pointer text-gray-700">
              <input
                type="checkbox"
                checked={selected.size === items.length && items.length > 0}
                ref={(el) => {
                  if (el) el.indeterminate = selected.size > 0 && selected.size < items.length;
                }}
                onChange={(e) =>
                  setSelected(e.target.checked ? new Set(items.map((i) => i.product_id)) : new Set())
                }
              />
              <span>
                {selected.size > 0 ? `${selected.size} selected` : "Select all"}
              </span>
            </label>

            {selected.size > 0 && (
              <>
                <Button
                  variant="secondary"
                  onClick={() => handleBulkAction("bulk_accept")}
                  disabled={bulkBusy}
                >
                  Accept {selected.size}
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => handleBulkAction("bulk_defer")}
                  disabled={bulkBusy}
                >
                  Defer {selected.size}
                </Button>
                {(!filterMethodology || filterMethodology === "protein_tracker") && (
                  <span className="flex items-center gap-1.5">
                    <select
                      value={bulkPtGroup}
                      onChange={(e) => setBulkPtGroup(e.target.value)}
                      className="rounded border border-gray-300 px-2 py-1 text-sm"
                      disabled={bulkBusy}
                    >
                      {PT_GROUP_OPTIONS.map((g) => (
                        <option key={g} value={g}>{g}</option>
                      ))}
                    </select>
                    <Button
                      variant="secondary"
                      onClick={() => handleBulkAction("bulk_change_pt_group")}
                      disabled={bulkBusy}
                    >
                      Change PT group
                    </Button>
                  </span>
                )}
              </>
            )}
          </div>

          {bulkError && (
            <div className="rounded-md border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-700">
              {bulkError}
            </div>
          )}

          {items.map((item) => (
            <ReviewRow
              key={`${item.product_id}-${item.methodology}`}
              item={item}
              projectId={projectId}
              accessToken={accessToken}
              onAfter={refresh}
              selected={selected.has(item.product_id)}
              onToggleSelect={(id) =>
                setSelected((prev) => {
                  const next = new Set(prev);
                  next.has(id) ? next.delete(id) : next.add(id);
                  return next;
                })
              }
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
  selected = false,
  onToggleSelect,
}: {
  item: ReviewItem;
  projectId: string;
  accessToken: string | null;
  onAfter: () => void;
  selected?: boolean;
  onToggleSelect?: (id: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [choice, setChoice] = useState<string>(() => defaultChoice(item.methodology));
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const lockedByOther = item.lock_status === "locked_by_other";
  const lockedByMe = item.lock_status === "locked_by_me";

  async function submit(decision: DecisionType) {
    if (lockedByOther) return;
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

  async function handleClaim() {
    setBusy(true);
    setErr(null);
    try {
      await createApi(accessToken).claimItem(projectId, item.product_id, item.methodology);
      onAfter();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Claim failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleRelease() {
    setBusy(true);
    setErr(null);
    try {
      await createApi(accessToken).releaseItem(projectId, item.product_id, item.methodology);
      onAfter();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Release failed");
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
      <div className="flex items-start gap-3">
        {onToggleSelect && (
          <input
            type="checkbox"
            checked={selected}
            onChange={() => onToggleSelect(item.product_id)}
            className="mt-1 shrink-0"
          />
        )}
        <div className="min-w-0 flex-1">
          <CardHeader
            title={item.product_name}
            subtitle={`${item.external_product_id}${item.brand ? " · " + item.brand : ""}`}
            action={<Pill tone="warn">{item.reason}</Pill>}
          />
          <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
            <Pill tone="brand">{item.methodology}</Pill>
            <Pill tone={item.status === "in_queue" ? "neutral" : "ok"}>{item.status}</Pill>
            {/* Priority badge */}
            <Pill tone={PRIORITY_TONE[item.priority_level]}>
              {item.priority_level}
            </Pill>
            <span className="text-xs text-ink-soft">
              current: {item.current_category ?? "—"}
            </span>
            {item.confidence !== null && (
              <span className="text-xs text-gray-400">
                confidence: {(item.confidence * 100).toFixed(0)}%
              </span>
            )}
            {/* Lock badge */}
            {lockedByOther && (
              <Pill tone="warn">
                Locked by {item.locked_by_email ?? "another reviewer"}
              </Pill>
            )}
            {lockedByMe && (
              <Pill tone="ok">Locked by you</Pill>
            )}
            {item.lock_status === "expired" && (
              <Pill tone="neutral">Lock expired</Pill>
            )}
            {/* Assignment badge */}
            {item.assigned_to_email && (
              <span className="text-xs text-ink-soft">
                assigned: <span className="font-medium">{item.assigned_to_email}</span>
              </span>
            )}
          </div>
          {/* Priority reasons — compact inline list */}
          {item.priority_reasons.length > 0 && (
            <div className="mt-1 text-xs text-ink-soft">
              priority signals:{" "}
              <span className="font-medium text-gray-700">
                {item.priority_reasons.join(", ")}
              </span>
            </div>
          )}
          {/* Rationale section — source metadata + notes */}
          <div className="mt-2 space-y-1 text-xs text-ink-soft">
            {item.source && (
              <div className="flex flex-wrap gap-3">
                <span>source: <span className="font-medium text-gray-700">{item.source}</span></span>
                {item.rule_id && (
                  <span>rule: <span className="font-mono text-ink-muted">{item.rule_id}</span></span>
                )}
                {item.ai_model && (
                  <span>model: <span className="font-mono text-ink-muted">{item.ai_model}</span></span>
                )}
                {item.ai_prompt_version && (
                  <span>prompt: <span className="font-mono text-ink-muted">{item.ai_prompt_version}</span></span>
                )}
              </div>
            )}
            {item.rationale_notes.length > 0 && (
              <ul className="mt-1 list-disc pl-4 space-y-0.5 text-warn-700">
                {item.rationale_notes.map((note, i) => (
                  <li key={i}>{note}</li>
                ))}
              </ul>
            )}
          </div>
          {lockedByOther ? (
            <p className="mt-4 text-xs text-ink-soft">
              This item is being reviewed by {item.locked_by_email ?? "another reviewer"}.
              Decisions are disabled until the lock is released or expires.
            </p>
          ) : (
            <>
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
              <div className="mt-4 flex flex-wrap gap-2">
                <Button onClick={() => submit("changed")} disabled={busy}>
                  {busy ? "Saving…" : "Change to selected"}
                </Button>
                <Button variant="secondary" onClick={() => submit("accepted")} disabled={busy}>
                  Accept current
                </Button>
                <Button variant="ghost" onClick={() => submit("deferred")} disabled={busy}>
                  Defer
                </Button>
                {/* Lock controls */}
                {!lockedByMe && !item.status.startsWith("accept") && !item.status.startsWith("change") && item.status !== "deferred" && (
                  <Button variant="secondary" onClick={handleClaim} disabled={busy}>
                    Claim
                  </Button>
                )}
                {lockedByMe && (
                  <Button variant="ghost" onClick={handleRelease} disabled={busy}>
                    Release lock
                  </Button>
                )}
              </div>
            </>
          )}
          {err && (
            <div className="mt-3 rounded-md border border-danger-100 bg-danger-50 px-3 py-2 text-xs text-danger-700">
              {err}
            </div>
          )}
        </div>
      </div>
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
        <span className="text-xs text-ink-soft">
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
