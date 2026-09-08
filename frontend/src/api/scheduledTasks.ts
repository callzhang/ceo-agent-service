import { request } from "./console";

export type ScheduledTaskSkillSource = "managed" | "operation";
export type ScheduledTaskTriggerKind = "scheduled" | "manual";

export interface ScheduledTaskSkillRef {
  skill_source: ScheduledTaskSkillSource;
  skill_name: string;
  managed_skill_id: number | null;
  managed_revision_id: number | null;
  position: number;
}

export interface ScheduledTaskDraft {
  name: string;
  prompt: string;
  cron_expression: string;
  timezone_name: string;
  runtime_id: string;
  runtime_options: { thinking?: "low" | "medium" | "high" | "xhigh" };
  working_directory: string;
  enabled: boolean;
  skill_refs: ScheduledTaskSkillRef[];
}

export interface ScheduledTaskSnapshot extends Omit<ScheduledTaskDraft, "enabled"> {
  task_id: number;
  task_version: number;
}

export interface ScheduledTaskRun {
  id: number;
  event_id: string;
  scheduled_task_id: number;
  trigger_kind: ScheduledTaskTriggerKind;
  scheduled_for: string;
  dispatch_status: string;
  skip_or_error_reason: string;
  execution_kind: string;
  execution_id: string;
  created_at: string;
  dispatched_at: string | null;
  snapshot: ScheduledTaskSnapshot;
}

export interface ScheduledTask extends ScheduledTaskDraft {
  id: number;
  migration_key: string | null;
  schedule_description: string;
  next_run_at: string | null;
  version: number;
  recent_run: ScheduledTaskRun | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export interface RuntimeOption {
  route_name: string;
  runtime_kind: string;
  credential_mode: string;
  model: string;
  available: boolean;
  unavailable_reason: string | null;
}

export interface ManagedSkillRevisionOption {
  revision_id: number;
  revision_number: number;
  sha256: string;
  source: string;
  available: boolean;
  unavailable_reason: string | null;
}

export interface ManagedSkillOption {
  skill_id: number;
  name: string;
  display_name: string;
  revisions: ManagedSkillRevisionOption[];
}

export interface OperationSkillOption {
  name: string;
  source: string;
  content_summary: string;
  sha256: string;
  available: boolean;
  unavailable_reason: string | null;
}

export interface ScheduledTaskOptions {
  runtime_options: RuntimeOption[];
  managed_skill_options: ManagedSkillOption[];
  operation_skill_options: OperationSkillOption[];
  meta: { snapshot_at: string };
}

interface ItemEnvelope<T> { item: T; meta: { snapshot_at: string }; }
interface TaskList { items: ScheduledTask[]; meta: { total: number; snapshot_at: string }; }
export interface ScheduledTaskRunPage {
  scheduled_task: ScheduledTask;
  items: ScheduledTaskRun[];
  meta: { snapshot_at: string; page_size: number; next_cursor: string; has_more: boolean };
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function nullableString(value: unknown): value is string | null {
  return typeof value === "string" || value === null;
}

function validSkillRef(value: unknown): value is ScheduledTaskSkillRef {
  const item = record(value);
  return Boolean(item
    && (item.skill_source === "managed" || item.skill_source === "operation")
    && typeof item.skill_name === "string"
    && (typeof item.managed_skill_id === "number" || item.managed_skill_id === null)
    && (typeof item.managed_revision_id === "number" || item.managed_revision_id === null)
    && typeof item.position === "number");
}

function validRun(value: unknown): value is ScheduledTaskRun {
  const item = record(value);
  const snapshot = record(item?.snapshot);
  return Boolean(item && snapshot
    && typeof item.id === "number" && typeof item.event_id === "string"
    && typeof item.scheduled_task_id === "number"
    && (item.trigger_kind === "scheduled" || item.trigger_kind === "manual")
    && typeof item.scheduled_for === "string" && typeof item.dispatch_status === "string"
    && typeof item.skip_or_error_reason === "string" && typeof item.execution_kind === "string"
    && typeof item.execution_id === "string" && typeof item.created_at === "string"
    && nullableString(item.dispatched_at)
    && typeof snapshot.task_id === "number" && typeof snapshot.task_version === "number"
    && typeof snapshot.name === "string" && typeof snapshot.prompt === "string"
    && typeof snapshot.cron_expression === "string" && typeof snapshot.timezone_name === "string"
    && typeof snapshot.runtime_id === "string" && record(snapshot.runtime_options)
    && typeof snapshot.working_directory === "string" && Array.isArray(snapshot.skill_refs)
    && snapshot.skill_refs.every(validSkillRef));
}

function validTask(value: unknown): value is ScheduledTask {
  const item = record(value);
  return Boolean(item && typeof item.id === "number" && nullableString(item.migration_key)
    && typeof item.name === "string" && typeof item.prompt === "string"
    && typeof item.cron_expression === "string" && typeof item.timezone_name === "string"
    && typeof item.schedule_description === "string" && nullableString(item.next_run_at)
    && typeof item.runtime_id === "string" && record(item.runtime_options)
    && typeof item.working_directory === "string" && typeof item.enabled === "boolean"
    && typeof item.version === "number" && Array.isArray(item.skill_refs) && item.skill_refs.every(validSkillRef)
    && (item.recent_run === null || validRun(item.recent_run))
    && typeof item.created_at === "string" && typeof item.updated_at === "string" && nullableString(item.deleted_at));
}

function parseItem<T>(value: unknown, predicate: (item: unknown) => item is T, label: string): ItemEnvelope<T> {
  const envelope = record(value);
  const meta = record(envelope?.meta);
  if (!envelope || !predicate(envelope.item) || !meta || typeof meta.snapshot_at !== "string") throw new Error(`invalid ${label} response`);
  return value as ItemEnvelope<T>;
}

function validRuntimeOption(value: unknown): value is RuntimeOption {
  const item = record(value);
  return Boolean(item && typeof item.route_name === "string" && typeof item.runtime_kind === "string"
    && typeof item.credential_mode === "string" && typeof item.model === "string"
    && typeof item.available === "boolean" && nullableString(item.unavailable_reason));
}

function validRevision(value: unknown): value is ManagedSkillRevisionOption {
  const item = record(value);
  return Boolean(item && typeof item.revision_id === "number" && typeof item.revision_number === "number"
    && typeof item.sha256 === "string" && typeof item.source === "string"
    && typeof item.available === "boolean" && nullableString(item.unavailable_reason));
}

function validManagedSkill(value: unknown): value is ManagedSkillOption {
  const item = record(value);
  return Boolean(item && typeof item.skill_id === "number" && typeof item.name === "string"
    && typeof item.display_name === "string" && Array.isArray(item.revisions) && item.revisions.every(validRevision));
}

function validOperationSkill(value: unknown): value is OperationSkillOption {
  const item = record(value);
  return Boolean(item && typeof item.name === "string" && typeof item.source === "string"
    && typeof item.content_summary === "string" && typeof item.sha256 === "string"
    && typeof item.available === "boolean" && nullableString(item.unavailable_reason));
}

export async function listScheduledTasks(signal?: AbortSignal): Promise<TaskList> {
  const value: unknown = await request("/api/console/scheduled-tasks", { signal });
  const payload = record(value); const meta = record(payload?.meta);
  if (!payload || !Array.isArray(payload.items) || !payload.items.every(validTask) || !meta || typeof meta.total !== "number" || typeof meta.snapshot_at !== "string") throw new Error("invalid scheduled tasks response");
  return value as TaskList;
}

export async function getScheduledTaskOptions(signal?: AbortSignal): Promise<ScheduledTaskOptions> {
  const value: unknown = await request("/api/console/scheduled-task-options", { signal });
  const payload = record(value); const meta = record(payload?.meta);
  if (!payload || !Array.isArray(payload.runtime_options) || !payload.runtime_options.every(validRuntimeOption)
    || !Array.isArray(payload.managed_skill_options) || !payload.managed_skill_options.every(validManagedSkill)
    || !Array.isArray(payload.operation_skill_options) || !payload.operation_skill_options.every(validOperationSkill)
    || !meta || typeof meta.snapshot_at !== "string") throw new Error("invalid scheduled task options response");
  return value as ScheduledTaskOptions;
}

export async function createScheduledTask(payload: ScheduledTaskDraft) {
  return parseItem(await request("/api/console/scheduled-tasks", { method: "POST", body: JSON.stringify(payload) }), validTask, "scheduled task");
}

export async function updateScheduledTask(taskId: number, payload: ScheduledTaskDraft & { version: number }) {
  return parseItem(await request(`/api/console/scheduled-tasks/${taskId}`, { method: "PUT", body: JSON.stringify(payload) }), validTask, "scheduled task");
}

export async function setScheduledTaskEnabled(taskId: number, enabled: boolean, version: number) {
  return parseItem(await request(`/api/console/scheduled-tasks/${taskId}/${enabled ? "enable" : "disable"}`, { method: "POST", body: JSON.stringify({ version }) }), validTask, "scheduled task");
}

export async function runScheduledTask(taskId: number) {
  return parseItem(await request(`/api/console/scheduled-tasks/${taskId}/run`, { method: "POST" }), validRun, "scheduled task run");
}

export async function deleteScheduledTask(taskId: number, version: number) {
  return parseItem(await request(`/api/console/scheduled-tasks/${taskId}?version=${encodeURIComponent(version)}`, { method: "DELETE" }), validTask, "scheduled task");
}

export async function listScheduledTaskRuns(taskId: number, cursor = "", signal?: AbortSignal): Promise<ScheduledTaskRunPage> {
  const query = new URLSearchParams();
  if (cursor) query.set("cursor", cursor);
  query.set("page_size", "20");
  const value: unknown = await request(`/api/console/scheduled-tasks/${taskId}/runs?${query}`, { signal });
  const payload = record(value); const meta = record(payload?.meta);
  if (!payload || !validTask(payload.scheduled_task) || !Array.isArray(payload.items) || !payload.items.every(validRun)
    || !meta || typeof meta.snapshot_at !== "string" || typeof meta.page_size !== "number"
    || typeof meta.next_cursor !== "string" || typeof meta.has_more !== "boolean") throw new Error("invalid scheduled task runs response");
  return value as ScheduledTaskRunPage;
}
