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

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = isRecord(payload) ? payload : {};
    throw new ConsoleApiError(response.status, typeof error.code === "string" ? error.code : "request_failed", typeof error.message === "string" ? error.message : "请求失败，请稍后重试");
  }
  return payload as T;
}

function query(params: Record<string, string | number | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== "") search.set(key, String(value));
  return search.size ? `?${search.toString()}` : "";
}

export interface TaskSummary { id: string; title: string; status: string; category: string; priority: string; risk: string; owner: string; progress: string; todo_count: number; state_summary: string; next_summary: string; }
export interface TaskDetail extends TaskSummary { description: string; background: string; blocker: string; follow_up_mode: string; tags: string[]; facts: Array<{ id: string; description: unknown; source: unknown; created: string; updated: string }>; todos: Array<Record<string, unknown>>; updates: Array<Record<string, unknown>>; memory: Array<Record<string, unknown>>; }
export interface HistoryItem { id: string; occurred_at: string; title: string; type: string; status: string; summary: unknown; actor: string; detail_url: string; }
export interface AttentionItem { id: string; category: string; root_cause: string; context: string; severity: string; count: number; summary: unknown; error: unknown; updated_at: string; links: Array<{ label: string; href: string }>; }
export interface FeedbackItem { id: string; attempt_id: string; status: string; rating: string; comment: string; context: string; created_at: string; }

function asRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function mapTaskSummary(value: unknown): TaskSummary {
  const row = asRecord(value);
  const done = Number(row.progress_count || 0);
  const total = Number(row.progress_total || row.todo_count || 0);
  return {
    id: String(row.id ?? ""), title: displayValue(row.title),
    status: displayValue(row.status), category: displayValue(row.category),
    priority: displayValue(row.priority), risk: displayValue(row.risk_level),
    owner: displayValue(row.owner_name || row.owner_user_id),
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
  return {
    ...mapTaskSummary({
      id: project.id, title: project.title, status: project.status,
      category: project.category, priority: project.priority,
      risk_level: project.risk_level, owner_name: project.owner_name,
      todo_count: Array.isArray(payload.todos) ? payload.todos.length : 0,
      current_state: project.current_state, next_step: project.next_step,
    }),
    description: displayValue(project.goal || project.description),
    background: displayValue(project.background), blocker: displayValue(project.blocker),
    follow_up_mode: displayValue(project.follow_up_mode), tags: Array.isArray(project.tags) ? project.tags.map(displayValue) : [],
    facts: facts.map((fact, index) => { const item = asRecord(fact); return { id: String(item.id ?? index), description: item.description, source: item.source, created: displayValue(item.created || item.created_at), updated: displayValue(item.updated || item.updated_at) }; }),
    todos: Array.isArray(payload.todos) ? payload.todos.map(asRecord) : [],
    updates: Array.isArray(payload.updates) ? payload.updates.map(asRecord) : [],
    memory: project.memory_context && isRecord(project.memory_context) ? [project.memory_context] : [],
  };
}

export function listTasks(params: Record<string, string | number | undefined> = {}, signal?: AbortSignal) {
  return request<unknown>(`/api/console/tasks${query(params)}`, { signal }).then((value) => {
    const page = parseConsoleList(value);
    return { ...page, items: page.items.map(mapTaskSummary) };
  });
}

export function getTaskDetail(projectId: string, signal?: AbortSignal) {
  return request<ConsoleResource<unknown>>(`/api/console/tasks/${encodeURIComponent(projectId)}`, { signal }).then((response) => ({ ...response, item: mapTaskDetail(response.item) }));
}

export function listHistory(params: Record<string, string | number | undefined> = {}, signal?: AbortSignal) {
  return request<unknown>(`/api/console/history${query(params)}`, { signal }).then((value) => parseConsoleList<HistoryItem>(value));
}

export function listAttention(signal?: AbortSignal) {
  return request<ConsoleList<Record<string, unknown>>>("/api/console/attention", { signal }).then((page) => ({
    ...page,
    items: page.items.map((value) => {
      const row = asRecord(value);
      const records = Array.isArray(row.records) ? row.records : [];
      const links = records.map((record) => {
        const item = asRecord(record);
        return { label: `查看 ${displayValue(item.id)}`, href: `/attempts/${encodeURIComponent(displayValue(item.id))}` };
      });
      return { id: `${displayValue(row.category)}:${displayValue(row.root_cause)}:${displayValue(row.context)}`, category: displayValue(row.category), root_cause: displayValue(row.root_cause), context: displayValue(row.context), severity: displayValue(row.severity), count: Number(row.count || records.length), summary: row.summary, error: row.error, updated_at: displayValue(row.updated_at), links } satisfies AttentionItem;
    }),
  }));
}

export function listFeedback(params: Record<string, string | number | undefined> = {}, signal?: AbortSignal) {
  return request<unknown>(`/api/console/feedback${query(params)}`, { signal }).then((value) => parseConsoleList<FeedbackItem>(value));
}

export function getStatus(signal?: AbortSignal) {
  return request<{ item: Record<string, unknown>; meta: { snapshot_at: string } }>("/api/console/status", { signal });
}

export function getSettings(section: string, signal?: AbortSignal) {
  return request<ConsoleResource<Record<string, unknown>>>(`/api/console/settings/${encodeURIComponent(section)}`, { signal });
}

export function getResource(path: string, signal?: AbortSignal) {
  return request<ConsoleResource<Record<string, unknown>>>(path, { signal });
}

export function command(path: string, body: Record<string, unknown> = {}) {
  return request<{ ok: boolean; item?: unknown; message: string; meta: { updated_at: string } }>(path, { method: "POST", body: JSON.stringify(body) });
}

export function resolveFeedback(id: string) { return command(`/api/console/feedback/${encodeURIComponent(id)}/resolve`); }
export function syncFeedback() { return command("/api/console/feedback/sync"); }
export function saveSettings(section: string, fields: Record<string, unknown>) { return command(`/api/console/settings/${encodeURIComponent(section)}`, { fields }); }
export function getTutorial(signal?: AbortSignal) { return getResource("/api/console/tutorial", signal); }
export function runTutorialAction(actionId: string) { return command(`/api/console/tutorial/run/${encodeURIComponent(actionId)}`); }
export function checkTutorialStep(stepId: string) { return command(`/api/console/tutorial/check/${encodeURIComponent(stepId)}`); }
export function confirmTutorialStep(stepId: string, evidence: Record<string, unknown> = {}) { return command(`/api/console/tutorial/confirm/${encodeURIComponent(stepId)}`, { evidence }); }
export function listCodexSessions(signal?: AbortSignal) { return request<unknown>("/api/console/codex/sessions", { signal }).then(parseConsoleList); }
export function getCodexSession(id: string, signal?: AbortSignal) { return getResource(`/api/console/codex/sessions/${encodeURIComponent(id)}`, signal); }
export function listWechat(path: string, signal?: AbortSignal) { return request<unknown>(path, { signal }).then(parseConsoleList); }
export function approveWechatDelivery(id: string) { return command(`/api/console/wechat/deliveries/${encodeURIComponent(id)}/approve`); }
export function rejectWechatDelivery(id: string) { return command(`/api/console/wechat/deliveries/${encodeURIComponent(id)}/reject`); }
export function reviewWechatMemory(id: string, action: "approve" | "reject" | "revoke", finalStatement = "") { return command(`/api/console/wechat/memory-review/${encodeURIComponent(id)}/${action}`, { reviewer: "local-user", final_statement: finalStatement }); }
