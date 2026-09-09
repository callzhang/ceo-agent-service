import { request } from "./console";

export type ScheduledTaskSkillSource = "managed" | "operation";
export type ScheduledTaskTriggerKind = "scheduled" | "manual";
export type ScheduledTaskDispatchStatus = "pending" | "dispatched" | "skipped" | "failed";

interface ScheduledTaskSkillRefBase {
  skill_name: string;
  position: number;
}

export interface ManagedScheduledTaskSkillRef extends ScheduledTaskSkillRefBase {
  skill_source: "managed";
  managed_skill_id: number;
  managed_revision_id: number;
}

export interface OperationScheduledTaskSkillRef extends ScheduledTaskSkillRefBase {
  skill_source: "operation";
  managed_skill_id: null;
  managed_revision_id: null;
}

export type ScheduledTaskSkillRef = ManagedScheduledTaskSkillRef | OperationScheduledTaskSkillRef;

export interface ScheduledTaskDraft {
  name: string;
  prompt: string;
  cron_expression: string;
  timezone_name: string;
  runtime_id: string;
  runtime_options: { thinking?: "low" | "medium" | "high" | "xhigh" };
  required_runtime_capabilities: string[];
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
  dispatch_status: ScheduledTaskDispatchStatus;
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

interface RuntimeOptionBase {
  route_name: string;
  runtime_kind: string;
  credential_mode: string;
  model: string;
  supported_thinking: Array<"low" | "medium" | "high" | "xhigh">;
  capabilities: string[];
}

type Availability = (
  | { available: true; unavailable_reason: null }
  | { available: false; unavailable_reason: string }
);

export type RuntimeOption = RuntimeOptionBase & Availability;

interface ManagedSkillRevisionOptionBase {
  revision_id: number;
  revision_number: number;
  sha256: string;
  source: string;
}

export type ManagedSkillRevisionOption = ManagedSkillRevisionOptionBase & Availability;

export interface ManagedSkillOption {
  skill_id: number;
  name: string;
  display_name: string;
  revisions: ManagedSkillRevisionOption[];
}

interface OperationSkillOptionBase {
  name: string;
  source: string;
  content_summary: string;
  sha256: string;
}


export type OperationSkillOption = OperationSkillOptionBase & Availability;

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

function positiveInteger(value: unknown): value is number {
  return Number.isInteger(value) && Number(value) > 0;
}

function validStringSet(value: unknown): value is string[] {
  return Array.isArray(value)
    && value.every((item) => typeof item === "string" && Boolean(item.trim()))
    && new Set(value).size === value.length
    && [...value].sort().every((item, index) => item === value[index]);
}

function validRuntimeOptions(value: unknown): value is ScheduledTaskDraft["runtime_options"] {
  const item = record(value);
  if (!item || Object.keys(item).some((key) => key !== "thinking")) return false;
  return !("thinking" in item)
    || item.thinking === "low"
    || item.thinking === "medium"
    || item.thinking === "high"
    || item.thinking === "xhigh";
}

function validSkillRef(value: unknown): value is ScheduledTaskSkillRef {
  const item = record(value);
  if (!item || typeof item.skill_name !== "string" || !item.skill_name.trim()
    || !Number.isInteger(item.position) || Number(item.position) < 0) return false;
  if (item.skill_source === "managed") {
    return Number.isInteger(item.managed_skill_id) && Number(item.managed_skill_id) > 0
      && Number.isInteger(item.managed_revision_id) && Number(item.managed_revision_id) > 0;
  }
  return item.skill_source === "operation"
    && item.managed_skill_id === null
    && item.managed_revision_id === null;
}

function validSkillRefs(value: unknown): value is ScheduledTaskSkillRef[] {
  return Array.isArray(value)
    && value.length > 0
    && value.every((ref, position) => validSkillRef(ref) && ref.position === position);
}

function validRun(value: unknown): value is ScheduledTaskRun {
  const item = record(value);
  const snapshot = record(item?.snapshot);
  return Boolean(item && snapshot
    && positiveInteger(item.id) && typeof item.event_id === "string"
    && positiveInteger(item.scheduled_task_id)
    && (item.trigger_kind === "scheduled" || item.trigger_kind === "manual")
    && typeof item.scheduled_for === "string"
    && (item.dispatch_status === "pending" || item.dispatch_status === "dispatched" || item.dispatch_status === "skipped" || item.dispatch_status === "failed")
    && typeof item.skip_or_error_reason === "string" && typeof item.execution_kind === "string"
    && typeof item.execution_id === "string" && typeof item.created_at === "string"
    && nullableString(item.dispatched_at)
    && positiveInteger(snapshot.task_id) && positiveInteger(snapshot.task_version)
    && typeof snapshot.name === "string" && typeof snapshot.prompt === "string"
    && typeof snapshot.cron_expression === "string" && typeof snapshot.timezone_name === "string"
    && typeof snapshot.runtime_id === "string" && validRuntimeOptions(snapshot.runtime_options)
    && validStringSet(snapshot.required_runtime_capabilities)
    && typeof snapshot.working_directory === "string" && validSkillRefs(snapshot.skill_refs));
}

function validTask(value: unknown): value is ScheduledTask {
  const item = record(value);
  return Boolean(item && positiveInteger(item.id) && nullableString(item.migration_key)
    && typeof item.name === "string" && typeof item.prompt === "string"
    && typeof item.cron_expression === "string" && typeof item.timezone_name === "string"
    && typeof item.schedule_description === "string" && nullableString(item.next_run_at)
    && typeof item.runtime_id === "string" && validRuntimeOptions(item.runtime_options)
    && validStringSet(item.required_runtime_capabilities)
    && typeof item.working_directory === "string" && typeof item.enabled === "boolean"
    && positiveInteger(item.version) && validSkillRefs(item.skill_refs)
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
  const supported = item?.supported_thinking;
  const capabilities = item?.capabilities;
  return Boolean(item && typeof item.route_name === "string" && typeof item.runtime_kind === "string"
    && typeof item.credential_mode === "string" && typeof item.model === "string"
    && Array.isArray(supported) && supported.every((thinking) => thinking === "low" || thinking === "medium" || thinking === "high" || thinking === "xhigh")
    && new Set(supported).size === supported.length
    && validStringSet(capabilities)
    && ((item.available === true && item.unavailable_reason === null)
      || (item.available === false && typeof item.unavailable_reason === "string" && Boolean(item.unavailable_reason.trim()))));
}

function validAvailability(item: Record<string, unknown>) {
  return (item.available === true && item.unavailable_reason === null)
    || (item.available === false && typeof item.unavailable_reason === "string" && Boolean(item.unavailable_reason.trim()));
}

function validRevision(value: unknown): value is ManagedSkillRevisionOption {
  const item = record(value);
  return Boolean(item && positiveInteger(item.revision_id) && positiveInteger(item.revision_number)
    && typeof item.sha256 === "string" && Boolean(item.sha256.trim())
    && typeof item.source === "string" && Boolean(item.source.trim())
    && validAvailability(item));
}

function validManagedSkill(value: unknown): value is ManagedSkillOption {
  const item = record(value);
  return Boolean(item && positiveInteger(item.skill_id) && typeof item.name === "string" && Boolean(item.name.trim())
    && typeof item.display_name === "string" && Boolean(item.display_name.trim())
    && Array.isArray(item.revisions) && item.revisions.every(validRevision));
}

function validOperationSkill(value: unknown): value is OperationSkillOption {
  const item = record(value);
  return Boolean(item && typeof item.name === "string" && Boolean(item.name.trim())
    && typeof item.source === "string" && Boolean(item.source.trim())
    && typeof item.content_summary === "string"
    && typeof item.sha256 === "string" && Boolean(item.sha256.trim())
    && validAvailability(item));
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
