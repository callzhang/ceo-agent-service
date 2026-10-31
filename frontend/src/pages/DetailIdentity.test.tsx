import { useLayoutEffect, type ReactNode } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ attention: vi.fn(), project: vi.fn(), legacy: vi.fn(), attempt: vi.fn(), codex: vi.fn(), resource: vi.fn(), command: vi.fn(), search: vi.fn(), confirm: vi.fn(), clear: vi.fn() }));
vi.mock("../api/console", async (original) => ({ ...(await original<typeof import("../api/console")>()), getBusinessAttentionDetail: api.attention, getBusinessProjectDetail: api.project, getLegacyProjectDetail: api.legacy, getCodexSession: api.codex, getResource: api.resource, command: api.command, searchBusinessProjectCrmCustomers: api.search, confirmBusinessProjectCrmCustomer: api.confirm, clearBusinessProjectCrmCustomer: api.clear }));
vi.mock("../api/attempts", async (original) => ({ ...(await original<typeof import("../api/attempts")>()), getAttemptDetail: api.attempt }));
import { TaskAttentionDetailPage } from "./TaskAttentionDetailPage";
import { TaskProjectDetailPage } from "./TaskProjectDetailPage";
import { LegacyProjectDetailPage } from "./TaskDetailPage";
import { AttemptDetailPage } from "./AttemptDetailPage";
import { HistoryTaskPage } from "./HistoryTaskPage";
import { MeetingAttemptPage, BusinessDetailPage } from "./BusinessDetailPage";
import { RuntimeErrorDetailPage } from "./RuntimeErrorDetailPage";
import { CodexSessionDetailPage } from "./CodexPages";

const marker = (id: string) => `已保存事实 ${id}`;
function attempt(id: string) {
  return { id: Number(id), title: marker(id), type: "reply", conversation: { label: "群名", title: `会话 ${id}`, trigger_sender: "负责人" },
    status: { raw: "completed", subject: "已完成", message: "已完成", requires_decision: false, attention: { kind: "", reason: "", external_effect: "", retry_at: "" } },
    metadata: [], trigger: { title: "Trigger", text: "任务输入" }, audit_explanation: { title: "审计说明", text: "已核验" }, generated_reply: { title: "业务结果", text: marker(id) },
    feedback: { reviewer_feedback: `反馈 ${id}`, corrected_reply: "", feedback_url: `/api/console/history/${id}/feedback`, events: [] }, decision_options: [], audit_summary: "", draft_reply: "", failure_reason: "", recovery_state: "", action_pills: [], quality_warnings: [], context_only_info: "", references: [], agent_execution_record: true, revision_count: 0,
    oa: { process_instance_id: "", task_id: "", url: "", action: "", remark: "", result: {} }, calendar: { event_id: "", response_status: "", result: {} },
    actions: { can_rerun: false, can_recall: false, can_submit_feedback: true, rerun_url: "", rerun_label: "", rerun_confirmation: "", recall_url: "", feedback_url: `/api/console/history/${id}/feedback`, consumer_url: "", audit_url: "", agent_url: "", dingtalk_url: "", terminal: true, action_label: "无需操作", wechat_open_url: `/api/console/history/${id}/open-wechat` },
    tool_uses: [], email: null, agent_sessions: [], runtime_attempts: [], created_at: "now", updated_at: "now" };
}
function project(id: string) {
  return { summary: { id, title: marker(id), registry_source: "会议", canonical_anchor_id: 3, confirmed_task_count: 0, overall_owner: "", overall_responsibility: "", attention_reason: "", responsible_content: "", goal: "", deadline: "", current_status: "", source_title: "", reporting_period: "", source_url: "", open_task_count: 0, done_task_count: 0, updated_at: "now", detail_url: `/tasks/project/${id}` }, anchor: null, context: null, confirmed_tasks: [], suggestions: [], evidence_signals: [], context_revisions: [], evidence_meta: { total: 0 }, context_revision_meta: { total: 0 } };
}
function meeting(id: string) {
  return { id: Number(id), title: marker(id), status: "sent", conversation: { label: "会议", title: marker(id), subtitle: "" }, metadata: [], trigger: { title: "输入", text: "" }, audit_explanation: { title: "审查", text: "" }, generated_reply: { title: "业务结果", text: "" }, audit_summary: "", tool_uses: [], tool_events_available: false, native_available: false, runtime: {}, actions: { agent_url: "" } };
}
const cases = [
  { kind: "attention", path: "/attention/:id", url: "/attention/", mock: api.attention, item: (id: string) => ({ summary: { id, title: marker(id), category: "watch", business_area: "交付", current_state: "处理中", why_attention: "待确认", ceo_action: "关注", anchor_label: "", updated_at: "now" }, assessment: { evidence: [] }, linked_tasks: [], evidence_signals: [], events: [] }) },
  { kind: "project", path: "/project/:id", url: "/project/", mock: api.project, item: project },
  { kind: "historical project", path: "/historical/:id", url: "/historical/", mock: api.legacy, item: (id: string) => ({ id, title: marker(id), status: "active", description: "", owner: "", facts: [], todos: [], updates: [], source_conversations: [], evidence_candidates: [], unlinked_follow_ups: [] }) },
  { kind: "history task", path: "/history/:taskId", url: "/history/", mock: api.resource, item: (id: string) => ({ task: { id: Number(id), title: marker(id), status: "done", channel: "dingtalk", trigger_text: "", error: "", created_at: "", updated_at: "" }, runs: [], attempts: [] }) },
  { kind: "attempt", path: "/attempt/:attemptId", url: "/attempt/", mock: api.attempt, item: attempt },
  { kind: "meeting", path: "/meeting/:runId", url: "/meeting/", mock: api.resource, item: meeting },
  { kind: "OA approval", path: "/oa/:processInstanceId", url: "/oa/", mock: api.resource, item: (id: string) => ({ title: marker(id), status: "done", runtime: {} }) },
  { kind: "runtime error", path: "/error/:errorId", url: "/error/", mock: api.resource, item: (id: string) => ({ summary: marker(id), status: "failed", error: "读取失败", runtime: {} }) },
  { kind: "session", path: "/session/:sessionId", url: "/session/", mock: api.codex, item: (id: string) => ({ available: false, message: marker(id), events: [], related_attempts: [] }) },
];
type Case = typeof cases[number];
function response(c: Case, id: string) { return { item: c.item(id), meta: { snapshot_at: "now" } }; }
function deferred() {
  let resolve!: (value: unknown) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function DirectDetail({ kind }: { kind: string }) {
  const location = useLocation();
  const id = location.pathname.split("/").at(-1)!;
  return kind === "attention" ? <TaskAttentionDetailPage attentionId={id} /> : kind === "project" ? <TaskProjectDetailPage projectId={id} /> : <LegacyProjectDetailPage legacyProjectId={id} />;
}
function Probe({ children, commits }: { children: ReactNode; commits: string[] }) {
  const { pathname } = useLocation();
  useLayoutEffect(() => { commits.push(document.body.textContent || ""); }, [pathname, commits]);
  return children;
}
function renderCase(c: Case, commits: string[] = []) {
  const component = ["attention", "project", "historical project"].includes(c.kind) ? <DirectDetail kind={c.kind} /> : c.kind === "history task" ? <HistoryTaskPage /> : c.kind === "attempt" ? <AttemptDetailPage /> : c.kind === "meeting" ? <MeetingAttemptPage endpoint="/api/console/meeting-attempts/:id" /> : c.kind === "OA approval" ? <BusinessDetailPage kind="OA Approval" endpoint="/api/console/oa-approvals/:id" /> : c.kind === "runtime error" ? <RuntimeErrorDetailPage /> : <CodexSessionDetailPage />;
  return render(<MemoryRouter initialEntries={[c.url + "1"]}><Probe commits={commits}><Link to={c.url + "2"}>切换详情</Link><Routes><Route path={c.path} element={component} /></Routes></Probe></MemoryRouter>);
}
beforeEach(() => vi.resetAllMocks());

it.each(cases)("$kind clears old facts in the navigation commit before effects run", async (c) => {
  const next = deferred();
  c.mock.mockResolvedValueOnce(response(c, "1")).mockReturnValueOnce(next.promise);
  const commits: string[] = [];
  renderCase(c, commits);
  await screen.findByText(marker("1"));
  fireEvent.click(screen.getByRole("link", { name: "切换详情" }));
  expect(commits.at(-1)).not.toContain(marker("1"));
  expect(screen.queryByText(marker("1"))).not.toBeInTheDocument();
  await act(async () => next.resolve(response(c, "2")));
  expect(screen.getByText(marker("2"))).toBeInTheDocument();
});

it.each(cases)("$kind ignores an aborted read that completes after the new record", async (c) => {
  const old = deferred();
  c.mock.mockReturnValueOnce(old.promise).mockResolvedValueOnce(response(c, "2"));
  renderCase(c);
  const signal = c.mock.mock.calls[0][1] as AbortSignal;
  fireEvent.click(screen.getByRole("link", { name: "切换详情" }));
  await screen.findByText(marker("2"));
  expect(signal.aborted).toBe(true);
  await act(async () => old.resolve(response(c, "1")));
  expect(screen.queryByText(marker("1"))).not.toBeInTheDocument();
  expect(screen.getByText(marker("2"))).toBeInTheDocument();
});

it.each(["resolve", "reject"] as const)("project isolates CRM input and late %s from a different Project", async (outcome) => {
  const c = cases[1];
  const pending = deferred();
  api.project.mockImplementation((id: string) => Promise.resolve(response(c, id)));
  api.search.mockReturnValue(pending.promise);
  renderCase(c);
  await screen.findByText(marker("1"));
  fireEvent.change(screen.getByLabelText("按 CRM 客户名称、别名或登记名称查询"), { target: { value: "旧项目客户" } });
  fireEvent.click(screen.getByRole("button", { name: "只读查询 CRM" }));
  fireEvent.click(screen.getByRole("link", { name: "切换详情" }));
  await screen.findByText(marker("2"));
  expect(screen.getByLabelText("按 CRM 客户名称、别名或登记名称查询")).toHaveValue("");
  expect(screen.getByRole("button", { name: "只读查询 CRM" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("按 CRM 客户名称、别名或登记名称查询"), { target: { value: "新项目客户" } });
  expect(screen.getByRole("button", { name: "只读查询 CRM" })).toBeEnabled();
  const readsBefore = api.project.mock.calls.length;
  await act(async () => outcome === "resolve" ? pending.resolve({ ok: true }) : pending.reject(new Error("旧项目查询失败")));
  expect(screen.queryByText("旧项目查询失败")).not.toBeInTheDocument();
  expect(screen.queryByText("CRM 查询完成；请核对结果后手动确认关联。")).not.toBeInTheDocument();
  expect(api.project).toHaveBeenCalledTimes(readsBefore);
});

it.each(["action", "feedback"])("attempt isolates a pending $0 result and draft from another Attempt", async (operation) => {
  const c = cases[4];
  const pending = deferred();
  api.attempt.mockImplementation((id: string) => Promise.resolve(response(c, id)));
  api.command.mockReturnValue(pending.promise);
  renderCase(c);
  await screen.findByText(marker("1"));
  fireEvent.change(screen.getByLabelText("内部反馈意见"), { target: { value: "旧记录未保存草稿" } });
  fireEvent.click(screen.getByRole("button", { name: operation === "action" ? "查看微信消息" : "保存反馈" }));
  fireEvent.click(screen.getByRole("link", { name: "切换详情" }));
  await screen.findByText(marker("2"));
  expect(screen.getByLabelText("内部反馈意见")).toHaveValue("反馈 2");
  expect(screen.getByRole("button", { name: "保存反馈" })).toBeEnabled();
  await act(async () => pending.resolve({ message: "旧记录操作完成" }));
  expect(screen.queryByText("旧记录操作完成")).not.toBeInTheDocument();
  expect(screen.queryByText(marker("1"))).not.toBeInTheDocument();
  expect(screen.getByText(marker("2"))).toBeInTheDocument();
});


it("attempt drops custom decision input and submitting state on identity change", async () => {
  const c = cases[4];
  const pending = deferred();
  const decision = (id: string) => ({ ...attempt(id), status: { ...attempt(id).status, requires_decision: true }, human_decision: { candidate_id: Number(id), candidate_digest: `digest-${id}`, review_id: Number(id), reason: "需要补充事实", requested_input: "补充新事实", basis: { verified_facts: [], rule_evidence: [], quality_explanation: "", no_external_action_evidence: [], conclusion: "" }, authorization_plan: null, selection: null } });
  api.attempt.mockImplementation((id: string) => Promise.resolve({ item: decision(id), meta: { snapshot_at: "now" } }));
  api.command.mockReturnValue(pending.promise);
  renderCase(c);
  await screen.findByText(marker("1"));
  fireEvent.change(screen.getByLabelText("补充新事实"), { target: { value: "仅属于旧事项的补充" } });
  fireEvent.click(screen.getByRole("button", { name: "提交补充信息" }));
  fireEvent.click(screen.getByRole("link", { name: "切换详情" }));
  await screen.findByText(marker("2"));
  expect(screen.getByLabelText("补充新事实")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("补充新事实"), { target: { value: "新事项" } });
  expect(screen.getByRole("button", { name: "提交补充信息" })).toBeEnabled();
  await act(async () => pending.resolve({ message: "旧事项补充已提交" }));
  expect(screen.queryByText("旧事项补充已提交")).not.toBeInTheDocument();
  expect(screen.getByLabelText("补充新事实")).toHaveValue("新事项");
});


it.each(["confirm", "clear"] as const)("project isolates a pending CRM %s operation", async (operation) => {
  const c = cases[1];
  const pending = deferred();
  api.project.mockImplementation((id: string) => { const item = project(id); return Promise.resolve({ item: { ...item, summary: { ...item.summary, crm_customer_id: "crm-existing", crm_customer_candidates: [{ customer_id: "crm-next", name: "客户候选", alias: "", registered_name: "", matched_fields: [] }] } }, meta: { snapshot_at: "now" } }); });
  api[operation].mockReturnValue(pending.promise);
  renderCase(c);
  await screen.findByText(marker("1"));
  fireEvent.click(screen.getByRole("button", { name: operation === "confirm" ? "确认关联" : "解除客户关联" }));
  fireEvent.click(screen.getByRole("link", { name: "切换详情" }));
  await screen.findByText(marker("2"));
  expect(screen.getByRole("button", { name: "确认关联" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "解除客户关联" })).toBeEnabled();
  const readsBefore = api.project.mock.calls.length;
  await act(async () => pending.resolve({ ok: true }));
  expect(screen.queryByText("项目客户关联已确认。")).not.toBeInTheDocument();
  expect(screen.queryByText("项目客户关联已解除。")).not.toBeInTheDocument();
  expect(api.project).toHaveBeenCalledTimes(readsBefore);
});

it.each(cases)("$kind does not carry a previous read error into a successful detail", async (c) => {
  c.mock.mockRejectedValueOnce(new Error("旧详情加载失败")).mockResolvedValueOnce(response(c, "2"));
  renderCase(c);
  await screen.findAllByText("旧详情加载失败");
  fireEvent.click(screen.getByRole("link", { name: "切换详情" }));
  await screen.findByText(marker("2"));
  expect(screen.queryAllByText("旧详情加载失败")).toHaveLength(0);
});

it("keeps execution detail identity separate when both Attempt and role change", async () => {
  const pending = deferred();
  api.attempt.mockResolvedValueOnce({ item: attempt("1"), meta: { snapshot_at: "now" } }).mockReturnValueOnce(pending.promise);
  const commits: string[] = [];
  render(<MemoryRouter initialEntries={["/attempts/1/execution/consumer"]}><Probe commits={commits}><Link to="/attempts/2/execution/audit">切换详情</Link><Routes><Route path="/attempts/:attemptId/execution/:role" element={<AttemptDetailPage />} /></Routes></Probe></MemoryRouter>);
  await screen.findByText("会话 1");
  fireEvent.click(screen.getByRole("link", { name: "切换详情" }));
  expect(commits.at(-1)).not.toContain("会话 1");
  expect(screen.queryByText("会话 1")).not.toBeInTheDocument();
  await act(async () => pending.resolve({ item: attempt("2"), meta: { snapshot_at: "now" } }));
  expect(screen.getByRole("heading", { name: "审计过程 · Audit" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "返回 Attempt" })).toHaveAttribute("href", "/attempts/2");
});

it("does not reuse an execution payload while changing only its role view", async () => {
  const pending = deferred();
  api.attempt.mockResolvedValueOnce({ item: attempt("1"), meta: { snapshot_at: "now" } }).mockReturnValueOnce(pending.promise);
  render(<MemoryRouter initialEntries={["/attempts/1/execution/consumer"]}><Link to="/attempts/1/execution/audit">切换详情</Link><Routes><Route path="/attempts/:attemptId/execution/:role" element={<AttemptDetailPage />} /></Routes></MemoryRouter>);
  await screen.findByText("会话 1");
  fireEvent.click(screen.getByRole("link", { name: "切换详情" }));
  expect(screen.queryByText("会话 1")).not.toBeInTheDocument();
  expect(api.attempt).toHaveBeenCalledTimes(2);
  await act(async () => pending.resolve({ item: attempt("1"), meta: { snapshot_at: "now" } }));
  expect(screen.getByRole("heading", { name: "审计过程 · Audit" })).toBeInTheDocument();
});

it("isolates generic detail identity when the resource endpoint changes at the same id", async () => {
  const pending = deferred();
  api.resource.mockResolvedValueOnce({ item: { title: "资源 A" }, meta: { snapshot_at: "now" } }).mockReturnValueOnce(pending.promise);
  const view = (endpoint: string) => <MemoryRouter initialEntries={["/oa/1"]}><Routes><Route path="/oa/:processInstanceId" element={<BusinessDetailPage kind="OA Approval" endpoint={endpoint} />} /></Routes></MemoryRouter>;
  const mounted = render(view("/api/console/oa-approvals/:id"));
  await screen.findByText("资源 A");
  mounted.rerender(view("/api/console/other-resource/:id"));
  expect(screen.queryByText("资源 A")).not.toBeInTheDocument();
  await act(async () => pending.resolve({ item: { title: "资源 B" }, meta: { snapshot_at: "now" } }));
  expect(screen.getByText("资源 B")).toBeInTheDocument();
});
