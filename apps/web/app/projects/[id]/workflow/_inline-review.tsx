"use client";

/**
 * Phase 34E — Inline manual classification review for wizard Step 5.
 *
 * Replaces the standalone /review page in the normal workflow. Designed
 * for the common case: resolve the queue one product at a time with a
 * single click ("Accepter la suggestion") or by choosing a different
 * Protein Tracker group from a dropdown.
 *
 * Out of scope (kept on /review for admin/debug):
 * - Bulk actions across many products
 * - Claim / release locks for multi-reviewer concurrency
 * - Filter by reason / priority / status / upload
 * - Full classification-history audit trail
 *
 * Pagination: 20 items per page; the queue is rarely larger than a few
 * hundred items because deterministic + AI resolve most rows upstream.
 */

import { useEffect, useState } from "react";

import { Button, Card, Pill } from "@/components/ui";
import { useT } from "@/lib/i18n";
import type {
  Methodology,
  ProteinTrackerGroup,
  ReviewItem,
} from "@/lib/api";
import { ApiError, createApi } from "@/lib/api";

// PT group CODE -> i18n key (codes unchanged; display label is translated).
const PT_GROUP_LABEL_KEYS: Record<ProteinTrackerGroup, string> = {
  plant_based_core: "review.ptGroup.plant_based_core",
  plant_based_non_core: "review.ptGroup.plant_based_non_core",
  composite_products: "review.ptGroup.composite_products",
  animal_core: "review.ptGroup.animal_core",
  out_of_scope: "review.ptGroup.out_of_scope",
  unknown: "review.ptGroup.unknown",
};

const PT_GROUP_OPTIONS: ProteinTrackerGroup[] = [
  "plant_based_core",
  "plant_based_non_core",
  "composite_products",
  "animal_core",
  "out_of_scope",
];

const PAGE_SIZE = 20;

// Returns either a translated label (via t) for a known PT group code, or
// the raw category value when it isn't a known code. ``t`` is passed in so
// this stays a plain helper (hooks belong inside components).
function labelForCategory(t: (key: string) => string, c: string | null): string {
  if (!c) return t("review.noSuggestion");
  const key = PT_GROUP_LABEL_KEYS[c as ProteinTrackerGroup];
  return key ? t(key) : c;
}

function ReasonPill({ reason }: { reason: ReviewItem["reason"] }) {
  const t = useT();
  // Review reason CODE -> i18n key (codes unchanged; label is translated).
  const REASON_KEYS: Record<ReviewItem["reason"], string> = {
    low_confidence: "review.reason.low_confidence",
    ai_parse_failed: "review.reason.ai_parse_failed",
    ai_provider_error: "review.reason.ai_provider_error",
    rule_collision: "review.reason.rule_collision",
    contradiction_detected: "review.reason.contradiction_detected",
    requested: "review.reason.requested",
  };
  const key = REASON_KEYS[reason];
  return <Pill tone="neutral">{key ? t(key) : reason}</Pill>;
}

export function InlineReview({
  projectId,
  accessToken,
  methodology,
  onResolved,
}: {
  projectId: string;
  accessToken: string | null;
  methodology: Methodology;
  onResolved: () => void | Promise<void>;
}) {
  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submittingId, setSubmittingId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  // Per-item override the user can pick before clicking "Valider".
  const [overrides, setOverrides] = useState<Record<string, ProteinTrackerGroup>>(
    {},
  );

  const t = useT();
  const api = createApi(accessToken);

  async function load() {
    setLoadError(null);
    try {
      const r = await api.listReview(projectId, {
        methodology,
        status: "in_queue",
        sort: "priority",
      });
      setItems(r.items);
      // If we dropped below the current page, snap back to a valid one.
      const maxPage = Math.max(0, Math.ceil(r.items.length / PAGE_SIZE) - 1);
      setPage((p) => Math.min(p, maxPage));
    } catch (e) {
      setLoadError(
        e instanceof Error ? e.message : t("review.loadError"),
      );
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, methodology]);

  async function submit(item: ReviewItem, decision: "accepted" | "changed") {
    setSubmittingId(item.product_id);
    setSubmitError(null);
    try {
      const to =
        decision === "changed"
          ? overrides[item.product_id]
          : undefined;
      if (decision === "changed" && !to) {
        setSubmitError(t("review.chooseCategoryFirst"));
        setSubmittingId(null);
        return;
      }
      await api.submitDecision(projectId, item.product_id, methodology, {
        decision,
        to_category: to,
      });
      // Optimistically remove from list, then reload to confirm + pick
      // up any newly surfaced items.
      setItems((prev) =>
        prev ? prev.filter((i) => i.product_id !== item.product_id) : prev,
      );
      await load();
      await onResolved();
    } catch (e) {
      if (e instanceof ApiError && typeof e.detail === "object" && e.detail) {
        const d = e.detail as { message?: string };
        setSubmitError(d.message ?? String(e));
      } else {
        setSubmitError(
          e instanceof Error ? e.message : t("review.decisionError"),
        );
      }
    } finally {
      setSubmittingId(null);
    }
  }

  if (items === null && !loadError) {
    return (
      <Card>
        <p className="text-sm text-gray-500">{t("review.loading")}</p>
      </Card>
    );
  }
  if (loadError) {
    return (
      <Card>
        <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {loadError}
        </div>
      </Card>
    );
  }
  const total = items?.length ?? 0;
  if (total === 0) {
    return (
      <Card>
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          {t("review.empty")}
        </div>
      </Card>
    );
  }
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const start = page * PAGE_SIZE;
  const visible = items!.slice(start, start + PAGE_SIZE);

  return (
    <Card>
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-700">
          {t("review.pageStatus")
            .replace("{n}", String(total))
            .replace("{page}", String(page + 1))
            .replace("{pages}", String(pageCount))}
        </p>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
          >
            ←
          </Button>
          <Button
            variant="ghost"
            onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
            disabled={page >= pageCount - 1}
          >
            →
          </Button>
        </div>
      </div>

      {submitError && (
        <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {submitError}
        </div>
      )}

      <ul className="mt-3 divide-y divide-gray-100">
        {visible.map((item) => {
          const busy = submittingId === item.product_id;
          const chosen = overrides[item.product_id];
          return (
            <li key={item.product_id} className="py-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-gray-800">
                    {item.product_name}
                  </p>
                  <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-gray-500">
                    {item.brand && <span>{item.brand}</span>}
                    <ReasonPill reason={item.reason} />
                    <span>
                      {t("review.suggestionLabel")}{" "}
                      <span className="font-medium text-gray-700">
                        {labelForCategory(t, item.current_category)}
                      </span>
                    </span>
                    {item.confidence != null && (
                      <span>
                        ({(item.confidence * 100).toFixed(0)} %)
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    value={chosen ?? ""}
                    onChange={(e) =>
                      setOverrides((prev) => ({
                        ...prev,
                        [item.product_id]: e.target.value as ProteinTrackerGroup,
                      }))
                    }
                    className="rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-800 focus:border-brand-500 focus:outline-none"
                    disabled={busy}
                  >
                    <option value="">{t("review.chooseCategory")}</option>
                    {PT_GROUP_OPTIONS.map((g) => (
                      <option key={g} value={g}>
                        {t(PT_GROUP_LABEL_KEYS[g])}
                      </option>
                    ))}
                  </select>
                  <Button
                    variant="secondary"
                    onClick={() => void submit(item, "changed")}
                    disabled={busy || !chosen}
                  >
                    {busy ? "…" : t("review.change")}
                  </Button>
                  <Button
                    onClick={() => void submit(item, "accepted")}
                    disabled={busy || !item.current_category}
                  >
                    {busy ? "…" : t("review.accept")}
                  </Button>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
