export interface ConsoleListMeta {
  page: number;
  page_size: number;
  total: number;
  next_cursor: string;
  has_more: boolean;
  snapshot_at: string;
}

export interface ConsoleList<T = Record<string, unknown>> {
  items: T[];
  meta: ConsoleListMeta;
}

export interface ConsoleResource<T = Record<string, unknown>> {
  item: T;
  meta: { snapshot_at: string };
}

export class ConsoleApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ConsoleApiError";
    this.status = status;
    this.code = code;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isListMeta(value: unknown): value is ConsoleListMeta {
  if (!isRecord(value)) return false;
  return typeof value.page === "number"
    && typeof value.page_size === "number"
    && typeof value.total === "number"
    && typeof value.next_cursor === "string"
    && typeof value.has_more === "boolean"
    && typeof value.snapshot_at === "string";
}

export function parseConsoleList<T = Record<string, unknown>>(value: unknown): ConsoleList<T> {
  if (!isRecord(value) || !Array.isArray(value.items) || !isListMeta(value.meta)) {
    throw new Error("invalid console response");
  }
  return { items: value.items as T[], meta: value.meta };
}

export function displayValue(value: unknown): string {
  if (typeof value === "string") return value || "未提供";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(displayValue).join("；") || "未提供";
  if (isRecord(value)) {
    for (const key of ["title", "text", "content", "summary", "label"]) {
      if (key in value && value[key] !== value) return displayValue(value[key]);
    }
    return JSON.stringify(value) || "未提供";
  }
  return "未提供";
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = isRecord(payload) ? payload : {};
    const message = typeof error.message === "string"
      ? error.message
      : typeof error.detail === "string"
        ? error.detail
        : "请求失败，请稍后重试";
    throw new ConsoleApiError(response.status, typeof error.code === "string" ? error.code : "request_failed", message);
  }
  return payload as T;
}

function query(params: Record<string, string | number | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== "") search.set(key, String(value));
  return search.size ? `?${search.toString()}` : "";
}

export interface TaskSummary { id: string; title: string; status: string; category: string; priority: string; risk: string; owner: string; progress: string; todo_count: number; state_summary: string; next_summary: string; }
export interface TaskFilters { categories: string[]; task_states: string[]; }
export interface TaskList extends ConsoleList<TaskSummary> { filters: TaskFilters; }
export interface TaskDetail extends TaskSummary { description: string; background: string; blocker: string; follow_up_mode: string; tags: string[]; facts: Array<{ id: string; description: unknown; source: unknown; created: string; updated: string }>; todos: Array<Record<string, unknown>>; updates: Array<Record<string, unknown>>; evidence_candidates: Array<Record<string, unknown>>; memory: Array<Record<string, unknown>>; unlinked_follow_ups: Array<Record<string, unknown>>; }
export interface HistoryItem { id: string; occurred_at: string; title: string; type: string; status: string; summary: unknown; actor: string; detail_url: string; kind?: string; input?: string; output?: string; action?: string; }
export interface HistoryChart { labels: string[]; series: Array<{ name: string; data: number[] }>; total: number; range: string; }
export interface AttentionItem { id: string; category: string; root_cause: string; context: string; severity: string; count: number; summary: unknown; error: unknown; detail_label: string; detail: unknown; updated_at: string; links: Array<{ label: string; href: string }>; }
export interface FeedbackReference { label: string; route: string; }
export interface FeedbackProcessingRound {
  id: number;
  feedback_key: string;
  round_number: number;
  batch_id: string;
  status: "processing" | "resolved";
  workbench_task_id: string;
  workbench_turn_id: string;
  attempt_id: number;
  agent_run_id: number;
  commit_sha: string;
  test_evidence: Record<string, unknown>;
  restart_evidence: Record<string, unknown>;
  health_evidence: Record<string, unknown>;
  backlog_evidence?: Record<string, unknown>;
  scope_receipt?: Record<string, unknown>;
  receipt_version: 1 | 2;
  note: string;
  started_at: string;
  resolved_at: string;
  reopened_at: string;
  reopen_reason: string;
  created_at: string;
  updated_at: string;
}
export interface FeedbackItem {
  id: string;
  feedback_key?: string;
  attempt_id: string;
  status: string;
  processing_status?: string;
  rating: string;
  comment: string;
  context: string;
  created_at: string;
  summary: string;
  references: FeedbackReference[];
  batch_id: string;
  processing_task_id: string;
  current_processing?: FeedbackProcessingRound | null;
  processing_history?: FeedbackProcessingRound[];
}
export interface FeedbackList extends ConsoleList<FeedbackItem> { pending_count?: number; }
export interface EmailAttachmentMetadata {
  filename: string;
  mime_type: string;
  size_bytes: number;
  inline: boolean;
}
export interface EmailClassificationItem {
  id: string;
  provider: string;
  mailbox: string;
  message_id: string;
  thread_id: string;
  sender: string;
  subject: string;
  preview: string;
  message_text?: string;
  quoted_text?: string;
  important?: boolean | null;
  provider_classification?: EmailProviderClassification | null;
  description_version?: string;
  recipients?: string[];
  cc?: string;
  received_at: string;
  category: string;
  confidence: number;
  margin: number;
  probabilities: Record<string, number>;
  model_version: string;
  config_version: string;
  status: string;
  classification_source: string;
  action_plan: Record<string, unknown>;
  current_action_plan_id: string | null;
  attachment_metadata: EmailAttachmentMetadata[];
  confirmed_at: string;
  created_at: string;
  updated_at: string;
}
export interface EmailObservabilityAttempt {
  attempt_number: number;
  status: string;
  provider_operation: string;
  provider_result_id: string;
  error: string;
  started_at: string;
  finished_at: string;
}
export interface EmailObservabilityStep {
  sequence: number;
  operation: string;
  state: string;
  reference: string;
}
export interface EmailObservabilityEvent {
  kind: "provider_action" | "auto_reply" | "unsubscribe" | string;
  operation: string;
  action_id?: string;
  action_identity?: string;
  action_plan_id?: string;
  action_plan_version?: number;
  lifecycle_version?: string;
  task_id?: number;
  task_status?: string;
  consumer_run_ids?: number[];
  audit_run_ids?: number[];
  status: string;
  attempt_count?: number;
  provider_operation?: string;
  provider_result_id?: string;
  receipt_id?: string;
  error?: string;
  summary?: string;
  entry_reference?: string;
  evidence?: string;
  result_text?: string;
  observation_digest?: string;
  started_at?: string;
  finished_at?: string;
  completed_at?: string;
  created_at?: string;
  attempts?: EmailObservabilityAttempt[];
  steps?: EmailObservabilityStep[];
}
export interface EmailClassificationDetail {
  ok: boolean;
  item: EmailClassificationItem;
  observability: EmailObservabilityEvent[];
  provider_classification?: EmailProviderClassification | null;
  meta: { snapshot_at: string };
}
export interface EmailProviderClassification {
  state: string;
  category_key: string | null;
  important: boolean | null;
  [key: string]: unknown;
}
export interface EmailCategoryConfig {
  category_key: string;
  display_name: string;
  core_description: string;
  include: string[];
  exclude: string[];
  description_version: string;
  bindings: Array<{account_id: string; provider_folder_id: string; provider_folder_name: string; binding_status: string; last_verified_at: string}>;
  threshold: number;
  actions: string[];
  action_parameters?: Record<string, Record<string, unknown>>;
  enabled: boolean;
  config_version: string;
  updated_at: string;
}
export interface EmailAccountItem {
  account_id: string;
  display_name: string;
  email_address: string;
  imap_host: string;
  imap_port: number;
  imap_tls: boolean;
  imap_username: string;
  imap_secret_configured: boolean;
  enabled: boolean;
  scan_folders: string[];
  scan_interval_seconds: number;
  created_at: string;
  updated_at: string;
}
export interface EmailAccountPayload {
  account_id: string;
  display_name: string;
  email_address: string;
  imap_host: string;
  imap_port: number;
  imap_tls: boolean;
  imap_username: string;
  imap_secret?: string;
  enabled: boolean;
  scan_folders: string[];
  scan_interval_seconds: number;
}
export interface EmailAccountSaveResult {
  ok: boolean;
  item: EmailAccountItem;
  restart_required: boolean;
  message: string;
}
export interface EmailAccountTestResult {
  ok: boolean;
  account_id: string;
  diagnostics: {
    imap: { ok: boolean; code: string };
    smtp: { enabled: false; tested: false; code: "disabled" };
  };
}
export interface EmailModelEvidence {
  model_id: string;
  model_version: string;
  parent_model_id?: string | null;
  model_family?: string;
  tokenizer_version?: string;
  feature_version?: string;
  training_dataset_version?: string;
  status: string;
  status_reason: string;
  candidate_reason: string;
  promotion_reason: string;
  rejection_reason: string;
  failure_reason: string;
  superseded_reason: string;
  integrity_status: "verified" | "corrupt";
  integrity_error: string;
  lifecycle: Array<{ event_id: string; model_id: string; status: string; reason: string; occurred_at: string }>;
  trained_at: string;
  training_started_at: string;
  training_finished_at: string;
  sample_count: number;
  new_sample_count: number;
  category_counts: Record<string, number>;
  account_counts: Record<string, number>;
  validation_method: string;
  accuracy: number;
  macro_f1: number;
  per_category_metrics: Record<string, Record<string, unknown>>;
  prediction_latency_p50_ms: number;
  prediction_latency_p95_ms: number;
  artifact_sha256: string;
}
export interface EmailLearningEvidence {
  runtime: EmailRuntime;
  promotion_gate: {config: EmailPromotionConfig; promotion_eligible: boolean; checks: Array<{key: string; actual: unknown; target: unknown; operator: string; passed: boolean; reason: string}>};
  mode_transitions: Array<Record<string, unknown>>;
  staged_models: EmailStagedModel[];
  active_model_id: string | null;
  pending_examples: number;
  last_trained_feedback_count: number;
  last_trained_at: string | null;
  last_feedback_at: string | null;
  active_run_id: string | null;
  models: EmailModelEvidence[];
  registry_issues: Array<{ model_id: string; integrity_status: "corrupt"; integrity_error: string }>;
  category_thresholds: Record<string, number>;
}
export interface EmailRuntime {
  mode: "agent_primary" | "model_primary";
  active_model_id: string | null;
  candidate_model_id: string | null;
  candidate_ready: boolean;
  toggle_enabled: boolean;
}
export interface EmailPromotionConfig {
  macro_f1_min: number;
  category_precision_min: number;
  category_validation_samples_min: number;
  p95_latency_max_ms: number;
  config_version: string;
}
export interface EmailStagedModel {
  model_id: string;
  status: string;
  trained_at: string;
  metrics: {accuracy: number | null; macro_f1: number | null; categories: Record<string, Record<string, number | null>>; important?: Record<string, number | null>} | null;
  evaluation: {protocol: string; test_digest: string; comparability_key: string} | null;
  head_timing_percentiles_ms: EmailLatency | null;
  end_to_end_latency_ms: EmailLatency | null;
  compatibility?: {enabled_categories: string[]; description_version: string; [key: string]: unknown};
  split_counts?: {train: number; validation: number; test: number; [key: string]: unknown};
  integrity_status?: string;
  training?: {
    started_at: string | null; completed_at: string | null; duration_ms: number | null;
    sample_count: number | null; category_sample_count: number | null; account_count: number | null; group_count: number | null;
    new_sample_count?: number | null;
  } | null;
  parameters?: Record<string, unknown> | null;
  failure_reason?: string;
  [key: string]: unknown;
}
export interface EmailLatency {p50: number | null; p95: number | null; p99: number | null}
export interface EmailCategoryUpdate {
  core_description: string; include: string[]; exclude: string[];
  threshold: number; enabled: boolean; expected_current_version: string;
}
export interface EmailCategoryCreate extends Omit<EmailCategoryUpdate, "expected_current_version"> {
  category_key: string; display_name: string; provider_folder_name: string;
}
export interface EmailCategoryRevision {
  revision_id: number | string;
  category_key: string;
  config_version: string;
  description_version: string;
  created_at: string;
  config: Pick<EmailCategoryConfig,"core_description"|"include"|"exclude">;
}
export interface SentTodoItem { id: string; kind: string; kind_label: string; sent_at: string; status: string; owner: string; project_title: string; todo_title: string; description: string; original_text: string; deadline: string; priority: string; target: string; external_id: string; detail_url: string; }
export interface WechatScopeTarget {
  account_id?: string;
  target_type: "direct" | "group";
  target_id: string;
  display_name: string;
  trigger_mode: "every_inbound_text" | "mention_current_account";
  conversation_id: string;
  enabled?: boolean;
}

export interface WechatTargetList extends ConsoleList<WechatScopeTarget> {
  account_id: string;
}

function asRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function emailText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function mapEmailClassification(value: unknown): EmailClassificationItem {
  const row = asRecord(value);
  const confirmedCategory = emailText(row.confirmed_category);
  const predictedCategory = emailText(row.predicted_category);
  const attachmentMetadata = Array.isArray(row.attachment_metadata)
    ? row.attachment_metadata.map(asRecord).map((attachment) => ({
      filename: emailText(attachment.filename),
      mime_type: emailText(attachment.mime_type),
      size_bytes: Number(attachment.size_bytes || 0),
      inline: attachment.inline === true,
    }))
    : [];
  return {
    id: emailText(row.id),
    provider: emailText(row.provider),
    mailbox: emailText(row.mailbox || row.folder),
    message_id: emailText(row.message_id || row.rfc_message_id || row.stable_message_identity),
    thread_id: emailText(row.thread_id || row.thread_identity),
    sender: emailText(row.sender),
    subject: emailText(row.subject),
    preview: emailText(row.preview),
    message_text: emailText(row.message_text),
    quoted_text: emailText(row.quoted_text),
    important: typeof row.important === "boolean" ? row.important : null,
    provider_classification: isRecord(row.provider_classification) ? row.provider_classification as unknown as EmailProviderClassification : null,
    description_version: emailText(row.description_version),
    cc: emailText(row.cc),
    recipients: Array.isArray(row.recipients) ? row.recipients.map(emailText) : [],
    received_at: emailText(row.received_at),
    category: confirmedCategory || predictedCategory || emailText(row.category),
    confidence: Number(row.confidence || 0),
    margin: Number(row.margin || 0),
    probabilities: (isRecord(row.probabilities) ? row.probabilities : {}) as Record<string, number>,
    model_version: emailText(row.model_version || row.model_id),
    config_version: emailText(row.config_version),
    status: emailText(row.status),
    classification_source: emailText(row.classification_source),
    action_plan: asRecord(row.action_plan),
    current_action_plan_id: typeof row.current_action_plan_id === "string" && row.current_action_plan_id.trim() !== ""
      ? row.current_action_plan_id
      : null,
    attachment_metadata: attachmentMetadata,
    confirmed_at: emailText(row.confirmed_at),
    created_at: emailText(row.created_at),
    updated_at: emailText(row.updated_at),
  };
}

function resolveOwnerDisplay(project: Record<string, unknown>, todos: Array<Record<string, unknown>> = []): string {
  const directOwner = displayValue(project.owner || project.owner_name || project.owner_user_id);
  if (directOwner !== "未提供") return directOwner;
  const todoOwners: string[] = [];
  for (const todo of todos) {
    const owner = displayValue(todo.owner || todo.owner_name || todo.owner_user_id);
    if (owner === "未提供" || todoOwners.includes(owner)) continue;
    todoOwners.push(owner);
  }
  if (todoOwners.length === 1) return todoOwners[0];
  if (todoOwners.length > 1) {
    const visible = todoOwners.slice(0, 3).join("、");
    return todoOwners.length > 3 ? `多人：${visible} 等 ${todoOwners.length} 人` : `多人：${visible}`;
  }
  return "未提供";
}

function isCompletedEvidence(value: unknown): boolean {
  if (!isRecord(value)) return false;
  return ["source", "reason", "completed_at"].some((key) => typeof value[key] === "string" && value[key].trim() !== "");
}

function todoDone(todo: Record<string, unknown>): boolean {
  if (todo.done === true) return true;
  if (todo.status === "done") return true;
  if (todo.status === "cancelled") return false;
  if (isCompletedEvidence(todo.completion_evidence)) return true;
  const followUps = Array.isArray(todo.follow_ups) ? todo.follow_ups.map(asRecord) : [];
  if (followUps.some((followUp) => followUp.status === "completed")) return true;
  const dingtalkTodos = Array.isArray(todo.dingtalk_todos) ? todo.dingtalk_todos.map(asRecord) : [];
  return dingtalkTodos.some((link) => link.status === "done" || link.last_dingtalk_done === true);
}

function progressFromTodos(todos: Array<Record<string, unknown>>) {
  const total = todos.length;
  const done = todos.filter(todoDone).length;
  const ratio = total ? Math.round((done * 100) / total) : 0;
  return { done, total, ratio };
}

function mapTaskSummary(value: unknown): TaskSummary {
  const row = asRecord(value);
  const done = Number(row.progress_count || 0);
  const total = Number(row.progress_total || row.todo_count || 0);
  return {
    id: String(row.id ?? ""), title: displayValue(row.title),
    status: displayValue(row.status), category: displayValue(row.category),
    priority: displayValue(row.priority), risk: displayValue(row.risk_level),
    owner: resolveOwnerDisplay(row),
    progress: `${done}/${total}（${Number(row.progress_ratio || 0)}%）`,
    todo_count: Number(row.todo_count || 0),
    state_summary: displayValue(row.current_state),
    next_summary: displayValue(row.next_step),
  };
}

function mapTaskDetail(value: unknown): TaskDetail {
  const payload = asRecord(value);
  const project = asRecord(payload.project);
  const facts = Array.isArray(project.facts) ? project.facts : [];
  const todos = Array.isArray(payload.todos) ? payload.todos.map(asRecord) : [];
  const progress = progressFromTodos(todos);
  return {
    ...mapTaskSummary({
      id: project.id, title: project.title, status: project.status,
      category: project.category, priority: project.priority,
      risk_level: project.risk_level,
      owner: resolveOwnerDisplay(project, todos),
      progress_count: progress.done,
      progress_total: progress.total,
      progress_ratio: progress.ratio,
      todo_count: progress.total,
      current_state: project.current_state, next_step: project.next_step,
    }),
    description: displayValue(project.goal || project.description),
    background: displayValue(project.background), blocker: displayValue(project.blocker),
    follow_up_mode: displayValue(project.follow_up_mode), tags: Array.isArray(project.tags) ? project.tags.map(displayValue) : [],
    facts: facts.map((fact, index) => { const item = asRecord(fact); return { id: String(item.id ?? index), description: item.description, source: item.source, created: displayValue(item.created || item.created_at), updated: displayValue(item.updated || item.updated_at) }; }),
    todos,
    updates: Array.isArray(payload.updates) ? payload.updates.map(asRecord) : [],
    evidence_candidates: Array.isArray(payload.evidence_candidates) ? payload.evidence_candidates.map(asRecord) : [],
    memory: project.memory_context && isRecord(project.memory_context) ? [project.memory_context] : [],
    unlinked_follow_ups: Array.isArray(payload.unlinked_follow_ups) ? payload.unlinked_follow_ups.map(asRecord) : [],
  };
}

export function listTasks(params: Record<string, string | number | undefined> = {}, signal?: AbortSignal) {
  return request<unknown>(`/api/console/tasks${query(params)}`, { signal }).then((value) => {
    const page = parseConsoleList(value);
    const payload = asRecord(value);
    const filters = asRecord(payload.filters);
    const categories = Array.isArray(filters.categories) ? filters.categories.map(displayValue).filter((item) => item !== "未提供") : [];
    const taskStates = Array.isArray(filters.task_states) ? filters.task_states.map(displayValue).filter((item) => item !== "未提供") : [];
    return { ...page, items: page.items.map(mapTaskSummary), filters: { categories, task_states: taskStates } } satisfies TaskList;
  });
}

export function listSentTodos(params: Record<string, string | number | undefined> = {}, signal?: AbortSignal) {
  return request<unknown>(`/api/console/tasks/sent-todos${query(params)}`, { signal }).then((value) => parseConsoleList<SentTodoItem>(value));
}

export function getTaskDetail(projectId: string, signal?: AbortSignal) {
  return request<ConsoleResource<unknown>>(`/api/console/tasks/${encodeURIComponent(projectId)}`, { signal }).then((response) => ({ ...response, item: mapTaskDetail(response.item) }));
}

export function listHistory(params: Record<string, string | number | undefined> = {}, signal?: AbortSignal) {
  return request<unknown>(`/api/console/history${query(params)}`, { signal }).then((value) => {
    const page = parseConsoleList<HistoryItem>(value);
    const row = asRecord(value);
    return { ...page, chart: isRecord(row.chart) ? row.chart as unknown as HistoryChart : undefined };
  });
}

export function listEmailClassifications(
  status: "processed" | "pending_feedback",
  params: Record<string, string | number | undefined> = {},
  signal?: AbortSignal,
) {
  return request<unknown>(`/api/console/email/classifications${query({ status, ...params })}`, { signal }).then((value) => {
    const page = parseConsoleList<Record<string, unknown>>(value);
    return { ...page, items: page.items.map(mapEmailClassification) } satisfies ConsoleList<EmailClassificationItem>;
  });
}

export function getEmailClassification(id: string, signal?: AbortSignal) {
  return request<unknown>(`/api/console/email/classifications/${id}`, { signal }).then((value) => {
    const payload = asRecord(value);
    return {
      ok: payload.ok === true,
      item: mapEmailClassification(payload.item),
      observability: Array.isArray(payload.observability) ? payload.observability as EmailObservabilityEvent[] : [],
      provider_classification: isRecord(payload.provider_classification) ? payload.provider_classification as unknown as EmailProviderClassification : null,
      meta: asRecord(payload.meta) as { snapshot_at: string },
    } satisfies EmailClassificationDetail;
  });
}

export function confirmEmailClassification(
  id: string,
  category: string,
  feedbackRequestId: string,
  expectedCurrentActionPlanId: string | null,
) {
  return request<Record<string, unknown>>(`/api/console/email/classifications/${id}/feedback`, {
    method: "POST",
    body: JSON.stringify({
      category,
      feedback_request_id: feedbackRequestId,
      expected_current_action_plan_id: expectedCurrentActionPlanId,
    }),
  }).then((payload) => ({
    ok: payload.ok === true,
    item: mapEmailClassification(payload.item),
    message: displayValue(payload.message),
  }));
}

export function listEmailConfigs(signal?: AbortSignal) {
  return request<{ items: EmailCategoryConfig[]; meta: { snapshot_at: string } }>("/api/console/email/config", { signal });
}

export function listEmailAccounts(signal?: AbortSignal) {
  return request<{ items: EmailAccountItem[]; meta: { snapshot_at: string } }>(
    "/api/console/email/accounts",
    { signal },
  );
}

export function createEmailAccount(payload: EmailAccountPayload) {
  return request<EmailAccountSaveResult>("/api/console/email/accounts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateEmailAccount(accountId: string, payload: EmailAccountPayload) {
  return request<EmailAccountSaveResult>(
    `/api/console/email/accounts/${encodeURIComponent(accountId)}`,
    { method: "PUT", body: JSON.stringify(payload) },
  );
}

export function testEmailAccount(accountId: string) {
  return request<EmailAccountTestResult>(
    `/api/console/email/accounts/${encodeURIComponent(accountId)}/test`,
    { method: "POST" },
  );
}

export function saveEmailConfig(
  category: string,
  payload: EmailCategoryUpdate,
) {
  return request<{ ok: boolean; item: EmailCategoryConfig; message: string }>(
    `/api/console/email/config/${encodeURIComponent(category)}`,
    { method: "PUT", body: JSON.stringify(payload) },
  );
}

export function listEmailLearning(signal?: AbortSignal) {
  return request<{ ok: boolean; learning: EmailLearningEvidence; meta: { snapshot_at: string } }>(
    "/api/console/email/learning",
    { signal },
  );
}

export function createEmailCategory(payload: EmailCategoryCreate) {
  return request<{ok: boolean; item: EmailCategoryConfig; message: string}>("/api/console/email/config", {method: "POST", body: JSON.stringify(payload)});
}
export function listEmailCategoryHistory(categoryKey:string, signal?:AbortSignal) {
  return request<{ok:boolean;items:EmailCategoryRevision[]}>(`/api/console/email/config/${encodeURIComponent(categoryKey)}/history`,{signal});
}
export function getEmailModelVersion(id: string, signal?: AbortSignal) {
  return request<{ok: boolean; model: EmailStagedModel}>(`/api/console/email/model-versions/${encodeURIComponent(id)}`, {signal});
}
export function saveEmailRuntimeMode(payload: {mode: EmailRuntime["mode"]; model_id: string | null; request_id: string; expected_mode: EmailRuntime["mode"]; expected_model_id: string | null}) {
  return request<{ok: boolean; runtime: EmailRuntime}>("/api/console/email/runtime-mode", {method:"PUT",body:JSON.stringify(payload)});
}
export function saveEmailPromotionConfig(payload: Omit<EmailPromotionConfig, "config_version"> & {expected_current_version: string}) {
  return request<{ok: boolean; config: EmailPromotionConfig}>("/api/console/email/promotion-config", {method:"PUT",body:JSON.stringify(payload)});
}

export function getHistoryChart(range = "24h", signal?: AbortSignal) {
  return request<unknown>(`/api/console/history/chart${query({ range })}`, { signal }).then((value) => {
    const row = asRecord(value);
    return isRecord(row.chart) ? row.chart as unknown as HistoryChart : undefined;
  });
}

export function listAttention(signal?: AbortSignal) {
  return request<ConsoleList<Record<string, unknown>>>("/api/console/attention", { signal }).then((page) => ({
    ...page,
    items: page.items.map((value) => {
      const row = asRecord(value);
      const records = Array.isArray(row.records) ? row.records : [];
      const links = records.map((record) => {
        const item = asRecord(record);
        const detailUrl = typeof item.detail_url === "string" ? item.detail_url.trim() : "";
        if (!detailUrl) return null;
        const label = displayValue(item.category) === "Service error" ? "查看错误详情" : "查看详情";
        return { label, href: detailUrl };
      }).filter((link): link is { label: string; href: string } => Boolean(link));
      return { id: `${displayValue(row.category)}:${displayValue(row.root_cause)}:${displayValue(row.context)}`, category: displayValue(row.category), root_cause: displayValue(row.root_cause), context: displayValue(row.context), severity: displayValue(row.severity), count: Number(row.count || records.length), summary: row.summary, error: row.error, detail_label: displayValue(row.detail_label || "状态"), detail: row.detail ?? row.error, updated_at: displayValue(row.updated_at), links } satisfies AttentionItem;
    }),
  }));
}

export function listFeedback(params: Record<string, string | number | undefined> = {}, signal?: AbortSignal) {
  return request<unknown>(`/api/console/feedback${query(params)}`, { signal }).then((value) => {
    const page = parseConsoleList<FeedbackItem>(value);
    const row = asRecord(value);
    return { ...page, pending_count: typeof row.pending_count === "number" ? row.pending_count : undefined } satisfies FeedbackList;
  });
}

export function getFeedbackDetail(feedbackKey: string, signal?: AbortSignal) {
  return request<ConsoleResource<FeedbackItem>>(`/api/console/feedback/${encodeURIComponent(feedbackKey)}`, { signal });
}

export function getStatus(signal?: AbortSignal) {
  return request<{ item: Record<string, unknown>; meta: { snapshot_at: string } }>("/api/console/status", { signal });
}

export function getSettings(section: string, signal?: AbortSignal) {
  return request<ConsoleResource<Record<string, unknown>>>(`/api/console/settings/${encodeURIComponent(section)}`, { signal });
}

export interface SkillFeature {
  feature_id: string;
  name: string;
  description: string;
  skills: string[];
  enabled: boolean;
  status: string;
}
export interface ProjectSkill {
  name: string;
  description: string;
  managed_by?: string;
  path?: string;
  content?: string;
  sha256?: string;
  referenced_by: string[];
  status?: string;
  error?: string;
}
export interface SkillSettings { features: SkillFeature[]; skills: ProjectSkill[]; }
export interface SkillDetail extends ProjectSkill { content: string; sha256: string; }

export function getSkillFeatures(signal?: AbortSignal) {
  return request<SkillSettings>("/api/console/settings/skills", { signal });
}
export function toggleSkillFeature(featureId: string, enabled: boolean) {
  return request<{ feature_id: string; enabled: boolean; status: string }>(`/api/console/settings/skills/${encodeURIComponent(featureId)}/toggle`, { method: "POST", body: JSON.stringify({ enabled }) });
}
export function getSkillDetail(name: string, signal?: AbortSignal) {
  return request<SkillDetail>(`/api/console/settings/skills/${encodeURIComponent(name)}`, { signal });
}
export function saveSkill(name: string, content: string, expectedSha256: string) {
  return request<SkillDetail>(`/api/console/settings/skills/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify({ content, expected_sha256: expectedSha256 }) });
}

export function getResource(path: string, signal?: AbortSignal) {
  return request<ConsoleResource<Record<string, unknown>>>(path, { signal });
}

export function command(path: string, body: Record<string, unknown> = {}) {
  return request<{ ok: boolean; item?: unknown; message: string; meta: { updated_at: string } }>(path, { method: "POST", body: JSON.stringify(body) });
}

export function resolveFeedback(id: string) { return command(`/api/console/feedback/${encodeURIComponent(id)}/resolve`); }
export function reopenFeedback(feedbackKey: string, reason: string) {
  return command(`/api/console/feedback/items/${encodeURIComponent(feedbackKey)}/reopen`, { reason });
}
export function syncFeedback() { return command("/api/console/feedback/sync"); }
export function saveSettings(section: string, fields: Record<string, unknown>, extras: Record<string, unknown> = {}) {
  return command(`/api/console/settings/${encodeURIComponent(section)}`, { ...extras, fields });
}
export function getTutorial(signal?: AbortSignal) { return getResource("/api/console/tutorial", signal); }
export function runTutorialAction(actionId: string) { return command(`/api/console/tutorial/run/${encodeURIComponent(actionId)}`); }
export function checkTutorialStep(stepId: string) { return command(`/api/console/tutorial/check/${encodeURIComponent(stepId)}`); }
export function confirmTutorialStep(stepId: string, evidence: Record<string, unknown> = {}) { return command(`/api/console/tutorial/confirm/${encodeURIComponent(stepId)}`, { evidence }); }
export function listCodexSessions(signal?: AbortSignal) { return request<unknown>("/api/console/codex/sessions", { signal }).then(parseConsoleList); }
export function getCodexSession(id: string, signal?: AbortSignal) { return getResource(`/api/console/codex/sessions/${encodeURIComponent(id)}`, signal); }
export function listWechat(path: string, signal?: AbortSignal) { return request<unknown>(path, { signal }).then(parseConsoleList); }
export function listWechatTargets(params: Record<string, string | number | undefined> = {}, signal?: AbortSignal) {
  return request<unknown>(`/api/console/wechat/targets${query(params)}`, { signal }).then((value) => {
    const page = parseConsoleList<WechatScopeTarget>(value);
    return { ...page, account_id: isRecord(value) && typeof value.account_id === "string" ? value.account_id : "" } satisfies WechatTargetList;
  });
}
export function saveWechatReplyScope(accountId: string, targets: WechatScopeTarget[]) {
  return command("/api/console/wechat/reply-scope", { account_id: accountId, targets });
}
export function approveWechatDelivery(id: string) { return command(`/api/console/wechat/deliveries/${encodeURIComponent(id)}/approve`); }
export function rejectWechatDelivery(id: string) { return command(`/api/console/wechat/deliveries/${encodeURIComponent(id)}/reject`); }
export function reviewWechatMemory(id: string, action: "approve" | "reject" | "revoke", finalStatement = "") { return command(`/api/console/wechat/memory-review/${encodeURIComponent(id)}/${action}`, { reviewer: "local-user", final_statement: finalStatement }); }
