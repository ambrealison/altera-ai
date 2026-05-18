/**
 * Typed fetch client for the Altera AI HTTP API.
 *
 * Every method that talks to a protected endpoint accepts an
 * `accessToken` argument; the caller (a hook or page component)
 * pulls it from `useAuth()`. In dev-auth mode the token is null and
 * the backend falls back to the demo user.
 */

export function getApiBaseUrl(): string {
  return (
    process.env.NEXT_PUBLIC_API_BASE_URL ??
    process.env.NEXT_PUBLIC_API_URL ??
    "http://localhost:8000"
  );
}

export type Methodology = "protein_tracker" | "wwf";

export type ProteinTrackerGroup =
  | "plant_based_core"
  | "plant_based_non_core"
  | "composite_products"
  | "animal_core"
  | "out_of_scope"
  | "unknown";

export type WWFFoodGroup =
  | "FG1"
  | "FG2"
  | "FG3"
  | "FG4"
  | "FG5"
  | "FG6"
  | "FG7"
  | "out_of_scope"
  | "unknown";

export type ManualReviewStatus =
  | "in_queue"
  | "reviewing"
  | "accepted"
  | "changed"
  | "deferred";

export type ManualReviewReason =
  | "low_confidence"
  | "ai_parse_failed"
  | "ai_provider_error"
  | "rule_collision"
  | "contradiction_detected"
  | "requested";

export type DecisionType = "accepted" | "changed" | "deferred";

export interface Project {
  id: string;
  organisation_id: string;
  name: string;
  methodologies_enabled: Methodology[];
  reporting_period_label: string;
  pt_validation_status: string;
  upload_count: number;
  review_queue_count: number;
  run_count: number;
}

export interface ValidationEntry {
  row_number: number;
  field: string | null;
  code: string;
  message: string;
}

export type UploadStatus =
  | "created"
  | "upload_url_created"
  | "uploaded_to_storage"
  | "validation_pending"
  | "validation_running"
  | "validation_failed"
  | "validation_completed"
  | "ingestion_running"
  | "ingestion_failed"
  | "ingestion_completed"
  | "ready_for_classification"
  | "pending"
  | "valid"
  | "invalid"
  | (string & {});

export interface UploadResult {
  id: string;
  project_id: string;
  original_filename: string;
  status: UploadStatus;
  row_count: number | null;
  dropped_columns: string[];
  products_count: number;
  errors: ValidationEntry[];
  warnings: ValidationEntry[];
  file_size_bytes: number | null;
  checksum_sha256: string | null;
  duplicate_of: string | null;
  validation_started_at: string | null;
  validation_completed_at: string | null;
  ingestion_started_at: string | null;
  ingestion_completed_at: string | null;
}

export interface PrepareUploadResult {
  upload_id: string;
  storage_path: string;
  signed_url: string;
  expires_in: number;
}

export interface ClassifySummary {
  methodology: Methodology;
  matched: number;
  pass_through: number;
  rule_collision: number;
  queued_for_review: number;
}

export interface ReviewItem {
  product_id: string;
  upload_id: string | null;
  external_product_id: string;
  product_name: string;
  brand: string | null;
  methodology: Methodology;
  status: ManualReviewStatus;
  reason: ManualReviewReason;
  queued_at: string;
  current_category: string | null;
  confidence: number | null;
  // Phase 19B — safe classification rationale
  source: "deterministic" | "ai" | "manual_review" | null;
  rule_id: string | null;
  ai_model: string | null;
  ai_prompt_version: string | null;
  rationale_notes: string[];
  // Phase 19D — lock and assignment
  locked_by_user_id: string | null;
  locked_by_email: string | null;
  locked_at: string | null;
  lock_expires_at: string | null;
  lock_status: "unlocked" | "locked_by_me" | "locked_by_other" | "expired";
  assigned_to_user_id: string | null;
  assigned_to_email: string | null;
}

export interface ReviewFilters {
  methodology?: Methodology;
  status?: ManualReviewStatus;
  reason?: ManualReviewReason;
  upload_id?: string;
  product_search?: string;
  sort?: "oldest" | "newest";
}

export type BulkReviewAction = "bulk_accept" | "bulk_defer" | "bulk_change_pt_group";

export interface BulkActionRequest {
  action: BulkReviewAction;
  methodology: Methodology;
  product_ids: string[];
  to_pt_group?: string;
  reason?: string;
}

export interface BulkActionResponse {
  action: string;
  requested_count: number;
  updated_count: number;
  decision_ids: string[];
}

export interface Run {
  id: string;
  project_id: string;
  methodology: Methodology;
  rows_count: number;
  started_at: string;
  finished_at: string | null;
  summary: Record<string, unknown>;
}

export interface CurrentUser {
  user_id: string;
  email: string;
  organisation_id: string;
  role: string;
  organisation_type: "gms_client" | "altera_internal";
  auth_provider: "supabase" | "dev";
  is_dev_auth: boolean;
}

export type ApprovalStatus = "draft" | "approved" | "rejected";

export type JobType =
  | "validate_upload"
  | "ingest_upload"
  | "classify_upload"
  | "run_calculation"
  | "generate_export"
  | "generate_report";

export type JobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "retrying";

export interface JobResult {
  // classify_upload result — deterministic counts
  methodology?: string;
  matched?: number;
  pass_through?: number;
  rule_collision?: number;
  queued_for_review?: number;
  total_products?: number;
  // classify_upload result — AI pipeline counts (zero when AI disabled)
  ai_attempted?: number;
  ai_accepted?: number;
  ai_review?: number;
  ai_failed?: number;
  // run_calculation result
  run_id?: string;
  rows_count?: number;
  // generate_export result
  fmt?: string;
  filename?: string;
  size_bytes?: number;
  export_id?: string;
  storage_path?: string;
  // validate_upload result
  errors?: string[];
  is_valid?: boolean;
  // generic
  [key: string]: unknown;
}

export interface Job {
  job_id: string;
  organisation_id: string;
  project_id: string;
  upload_id: string | null;
  run_id: string | null;
  job_type: JobType;
  status: JobStatus;
  progress_pct: number | null;
  created_by: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  failed_at: string | null;
  error_message: string | null;
  retry_count: number;
  idempotency_key: string | null;
  result: JobResult | null;
}

export interface ExportRecord {
  id: string;
  run_id: string;
  format: string;
  approval_status: ApprovalStatus;
  filename: string;
  size_bytes: number;
  created_at: string;
}

async function request<T>(
  path: string,
  init: RequestInit,
  accessToken: string | null,
): Promise<T> {
  const url = `${getApiBaseUrl()}${path}`;
  const headers: Record<string, string> = {
    ...(init.body && !(init.body instanceof FormData)
      ? { "Content-Type": "application/json" }
      : {}),
    ...((init.headers as Record<string, string>) ?? {}),
  };
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
  const res = await fetch(url, {
    ...init,
    headers,
    cache: "no-store",
    credentials: "omit",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // ignore
    }
    throw new Error(`${res.status} ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/**
 * Bind an API token to all method calls so pages don't have to thread
 * it through manually. Pages typically do:
 *
 *     const { accessToken } = useAuth();
 *     const api = createApi(accessToken);
 *     const projects = await api.listProjects();
 */
export function createApi(accessToken: string | null) {
  return {
    me: () => request<CurrentUser>("/api/v1/me", { method: "GET" }, accessToken),
    listProjects: () =>
      request<Project[]>("/api/v1/projects", { method: "GET" }, accessToken),
    getProject: (id: string) =>
      request<Project>(`/api/v1/projects/${id}`, { method: "GET" }, accessToken),
    createProject: (body: {
      name: string;
      methodologies_enabled: Methodology[];
      reporting_period_label: string;
    }) =>
      request<Project>(
        "/api/v1/projects",
        { method: "POST", body: JSON.stringify(body) },
        accessToken,
      ),

    listUploads: (projectId: string) =>
      request<UploadResult[]>(
        `/api/v1/projects/${projectId}/uploads`,
        { method: "GET" },
        accessToken,
      ),
    uploadCsv: async (projectId: string, file: File): Promise<UploadResult> => {
      const fd = new FormData();
      fd.append("file", file);
      return request<UploadResult>(
        `/api/v1/projects/${projectId}/uploads`,
        { method: "POST", body: fd },
        accessToken,
      );
    },

    prepareUpload: (projectId: string, filename: string): Promise<PrepareUploadResult> =>
      request<PrepareUploadResult>(
        `/api/v1/projects/${projectId}/uploads/prepare`,
        { method: "POST", body: JSON.stringify({ filename }) },
        accessToken,
      ),

    ingestUpload: (
      projectId: string,
      uploadId: string,
      storagePath: string,
      originalFilename: string,
    ): Promise<UploadResult> =>
      request<UploadResult>(
        `/api/v1/projects/${projectId}/uploads/${uploadId}/ingest`,
        {
          method: "POST",
          body: JSON.stringify({
            storage_path: storagePath,
            original_filename: originalFilename,
          }),
        },
        accessToken,
      ),

    classify: (projectId: string, uploadId: string, methodology: Methodology) =>
      request<ClassifySummary>(
        `/api/v1/projects/${projectId}/uploads/${uploadId}/classify`,
        { method: "POST", body: JSON.stringify({ methodology }) },
        accessToken,
      ),

    listReview: (projectId: string, filters: ReviewFilters = {}) => {
      const params = new URLSearchParams();
      if (filters.methodology) params.set("methodology", filters.methodology);
      if (filters.status) params.set("status", filters.status);
      if (filters.reason) params.set("reason", filters.reason);
      if (filters.upload_id) params.set("upload_id", filters.upload_id);
      if (filters.product_search) params.set("product_search", filters.product_search);
      if (filters.sort) params.set("sort", filters.sort);
      const q = params.size > 0 ? `?${params.toString()}` : "";
      return request<ReviewItem[]>(
        `/api/v1/projects/${projectId}/review${q}`,
        { method: "GET" },
        accessToken,
      );
    },
    submitDecision: (
      projectId: string,
      productId: string,
      methodology: Methodology,
      body: { decision: DecisionType; to_category?: string; reason?: string },
    ) =>
      request<ReviewItem>(
        `/api/v1/projects/${projectId}/review/${productId}/${methodology}/decision`,
        { method: "POST", body: JSON.stringify(body) },
        accessToken,
      ),

    bulkAction: (projectId: string, body: BulkActionRequest) =>
      request<BulkActionResponse>(
        `/api/v1/projects/${projectId}/review/bulk-action`,
        { method: "POST", body: JSON.stringify(body) },
        accessToken,
      ),

    claimItem: (projectId: string, productId: string, methodology: Methodology) =>
      request<ReviewItem>(
        `/api/v1/projects/${projectId}/review/${productId}/${methodology}/claim`,
        { method: "POST" },
        accessToken,
      ),

    releaseItem: (projectId: string, productId: string, methodology: Methodology) =>
      request<ReviewItem>(
        `/api/v1/projects/${projectId}/review/${productId}/${methodology}/release`,
        { method: "POST" },
        accessToken,
      ),

    refreshLock: (projectId: string, productId: string, methodology: Methodology) =>
      request<ReviewItem>(
        `/api/v1/projects/${projectId}/review/${productId}/${methodology}/refresh-lock`,
        { method: "POST" },
        accessToken,
      ),

    assignItem: (projectId: string, productId: string, methodology: Methodology, assignToUserId: string) =>
      request<ReviewItem>(
        `/api/v1/projects/${projectId}/review/${productId}/${methodology}/assign`,
        { method: "POST", body: JSON.stringify({ assign_to_user_id: assignToUserId }) },
        accessToken,
      ),

    createRun: (projectId: string, methodology: Methodology) =>
      request<Run>(
        `/api/v1/projects/${projectId}/runs`,
        { method: "POST", body: JSON.stringify({ methodology }) },
        accessToken,
      ),
    listRuns: (projectId: string) =>
      request<Run[]>(
        `/api/v1/projects/${projectId}/runs`,
        { method: "GET" },
        accessToken,
      ),
    getRun: (projectId: string, runId: string) =>
      request<Run>(
        `/api/v1/projects/${projectId}/runs/${runId}`,
        { method: "GET" },
        accessToken,
      ),

    /**
     * Download an export, sending the auth token so the backend approval
     * gate is enforced. Follows any redirect (signed URL), then triggers a
     * browser download via a blob URL.
     */
    downloadExport: async (
      projectId: string,
      runId: string,
      fmt: "csv" | "json" | "md",
    ): Promise<void> => {
      const url = `${getApiBaseUrl()}/api/v1/projects/${projectId}/runs/${runId}/export?fmt=${fmt}`;
      const headers: Record<string, string> = {};
      if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
      const res = await fetch(url, { headers, cache: "no-store", credentials: "omit" });
      if (!res.ok) {
        const body = await res.json().catch(() => null) as { detail?: string } | null;
        throw new Error(body?.detail ?? `${res.status} ${res.statusText}`);
      }
      const disposition = res.headers.get("Content-Disposition");
      const filenameMatch = disposition?.match(/filename="([^"]+)"/);
      const filename = filenameMatch?.[1] ?? `export.${fmt}`;
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(objectUrl);
    },

    listExports: (projectId: string, runId: string) =>
      request<ExportRecord[]>(
        `/api/v1/projects/${projectId}/runs/${runId}/exports`,
        { method: "GET" },
        accessToken,
      ),

    approveExport: (projectId: string, runId: string, exportId: string) =>
      request<ExportRecord>(
        `/api/v1/projects/${projectId}/runs/${runId}/exports/${exportId}/approve`,
        { method: "POST", body: JSON.stringify({}) },
        accessToken,
      ),

    rejectExport: (projectId: string, runId: string, exportId: string, reason?: string) =>
      request<ExportRecord>(
        `/api/v1/projects/${projectId}/runs/${runId}/exports/${exportId}/reject`,
        { method: "POST", body: JSON.stringify({ rejection_reason: reason ?? null }) },
        accessToken,
      ),

    // -----------------------------------------------------------------------
    // Jobs (Phase 16)
    // -----------------------------------------------------------------------

    getJob: (jobId: string) =>
      request<Job>(`/api/v1/jobs/${jobId}`, { method: "GET" }, accessToken),

    listJobs: (projectId: string, jobType?: JobType) => {
      const q = jobType ? `?job_type=${jobType}` : "";
      return request<Job[]>(
        `/api/v1/projects/${projectId}/jobs${q}`,
        { method: "GET" },
        accessToken,
      );
    },

    enqueueClassify: (projectId: string, uploadId: string, methodology: Methodology) =>
      request<Job>(
        `/api/v1/projects/${projectId}/uploads/${uploadId}/jobs/classify`,
        { method: "POST", body: JSON.stringify({ methodology }) },
        accessToken,
      ),

    enqueueCalculate: (projectId: string, methodology: Methodology) =>
      request<Job>(
        `/api/v1/projects/${projectId}/jobs/calculate`,
        { method: "POST", body: JSON.stringify({ methodology }) },
        accessToken,
      ),

    enqueueExport: (projectId: string, runId: string, fmt: "csv" | "json" | "md") =>
      request<Job>(
        `/api/v1/projects/${projectId}/runs/${runId}/jobs/export`,
        { method: "POST", body: JSON.stringify({ fmt }) },
        accessToken,
      ),

    /** Poll a job until it reaches a terminal state (succeeded/failed/cancelled). */
    pollJob: async (
      jobId: string,
      { intervalMs = 1500, timeoutMs = 60000 }: { intervalMs?: number; timeoutMs?: number } = {},
    ): Promise<Job> => {
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        const job = await request<Job>(`/api/v1/jobs/${jobId}`, { method: "GET" }, accessToken);
        if (job.status === "succeeded" || job.status === "failed" || job.status === "cancelled") {
          return job;
        }
        await new Promise((res) => setTimeout(res, intervalMs));
      }
      throw new Error(`job ${jobId} did not complete within ${timeoutMs}ms`);
    },
  };
}

export const PT_GROUP_OPTIONS: ProteinTrackerGroup[] = [
  "plant_based_core",
  "plant_based_non_core",
  "composite_products",
  "animal_core",
];

export const WWF_FOOD_GROUP_OPTIONS: WWFFoodGroup[] = [
  "FG1",
  "FG2",
  "FG3",
  "FG4",
  "FG5",
  "FG6",
  "FG7",
];
