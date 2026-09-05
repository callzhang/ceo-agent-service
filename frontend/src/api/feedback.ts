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
  scope_receipt?: Record<string, unknown>;
}

export interface FeedbackIterationCapability {
  enabled: boolean;
  config_id: number | null;
}

export interface FeedbackIterationDecision {
  scope: "skill_only" | "runtime_config" | "code" | "mixed" | "needs_human";
  root_cause: string;
  feedback_keys: string[];
  source_references: string[];
  target_skill_revisions: Array<{ skill_id: number; from_revision: number; to_revision: number }>;
  target_runtime_config_id?: number | null;
  why_not_code: string;
  acceptance: { scenario: string; expected_behavior: string; verification: string[] };
}

export interface FeedbackIterationDecisionRecord {
  id: number;
  batch_id: string;
  feedback_keys: string[];
  round_ids: number[];
  workbench_task_id: string;
  workbench_turn_id: string;
  decision: FeedbackIterationDecision;
  created_at: string;
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
  decisions: FeedbackIterationDecisionRecord[];
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

export interface FeedbackRequestOptions {
  signal?: AbortSignal;
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
    attempt_id: row.attempt_id === undefined ? 0 : row.attempt_id as number, agent_run_id: row.agent_run_id === undefined ? 0 : row.agent_run_id as number, commit_sha: stringField(row, "commit_sha"),
    test_evidence: record(row.test_evidence), restart_evidence: record(row.restart_evidence), health_evidence: record(row.health_evidence),
    note: stringField(row, "note"), resolved_at: stringField(row, "resolved_at"),
  };
  if (!Number.isInteger(item.attempt_id) || !Number.isInteger(item.agent_run_id) || item.attempt_id < 0 || item.agent_run_id < 0) throw new Error("invalid feedback response");
  if (row.scope_receipt !== undefined) item.scope_receipt = record(row.scope_receipt);
  return item;
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) throw new Error("invalid feedback response");
  return value as string[];
}

function positiveInteger(value: unknown): number {
  if (!Number.isInteger(value) || (value as number) <= 0) throw new Error("invalid feedback response");
  return value as number;
}

function parseDecisionRecord(value: unknown): FeedbackIterationDecisionRecord {
  const row = record(value);
  const decision = record(row.decision);
  const acceptance = record(decision.acceptance);
  const targets = Array.isArray(decision.target_skill_revisions) ? decision.target_skill_revisions.map((target) => {
    const targetRow = record(target);
    return { skill_id: positiveInteger(targetRow.skill_id), from_revision: positiveInteger(targetRow.from_revision), to_revision: positiveInteger(targetRow.to_revision) };
  }) : (() => { throw new Error("invalid feedback response"); })();
  const scope = stringField(decision, "scope");
  if (!(["skill_only", "runtime_config", "code", "mixed", "needs_human"] as string[]).includes(scope)) throw new Error("invalid feedback response");
  if (decision.target_runtime_config_id !== undefined && decision.target_runtime_config_id !== null && !Number.isInteger(decision.target_runtime_config_id)) throw new Error("invalid feedback response");
  return {
    id: positiveInteger(row.id), batch_id: stringField(row, "batch_id"), feedback_keys: stringArray(row.feedback_keys),
    round_ids: (Array.isArray(row.round_ids) && row.round_ids.every((item) => Number.isInteger(item) && (item as number) > 0)) ? row.round_ids as number[] : (() => { throw new Error("invalid feedback response"); })(),
    workbench_task_id: stringField(row, "workbench_task_id"), workbench_turn_id: stringField(row, "workbench_turn_id"), created_at: stringField(row, "created_at"),
    decision: {
      scope: scope as FeedbackIterationDecision["scope"], root_cause: stringField(decision, "root_cause"), feedback_keys: stringArray(decision.feedback_keys), source_references: stringArray(decision.source_references),
      target_skill_revisions: targets, ...(decision.target_runtime_config_id === undefined ? {} : { target_runtime_config_id: decision.target_runtime_config_id as number | null }), why_not_code: stringField(decision, "why_not_code"),
      acceptance: { scenario: stringField(acceptance, "scenario"), expected_behavior: stringField(acceptance, "expected_behavior"), verification: stringArray(acceptance.verification) },
    },
  };
}

function parseBatch(value: unknown): FeedbackBatch {
  const row = record(value);
  const rawItems = row.items;
  if (!Array.isArray(rawItems)) throw new Error("invalid feedback response");
  const batch: FeedbackBatch = {
    batch_id: stringField(row, "batch_id"), status: stringField(row, "status"), requested_count: Number(row.requested_count),
    items: rawItems.map(parseProcessingItem),
    decisions: row.decisions === undefined ? [] : (Array.isArray(row.decisions) ? row.decisions.map(parseDecisionRecord) : (() => { throw new Error("invalid feedback response"); })()),
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

export function getFeedbackIterationCapability(signal?: AbortSignal): Promise<FeedbackIterationCapability> {
  return request<unknown>("/api/console/settings/feedback-iteration", { signal }).then((value) => {
    const row = record(value);
    if (typeof row.enabled !== "boolean" || (row.config_id !== null && !Number.isInteger(row.config_id))) throw new Error("invalid feedback response");
    return { enabled: row.enabled, config_id: row.config_id as number | null };
  });
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
  return request<unknown>(`/api/console/feedback?${search.toString()}`, { signal }).then((value) => {
    const page = parseConsoleList(value);
    const items = page.items.map((raw) => {
      const row = record(raw);
      for (const key of ["id", "feedback_key", "attempt_id", "status", "processing_status", "rating", "comment", "context", "created_at", "summary", "batch_id", "processing_task_id"] as const) {
        if (typeof row[key] !== "string") throw new Error("invalid feedback response");
      }
      if (!Array.isArray(row.references) || row.references.some((ref) => typeof ref !== "object" || ref === null || typeof (ref as Record<string, unknown>).label !== "string" || typeof (ref as Record<string, unknown>).route !== "string")) throw new Error("invalid feedback response");
      return row as unknown as FeedbackItem;
    });
    return { ...page, items };
  });
}

export function getFeedbackBatch(batchId: string, signal?: AbortSignal): Promise<ConsoleResource<FeedbackBatch>> {
  return request<unknown>(`/api/console/feedback/batches/${encodeURIComponent(batchId)}`, { signal }).then((value) => parseResource(value, parseBatch));
}

export function claimFeedbackBatch(
  feedbackKeys: string[],
  workbenchTaskId = "",
  workbenchTurnId = "",
  batchId = "",
  options: FeedbackRequestOptions = {},
) {
  return request<unknown>("/api/console/feedback/batches", {
    method: "POST",
    signal: options.signal,
    body: JSON.stringify({ feedback_keys: feedbackKeys, workbench_task_id: workbenchTaskId, workbench_turn_id: workbenchTurnId, ...(batchId ? { batch_id: batchId } : {}) }),
  }).then(parseBatchCommand);
}

export function associateFeedbackTurn(batchId: string, workbenchTaskId: string, workbenchTurnId: string, options: FeedbackRequestOptions = {}) {
  return request<unknown>(`/api/console/feedback/batches/${encodeURIComponent(batchId)}`, {
    method: "PATCH",
    signal: options.signal,
    body: JSON.stringify({ workbench_task_id: workbenchTaskId, workbench_turn_id: workbenchTurnId }),
  }).then(parseBatchCommand);
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
    for (const key of ["id", "attempt_id", "status", "rating", "comment", "context", "created_at", "feedback_key", "feedback_token", "source", "received_at", "conversation_title", "trigger_sender", "trigger_text", "summary", "batch_id", "processing_task_id"] as const) {
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
