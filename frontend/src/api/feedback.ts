import {
  type ConsoleList,
  type ConsoleResource,
  type FeedbackItem,
  parseConsoleList,
  request,
} from "./console";

export interface FeedbackProcessingItem {
  feedback_key: string;
  batch_id: string;
  status: "pending" | "processing" | "resolved" | string;
  workbench_task_id: string;
  workbench_turn_id: string;
  attempt_id: number;
  agent_run_id: number;
  commit_sha: string;
  test_evidence: Record<string, unknown>;
  restart_evidence: Record<string, unknown>;
  health_evidence: Record<string, unknown>;
  note: string;
  resolved_at: string;
}

export interface FeedbackBatch {
  batch_id: string;
  status: string;
  requested_count: number;
  created_at?: string;
  updated_at?: string;
  resolved_at?: string;
  feedback_keys?: string[];
  start_message?: string;
  items: FeedbackProcessingItem[];
}

export interface FeedbackDetail extends FeedbackItem {
  feedback_token: string;
  source: string;
  received_at: string;
  agent_run_id: number;
  conversation_title: string;
  trigger_sender: string;
  trigger_text: string;
  processing: FeedbackProcessingItem | null;
}

export interface ResolutionEvidence {
  commit_sha: string;
  test_evidence: Record<string, unknown>;
  restart_evidence: Record<string, unknown>;
  health_evidence: Record<string, unknown>;
  associations?: Record<string, Record<string, unknown>>;
}

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("invalid feedback response");
  return value as Record<string, unknown>;
}

function stringField(row: Record<string, unknown>, key: string): string {
  if (typeof row[key] !== "string") throw new Error("invalid feedback response");
  return row[key] as string;
}

function parseProcessingItem(value: unknown): FeedbackProcessingItem {
  const row = record(value);
  const item: FeedbackProcessingItem = {
    feedback_key: stringField(row, "feedback_key"), batch_id: stringField(row, "batch_id"), status: stringField(row, "status"),
    workbench_task_id: stringField(row, "workbench_task_id"), workbench_turn_id: stringField(row, "workbench_turn_id"),
    attempt_id: Number(row.attempt_id), agent_run_id: Number(row.agent_run_id), commit_sha: stringField(row, "commit_sha"),
    test_evidence: record(row.test_evidence), restart_evidence: record(row.restart_evidence), health_evidence: record(row.health_evidence),
    note: stringField(row, "note"), resolved_at: stringField(row, "resolved_at"),
  };
  if (!Number.isInteger(item.attempt_id) || !Number.isInteger(item.agent_run_id)) throw new Error("invalid feedback response");
  return item;
}

function parseBatch(value: unknown): FeedbackBatch {
  const row = record(value);
  const rawItems = row.items;
  if (!Array.isArray(rawItems)) throw new Error("invalid feedback response");
  const batch: FeedbackBatch = {
    batch_id: stringField(row, "batch_id"), status: stringField(row, "status"), requested_count: Number(row.requested_count),
    items: rawItems.map(parseProcessingItem),
  };
  if (!Number.isInteger(batch.requested_count)) throw new Error("invalid feedback response");
  for (const key of ["created_at", "updated_at", "resolved_at", "start_message"] as const) {
    if (key in row && typeof row[key] !== "string") throw new Error("invalid feedback response");
    if (key in row) (batch as unknown as Record<string, unknown>)[key] = row[key];
  }
  if ("feedback_keys" in row) {
    if (!Array.isArray(row.feedback_keys) || row.feedback_keys.some((key) => typeof key !== "string")) throw new Error("invalid feedback response");
    batch.feedback_keys = row.feedback_keys as string[];
  }
  return batch;
}

function parseResource<T>(value: unknown, parser: (item: unknown) => T): ConsoleResource<T> {
  const row = record(value);
  const meta = record(row.meta);
  if (typeof meta.snapshot_at !== "string") throw new Error("invalid feedback response");
  return { item: parser(row.item), meta: { snapshot_at: meta.snapshot_at } };
}

function parseCommand<T>(value: unknown, parser: (item: unknown) => T): { ok: boolean; item: T; message: string; meta: { updated_at: string } } {
  const row = record(value);
  const meta = record(row.meta);
  if (typeof row.ok !== "boolean" || typeof row.message !== "string" || typeof meta.updated_at !== "string") throw new Error("invalid feedback response");
  return { ok: row.ok, item: parser(row.item), message: row.message, meta: { updated_at: meta.updated_at } };
}

function parseBatchCommand(value: unknown) {
  return parseCommand(value, parseBatch);
}

export function listPendingFeedback(params: Record<string, string | number | undefined> = {}, signal?: AbortSignal): Promise<ConsoleList<FeedbackItem>> {
  const filtered = Object.entries(params).filter(([key, value]) => key !== "status" && value !== undefined && value !== "");
  const search = new URLSearchParams({ status: "pending", ...Object.fromEntries(filtered.map(([key, value]) => [key, String(value)])) });
  return request<unknown>(`/api/console/feedback?${search.toString()}`, { signal }).then((value) => parseConsoleList<FeedbackItem>(value));
}

export function getFeedbackBatch(batchId: string, signal?: AbortSignal): Promise<ConsoleResource<FeedbackBatch>> {
  return request<unknown>(`/api/console/feedback/batches/${encodeURIComponent(batchId)}`, { signal }).then((value) => parseResource(value, parseBatch));
}

export function claimFeedbackBatch(feedbackKeys: string[], workbenchTaskId = "", workbenchTurnId = "") {
  return request<unknown>("/api/console/feedback/batches", { method: "POST", body: JSON.stringify({ feedback_keys: feedbackKeys, workbench_task_id: workbenchTaskId, workbench_turn_id: workbenchTurnId }) }).then(parseBatchCommand);
}

export function associateFeedbackTurn(batchId: string, workbenchTaskId: string, workbenchTurnId: string) {
  return request<unknown>(`/api/console/feedback/batches/${encodeURIComponent(batchId)}`, { method: "PATCH", body: JSON.stringify({ workbench_task_id: workbenchTaskId, workbench_turn_id: workbenchTurnId }) }).then(parseBatchCommand);
}

export function patchFeedbackItem(feedbackKey: string, evidence: Partial<ResolutionEvidence> & { note?: string; status?: "pending" | "processing" }) {
  return request<unknown>(`/api/console/feedback/items/${encodeURIComponent(feedbackKey)}`, { method: "PATCH", body: JSON.stringify(evidence) }).then((value) => parseCommand(value, parseProcessingItem));
}

export function resolveFeedbackBatch(batchId: string, evidence: ResolutionEvidence) {
  return request<unknown>(`/api/console/feedback/batches/${encodeURIComponent(batchId)}/resolve`, { method: "POST", body: JSON.stringify(evidence) }).then((value) => parseCommand(value, (item) => {
    const row = record(item);
    if (typeof row.batch_id !== "string" || typeof row.status !== "string") throw new Error("invalid feedback response");
    return { batch_id: row.batch_id, status: row.status };
  }));
}

export function getFeedbackDetail(feedbackKey: string, signal?: AbortSignal): Promise<ConsoleResource<FeedbackDetail>> {
  return request<unknown>(`/api/console/feedback/${encodeURIComponent(feedbackKey)}`, { signal }).then((value) => parseResource(value, (raw) => {
    const row = record(raw);
    const item = row as unknown as FeedbackDetail;
    for (const key of ["id", "attempt_id", "status", "rating", "comment", "context", "created_at", "feedback_token", "source", "received_at", "conversation_title", "trigger_sender", "trigger_text", "summary"] as const) {
      if (typeof row[key] !== "string") throw new Error("invalid feedback response");
    }
    if (typeof row.agent_run_id !== "number" || (row.processing !== null && row.processing !== undefined && typeof row.processing !== "object")) throw new Error("invalid feedback response");
    item.processing = row.processing === null || row.processing === undefined ? null : parseProcessingItem(row.processing);
    if (!Array.isArray(row.references) || row.references.some((reference) => {
      if (typeof reference !== "object" || reference === null || Array.isArray(reference)) return true;
      const value = reference as Record<string, unknown>;
      return typeof value.label !== "string" || typeof value.route !== "string";
    })) throw new Error("invalid feedback response");
    item.references = row.references as FeedbackItem["references"];
    return item;
  }));
}
