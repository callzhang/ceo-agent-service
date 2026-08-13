import type {
  Artifact,
  Attachment,
  Confirmation,
  ConfirmationStatus,
  EventType,
  RuntimeCapabilities,
  Task,
  TaskPage,
  TaskState,
  Timeline,
  Turn,
  TurnStatus,
  WorkbenchEvent,
  WorkbenchStats,
} from "./types";
import { isPublicEventPayload } from "./events";

const basePath = "/api/workbench";
const genericErrorDetail = "请求失败，请稍后重试";
const invalidResponseDetail = "服务返回的数据格式无效";
const taskStates: readonly TaskState[] = [
  "idle",
  "queued",
  "running",
  "waiting_confirmation",
  "completed",
  "stopped",
  "failed",
];
const turnStatuses: readonly TurnStatus[] = taskStates.slice(1) as TurnStatus[];
const eventTypes: readonly EventType[] = [
  "text_delta",
  "thinking_summary",
  "tool_started",
  "tool_completed",
  "file_changed",
  "artifact_created",
  "confirmation_required",
  "status_changed",
  "turn_completed",
  "turn_failed",
];
const confirmationStatuses: readonly ConfirmationStatus[] = [
  "pending",
  "confirmed",
  "cancelled",
  "executed",
  "failed",
];

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: string;

  constructor(status: number, code: string, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

export interface RequestOptions {
  signal?: AbortSignal;
}

export interface ListTasksOptions extends RequestOptions {
  archived?: "active" | "archived" | "all";
  limit?: number;
  cursor?: string;
}

export interface TimelineOptions extends RequestOptions {
  turnLimit?: number;
  eventLimit?: number;
  before?: string;
  eventBefore?: number;
  artifactAfter?: string;
  confirmationAfter?: string;
  attachmentAfter?: string;
}

export interface AttachmentUpload {
  filename: string;
  media_type: string;
  content_base64: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringField(value: Record<string, unknown>, key: string): string {
  if (typeof value[key] !== "string") throwInvalidResponse();
  return value[key];
}

function booleanField(value: Record<string, unknown>, key: string): boolean {
  if (typeof value[key] !== "boolean") throwInvalidResponse();
  return value[key];
}

function numberField(value: Record<string, unknown>, key: string): number {
  const item = value[key];
  if (typeof item !== "number" || !Number.isFinite(item)) throwInvalidResponse();
  return item;
}

function integerField(value: Record<string, unknown>, key: string): number {
  const item = numberField(value, key);
  if (!Number.isInteger(item)) throwInvalidResponse();
  return item;
}

function enumField<T extends string>(
  value: Record<string, unknown>,
  key: string,
  allowed: readonly T[],
): T {
  const item = value[key];
  if (typeof item !== "string" || !allowed.includes(item as T)) throwInvalidResponse();
  return item as T;
}

function recordField(value: Record<string, unknown>, key: string): Record<string, unknown> {
  const item = value[key];
  if (!isRecord(item)) throwInvalidResponse();
  return { ...item };
}

function arrayField(value: Record<string, unknown>, key: string): unknown[] {
  const item = value[key];
  if (!Array.isArray(item)) throwInvalidResponse();
  return item;
}

function throwInvalidResponse(): never {
  throw new ApiError(502, "invalid_response", invalidResponseDetail);
}

function parseTask(value: unknown): Task {
  if (!isRecord(value)) throwInvalidResponse();
  return {
    id: stringField(value, "id"),
    title: stringField(value, "title"),
    runtime_kind: stringField(value, "runtime_kind"),
    archived_at: stringField(value, "archived_at"),
    state: enumField(value, "state", taskStates),
    created_at: stringField(value, "created_at"),
    updated_at: stringField(value, "updated_at"),
  };
}

function parseTurn(value: unknown): Turn {
  if (!isRecord(value)) throwInvalidResponse();
  return {
    id: stringField(value, "id"),
    task_id: stringField(value, "task_id"),
    client_request_id: stringField(value, "client_request_id"),
    user_text: stringField(value, "user_text"),
    status: enumField(value, "status", turnStatuses),
    stop_requested: booleanField(value, "stop_requested"),
    final_text: stringField(value, "final_text"),
    error_code: stringField(value, "error_code"),
    error_detail: stringField(value, "error_detail"),
    started_at: stringField(value, "started_at"),
    completed_at: stringField(value, "completed_at"),
    created_at: stringField(value, "created_at"),
    updated_at: stringField(value, "updated_at"),
  };
}

function parseEvent(value: unknown): WorkbenchEvent {
  if (!isRecord(value)) throwInvalidResponse();
  const id = integerField(value, "id");
  const sequence = integerField(value, "sequence");
  if (id <= 0 || sequence <= 0) throwInvalidResponse();
  const eventType = enumField(value, "event_type", eventTypes);
  const payload = recordField(value, "payload");
  if (!isPublicEventPayload(eventType, payload)) throwInvalidResponse();
  return {
    id,
    turn_id: stringField(value, "turn_id"),
    sequence,
    event_type: eventType,
    payload,
    created_at: stringField(value, "created_at"),
  };
}

function parseAttachment(value: unknown): Attachment {
  if (!isRecord(value)) throwInvalidResponse();
  return {
    id: stringField(value, "id"),
    task_id: stringField(value, "task_id"),
    filename: stringField(value, "filename"),
    media_type: stringField(value, "media_type"),
    size_bytes: integerField(value, "size_bytes"),
    created_at: stringField(value, "created_at"),
  };
}

function parseArtifact(value: unknown): Artifact {
  if (!isRecord(value)) throwInvalidResponse();
  return {
    id: stringField(value, "id"),
    turn_id: stringField(value, "turn_id"),
    label: stringField(value, "label"),
    media_type: stringField(value, "media_type"),
    created_at: stringField(value, "created_at"),
    download_url: stringField(value, "download_url"),
  };
}

function parseConfirmation(value: unknown): Confirmation {
  if (!isRecord(value)) throwInvalidResponse();
  const targets = arrayField(value, "canonical_targets");
  if (!targets.every((target) => typeof target === "string")) throwInvalidResponse();
  return {
    id: stringField(value, "id"),
    turn_id: stringField(value, "turn_id"),
    action_kind: stringField(value, "action_kind"),
    target: stringField(value, "target"),
    summary: stringField(value, "summary"),
    risk: stringField(value, "risk"),
    canonical_capability: stringField(value, "canonical_capability"),
    canonical_operation: stringField(value, "canonical_operation"),
    canonical_targets: targets as string[],
    status: enumField(value, "status", confirmationStatuses),
    decision_requested: stringField(value, "decision_requested"),
    decision_requested_at: stringField(value, "decision_requested_at"),
    proposer_quiesced: booleanField(value, "proposer_quiesced"),
    created_at: stringField(value, "created_at"),
    decided_at: stringField(value, "decided_at"),
  };
}

function parseTimeline(value: unknown): Timeline {
  if (!isRecord(value)) throwInvalidResponse();
  return {
    task: parseTask(value.task),
    turns: arrayField(value, "turns").map(parseTurn),
    events: arrayField(value, "events").map(parseEvent),
    attachments: arrayField(value, "attachments").map(parseAttachment),
    artifacts: arrayField(value, "artifacts").map(parseArtifact),
    confirmations: arrayField(value, "confirmations").map(parseConfirmation),
    next_cursor: stringField(value, "next_cursor"),
    has_more: booleanField(value, "has_more"),
    events_has_more: booleanField(value, "events_has_more"),
    events_next_cursor: integerField(value, "events_next_cursor"),
    artifacts_has_more: booleanField(value, "artifacts_has_more"),
    artifacts_next_cursor: stringField(value, "artifacts_next_cursor"),
    confirmations_has_more: booleanField(value, "confirmations_has_more"),
    confirmations_next_cursor: stringField(value, "confirmations_next_cursor"),
    attachments_has_more: booleanField(value, "attachments_has_more"),
    attachments_next_cursor: stringField(value, "attachments_next_cursor"),
  };
}

function parseRuntimeCapabilities(value: unknown): RuntimeCapabilities {
  if (!isRecord(value)) throwInvalidResponse();
  const capabilities = recordField(value, "capabilities");
  return {
    kind: stringField(value, "kind"),
    capabilities: {
      session_resume: booleanField(capabilities, "session_resume"),
      streamed_text: booleanField(capabilities, "streamed_text"),
      structured_tools: booleanField(capabilities, "structured_tools"),
      image_input: booleanField(capabilities, "image_input"),
      model_selection: booleanField(capabilities, "model_selection"),
      mcp_configuration: booleanField(capabilities, "mcp_configuration"),
      stoppable: booleanField(capabilities, "stoppable"),
      recoverable: booleanField(capabilities, "recoverable"),
    },
  };
}

function parseCountRecord(value: unknown, keys: readonly string[]): Record<string, number> {
  if (!isRecord(value)) throwInvalidResponse();
  return Object.fromEntries(keys.map((key) => [key, integerField(value, key)]));
}

function parseStats(value: unknown): WorkbenchStats {
  if (!isRecord(value) || !isRecord(value.tasks) || !isRecord(value.duration)) {
    throwInvalidResponse();
  }
  return {
    tasks: {
      total: integerField(value.tasks, "total"),
      active: integerField(value.tasks, "active"),
      archived: integerField(value.tasks, "archived"),
    },
    turns: parseCountRecord(value.turns, turnStatuses) as Record<TurnStatus, number>,
    confirmations: parseCountRecord(
      value.confirmations,
      confirmationStatuses,
    ) as Record<ConfirmationStatus, number>,
    events: isRecord(value.events)
      ? Object.fromEntries(
          Object.entries(value.events).map(([key, count]) => {
            if (typeof count !== "number" || !Number.isInteger(count)) throwInvalidResponse();
            return [key, count];
          }),
        )
      : throwInvalidResponse(),
    attachments: integerField(value, "attachments"),
    artifacts: integerField(value, "artifacts"),
    duration: {
      completed_count: integerField(value.duration, "completed_count"),
      total_seconds: numberField(value.duration, "total_seconds"),
      average_seconds: numberField(value.duration, "average_seconds"),
    },
  };
}

function safeErrorText(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const cleaned = Array.from(value)
    .filter((character) => character.charCodeAt(0) >= 32 || character === "\n")
    .join("")
    .trim();
  if (!cleaned || cleaned.includes("<") || cleaned.includes(">")) return null;
  return cleaned.slice(0, 300);
}

async function errorFromResponse(response: Response): Promise<ApiError> {
  const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
  if (!contentType.includes("json")) {
    return new ApiError(response.status, "request_failed", genericErrorDetail);
  }
  try {
    const body: unknown = await response.json();
    if (!isRecord(body)) {
      return new ApiError(response.status, "request_failed", genericErrorDetail);
    }
    const nestedDetail = isRecord(body.detail) ? body.detail : null;
    const code =
      safeErrorText(body.code) ?? safeErrorText(nestedDetail?.code) ?? "request_failed";
    const detail =
      safeErrorText(typeof body.detail === "string" ? body.detail : nestedDetail?.message) ??
      genericErrorDetail;
    return new ApiError(response.status, code, detail);
  } catch {
    return new ApiError(response.status, "request_failed", genericErrorDetail);
  }
}

async function request<T>(
  path: string,
  parse: (value: unknown) => T,
  init: RequestInit = {},
): Promise<{ data: T; response: Response }> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...init.headers,
    },
  });
  if (!response.ok) throw await errorFromResponse(response);
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throwInvalidResponse();
  }
  return { data: parse(body), response };
}

function jsonMutation(method: "POST" | "PATCH", body: unknown, options?: RequestOptions): RequestInit {
  return {
    method,
    signal: options?.signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

function taskPath(taskId: string): string {
  return `${basePath}/tasks/${encodeURIComponent(taskId)}`;
}

function confirmationPath(taskId: string, turnId: string, confirmationId: string): string {
  return `${taskPath(taskId)}/turns/${encodeURIComponent(turnId)}/confirmations/${encodeURIComponent(confirmationId)}`;
}

export async function listTasks(options: ListTasksOptions = {}): Promise<TaskPage> {
  const params = new URLSearchParams();
  if (options.archived) params.set("archived", options.archived);
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  if (options.cursor) params.set("cursor", options.cursor);
  const query = params.size ? `?${params.toString()}` : "";
  const { data, response } = await request(
    `${basePath}/tasks${query}`,
    (value) => {
      if (!Array.isArray(value)) throwInvalidResponse();
      return value.map(parseTask);
    },
    { signal: options.signal },
  );
  return { items: data, nextCursor: response.headers.get("X-Next-Cursor") ?? "" };
}

export async function createTask(
  title: string,
  runtimeKind: string,
  options?: RequestOptions,
): Promise<Task> {
  return (
    await request(
      `${basePath}/tasks`,
      parseTask,
      jsonMutation("POST", { title, runtime_kind: runtimeKind }, options),
    )
  ).data;
}

export async function getTask(taskId: string, options?: RequestOptions): Promise<Task> {
  return (await request(taskPath(taskId), parseTask, { signal: options?.signal })).data;
}

export async function renameTask(
  taskId: string,
  title: string,
  options?: RequestOptions,
): Promise<Task> {
  return (
    await request(taskPath(taskId), parseTask, jsonMutation("PATCH", { title }, options))
  ).data;
}

export async function archiveTask(taskId: string, options?: RequestOptions): Promise<Task> {
  return (
    await request(
      `${taskPath(taskId)}/archive`,
      parseTask,
      jsonMutation("POST", {}, options),
    )
  ).data;
}

export async function uploadAttachment(
  taskId: string,
  upload: AttachmentUpload,
  options?: RequestOptions,
): Promise<Attachment> {
  return (
    await request(
      `${taskPath(taskId)}/attachments`,
      parseAttachment,
      jsonMutation("POST", upload, options),
    )
  ).data;
}

export async function createTurn(
  taskId: string,
  text: string,
  clientRequestId: string,
  options?: RequestOptions,
): Promise<Turn> {
  return (
    await request(
      `${taskPath(taskId)}/turns`,
      parseTurn,
      jsonMutation("POST", { text, client_request_id: clientRequestId }, options),
    )
  ).data;
}

export async function stopTurn(
  taskId: string,
  turnId: string,
  options?: RequestOptions,
): Promise<Turn> {
  return (
    await request(
      `${taskPath(taskId)}/turns/${encodeURIComponent(turnId)}/stop`,
      parseTurn,
      jsonMutation("POST", {}, options),
    )
  ).data;
}

export async function confirmAction(
  taskId: string,
  turnId: string,
  confirmationId: string,
  options?: RequestOptions,
): Promise<Confirmation> {
  return (
    await request(
      `${confirmationPath(taskId, turnId, confirmationId)}/confirm`,
      parseConfirmation,
      jsonMutation("POST", {}, options),
    )
  ).data;
}

export async function cancelAction(
  taskId: string,
  turnId: string,
  confirmationId: string,
  options?: RequestOptions,
): Promise<Confirmation> {
  return (
    await request(
      `${confirmationPath(taskId, turnId, confirmationId)}/cancel`,
      parseConfirmation,
      jsonMutation("POST", {}, options),
    )
  ).data;
}

export async function getTimeline(
  taskId: string,
  options: TimelineOptions = {},
): Promise<Timeline> {
  const params = new URLSearchParams();
  if (options.turnLimit !== undefined) params.set("turn_limit", String(options.turnLimit));
  if (options.eventLimit !== undefined) params.set("event_limit", String(options.eventLimit));
  if (options.before) params.set("before", options.before);
  if (options.eventBefore !== undefined) params.set("event_before", String(options.eventBefore));
  if (options.artifactAfter) params.set("artifact_after", options.artifactAfter);
  if (options.confirmationAfter) params.set("confirmation_after", options.confirmationAfter);
  if (options.attachmentAfter) params.set("attachment_after", options.attachmentAfter);
  const query = params.size ? `?${params.toString()}` : "";
  return (
    await request(`${taskPath(taskId)}/timeline${query}`, parseTimeline, {
      signal: options.signal,
    })
  ).data;
}

export async function getTurn(turnId: string, options?: RequestOptions): Promise<Turn> {
  return (
    await request(`${basePath}/turns/${encodeURIComponent(turnId)}`, parseTurn, {
      signal: options?.signal,
    })
  ).data;
}

export async function getStats(options?: RequestOptions): Promise<WorkbenchStats> {
  return (
    await request(`${basePath}/stats`, parseStats, { signal: options?.signal })
  ).data;
}

export async function runtimeCapabilities(options?: RequestOptions): Promise<RuntimeCapabilities[]> {
  return (
    await request(
      `${basePath}/runtimes`,
      (value) => {
        if (!Array.isArray(value)) throwInvalidResponse();
        return value.map(parseRuntimeCapabilities);
      },
      { signal: options?.signal },
    )
  ).data;
}
