import { useLayoutEffect, type ReactNode } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { Link, MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ tasks: vi.fn(), businessAttention: vi.fn(), projects: vi.fn(), history: vi.fn(), types: vi.fn(), chart: vi.fn(), attention: vi.fn(), confirm: vi.fn() }));
vi.mock("../api/console", async (original) => ({ ...(await original<typeof import("../api/console")>()), listBusinessTasks: api.tasks, listBusinessAttention: api.businessAttention, listBusinessProjects: api.projects, listHistory: api.history, listHistoryTypes: api.types, getHistoryChart: api.chart, listAttention: api.attention, confirmBusinessProjectCandidate: api.confirm }));
import { TasksPage } from "./TasksPage";
import { HistoryPage } from "./HistoryPage";
import { AttentionPanel } from "./AttentionPage";

function deferred() {
  let resolve!: (value: unknown) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const meta = { page: 1, page_size: 20, total: 41, next_cursor: "2", has_more: true, snapshot_at: "2026-10-30T10:00:00Z" };
function task(title: string) { return { id: title, title, origin: "source", stage: "formal", status: "open", commitment_status: "accepted", owner: "负责人", anchor_labels: [], updated_at: "", detail_url: "/tasks/item/1" }; }
function result(kind: string, title: string, total = 41) {
  const item = kind === "attention" ? { id: title, title, category: "watch", business_area: "业务", why_attention: "待确认", current_state: "处理中", ceo_action: "关注", anchor_label: "", linked_task_count: 1, updated_at: "", detail_url: "/tasks/attention/1" }
    : kind === "history" ? { id: title, title, type: "task", kind: "task", status: "done", occurred_at: "", detail_url: "/history/tasks/1" }
    : kind === "projects" ? { id: title, title, confirmed_task_count: 0, detail_url: "/tasks/project/1", updated_at: "" } : task(title);
  return { items: [item], meta: { ...meta, total }, candidates: [], candidate_meta: { ...meta, total: 0 } };
}
function Probe({ children, commits }: { children: ReactNode; commits: string[] }) {
  const { search } = useLocation();
  useLayoutEffect(() => { commits.push(document.body.textContent || ""); }, [search, commits]);
  return children;
}
const cases = [
  { kind: "tasks", from: "/tasks?view=all", to: "/tasks?view=all&q=new", mock: api.tasks },
  { kind: "tasks", from: "/tasks?view=all&page=2", to: "/tasks?view=all&status=done", mock: api.tasks },
  { kind: "tasks", from: "/tasks?view=all", to: "/tasks?view=all&owner=assigned", mock: api.tasks },
  { kind: "tasks", from: "/tasks?view=all", to: "/tasks?view=all&stage=candidate", mock: api.tasks },
  { kind: "tasks", from: "/tasks?view=all", to: "/tasks?view=all&sort=created", mock: api.tasks },
  { kind: "tasks", from: "/tasks?view=all", to: "/tasks?view=all&page=2", mock: api.tasks },
  { kind: "attention", from: "/tasks", to: "/tasks?category=decision", mock: api.businessAttention },
  { kind: "projects", from: "/tasks?view=projects", to: "/tasks?view=projects&q=new", mock: api.projects },
  { kind: "projects", from: "/tasks?view=projects", to: "/tasks?view=projects&candidate_page=2", mock: api.projects },
  { kind: "history", from: "/history", to: "/history?q=new", mock: api.history },
  { kind: "history", from: "/history?page=2", to: "/history?status=failed", mock: api.history },
  { kind: "history", from: "/history", to: "/history?object_type=task", mock: api.history },
  { kind: "history", from: "/history", to: "/history?page=2", mock: api.history },
  { kind: "history", from: "/history", to: "/history?page_size=50", mock: api.history },
];
type Case = typeof cases[number];
function mount(c: Case, commits: string[] = []) {
  return render(<MemoryRouter initialEntries={[c.from]}><Probe commits={commits}><Link to={c.to}>切换查询</Link>{c.kind === "history" ? <HistoryPage embedded /> : <TasksPage />}</Probe></MemoryRouter>);
}
beforeEach(() => {
  vi.resetAllMocks();
  api.types.mockResolvedValue([{ value: "task", label: "Task" }]);
  api.chart.mockResolvedValue({ labels: [], series: [], total: 0, range: "24h" });
  api.attention.mockResolvedValue({ items: [], meta });
});
afterEach(() => vi.useRealTimers());

it.each(cases)("$to clears old rows/count/pagination in the query commit", async (c) => {
  const next = deferred();
  c.mock.mockResolvedValueOnce(result(c.kind, "旧查询事实")).mockReturnValueOnce(next.promise);
  const commits: string[] = [];
  mount(c, commits);
  await screen.findByText("旧查询事实");
  fireEvent.click(screen.getByRole("link", { name: "切换查询" }));
  expect(commits.at(-1)).not.toContain("旧查询事实");
  expect(commits.at(-1)).not.toContain("41");
  expect(screen.queryByText("旧查询事实")).not.toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: c.kind === "history" ? "分页导航" : "Tasks 分页" })).not.toBeInTheDocument();
  await act(async () => next.resolve(result(c.kind, "当前查询事实", 1)));
  expect(screen.getByText("当前查询事实")).toBeInTheDocument();
});

it.each(cases)("$to ignores late success from the obsolete query", async (c) => {
  const old = deferred();
  c.mock.mockReturnValueOnce(old.promise).mockResolvedValueOnce(result(c.kind, "当前查询事实", 1));
  mount(c);
  const signal = c.mock.mock.calls[0][1] as AbortSignal;
  fireEvent.click(screen.getByRole("link", { name: "切换查询" }));
  await screen.findByText("当前查询事实");
  expect(signal.aborted).toBe(true);
  await act(async () => old.resolve(result(c.kind, "过时响应")));
  expect(screen.queryByText("过时响应")).not.toBeInTheDocument();
  expect(screen.getByText("当前查询事实")).toBeInTheDocument();
});

it.each([cases[0], cases[9]])("$kind clears an old error in the navigation commit", async (c) => {
  c.mock.mockRejectedValueOnce(new Error("旧查询错误")).mockReturnValueOnce(deferred().promise);
  const commits: string[] = [];
  mount(c, commits);
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("link", { name: "切换查询" }));
  expect(commits.at(-1)).not.toContain("旧查询错误");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("keeps search focus while typing and resets page without flashing previous matches", async () => {
  api.tasks.mockResolvedValueOnce(result("tasks", "旧查询事实")).mockReturnValue(deferred().promise);
  render(<MemoryRouter initialEntries={["/tasks?view=all&page=2"]}><TasksPage /></MemoryRouter>);
  await screen.findByText("旧查询事实");
  const input = screen.getByRole("searchbox", { name: "搜索任务" });
  input.focus();
  fireEvent.change(input, { target: { value: "new" } });
  expect(input).toHaveFocus();
  expect(screen.queryByText("旧查询事实")).not.toBeInTheDocument();
  expect(api.tasks).toHaveBeenLastCalledWith(expect.objectContaining({ q: "new", page: 1 }), expect.anything());
});

it("does not label the previous chart as the new range while loading", async () => {
  api.history.mockResolvedValue(result("history", "历史事实"));
  api.chart.mockResolvedValueOnce({ labels: ["old"], series: [{ name: "task", data: [7] }], total: 7, range: "24h" }).mockReturnValueOnce(deferred().promise);
  render(<MemoryRouter><HistoryPage embedded /></MemoryRouter>);
  await screen.findByText("7 events");
  fireEvent.click(screen.getByRole("tab", { name: "1 周" }));
  expect(screen.queryByText("7 events")).not.toBeInTheDocument();
  expect(screen.getByText("最近 1 周事件")).toBeInTheDocument();
});

function attention(title: string, count = 1) {
  return { items: [{ id: title, category: "Service error", root_cause: title, severity: "error", count, summary: title, detail: "详情", updated_at: "", links: [] }], meta };
}
it("Attention lets a slow poll settle instead of starting overlapping polls", async () => {
  vi.useFakeTimers();
  const slow = deferred();
  api.attention.mockReturnValueOnce(slow.promise);
  render(<MemoryRouter><AttentionPanel /></MemoryRouter>);
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  expect(api.attention).toHaveBeenCalledTimes(1);
  await act(async () => slow.resolve(attention("最新问题")));
  expect(screen.getByText("最新问题", { selector: "strong" })).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(api.attention).toHaveBeenCalledTimes(2);
});
it.each(["resolve", "reject"] as const)("Attention ignores superseded refresh %s including the count callback", async (outcome) => {
  const old = deferred();
  const counts = vi.fn();
  api.attention.mockReturnValueOnce(old.promise).mockResolvedValueOnce(attention("最新问题", 2));
  const page = render(<MemoryRouter><AttentionPanel refreshKey={1} onCountChange={counts} /></MemoryRouter>);
  const signal = api.attention.mock.calls[0][0] as AbortSignal | undefined;
  page.rerender(<MemoryRouter><AttentionPanel refreshKey={2} onCountChange={counts} /></MemoryRouter>);
  await screen.findByText("最新问题", { selector: "strong" });
  await act(async () => outcome === "resolve" ? old.resolve(attention("旧问题", 9)) : old.reject(new Error("旧刷新错误")));
  expect(screen.queryByText("旧问题")).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(counts).toHaveBeenCalledTimes(1);
  expect(counts).toHaveBeenLastCalledWith(2);
  expect(signal?.aborted).toBe(true);
});
it("Attention aborts unmounted reads and does not publish a late count", async () => {
  const pending = deferred();
  api.attention.mockReturnValueOnce(pending.promise);
  const counts = vi.fn();
  const page = render(<MemoryRouter><AttentionPanel onCountChange={counts} /></MemoryRouter>);
  const signal = api.attention.mock.calls[0][0] as AbortSignal | undefined;
  page.unmount();
  await act(async () => pending.resolve(attention("旧问题")));
  expect(counts).not.toHaveBeenCalled();
  expect(signal?.aborted).toBe(true);
});
it("Attention forgets expansion when a problem disappears from a completed snapshot", async () => {
  api.attention.mockResolvedValueOnce(attention("同一问题")).mockResolvedValueOnce({ items: [], meta }).mockResolvedValueOnce(attention("同一问题"));
  const page = render(<MemoryRouter><AttentionPanel refreshKey={1} /></MemoryRouter>);
  await screen.findByText("同一问题", { selector: "strong" });
  fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
  expect(screen.getByRole("button", { name: "收起详情" })).toBeInTheDocument();
  page.rerender(<MemoryRouter><AttentionPanel refreshKey={2} /></MemoryRouter>);
  await screen.findByText("当前没有待处理问题");
  page.rerender(<MemoryRouter><AttentionPanel refreshKey={3} /></MemoryRouter>);
  await screen.findByText("同一问题", { selector: "strong" });
  expect(screen.getByRole("button", { name: "查看详情" })).toHaveAttribute("aria-expanded", "false");
});

it.each([cases[0], cases[6], cases[9]])("$kind ignores an obsolete query rejection after current success", async (c) => {
  const old = deferred();
  c.mock.mockReturnValueOnce(old.promise).mockResolvedValueOnce(result(c.kind, "当前查询事实", 1));
  mount(c);
  fireEvent.click(screen.getByRole("link", { name: "切换查询" }));
  await screen.findByText("当前查询事实");
  await act(async () => old.reject(new Error("旧请求失败")));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByText("当前查询事实")).toBeInTheDocument();
});
it("History retains the same-query snapshot while polling without refetching the chart", async () => {
  vi.useFakeTimers();
  const next = deferred();
  api.history.mockResolvedValueOnce(result("history", "同查询事实")).mockReturnValueOnce(next.promise);
  render(<MemoryRouter><HistoryPage embedded /></MemoryRouter>);
  await act(async () => {});
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(screen.getByText("同查询事实")).toBeInTheDocument();
  expect(screen.getAllByText("共 41 条")).toHaveLength(2);
  expect(api.chart).toHaveBeenCalledTimes(1);
  await act(async () => next.resolve(result("history", "更新事实", 2)));
  expect(screen.queryByText("同查询事实")).not.toBeInTheDocument();
  expect(screen.getByText("共 2 条")).toBeInTheDocument();
});
it("Attention retains the current snapshot during a manual refresh and reports a real refresh failure", async () => {
  const next = deferred();
  api.attention.mockResolvedValueOnce(attention("已有问题")).mockReturnValueOnce(next.promise);
  render(<MemoryRouter><AttentionPanel /></MemoryRouter>);
  await screen.findByText("已有问题", { selector: "strong" });
  fireEvent.click(screen.getByRole("button", { name: "刷新" }));
  expect(screen.getByText("已有问题", { selector: "strong" })).toBeInTheDocument();
  await act(async () => next.reject(new Error("当前刷新失败")));
  expect(screen.getByRole("alert")).toHaveTextContent("当前刷新失败");
});
it("History preserves search focus and resets pagination through the real controls", async () => {
  api.history.mockResolvedValueOnce(result("history", "旧查询事实")).mockReturnValue(deferred().promise);
  render(<MemoryRouter initialEntries={["/history?page=2"]}><HistoryPage embedded /></MemoryRouter>);
  await screen.findByText("旧查询事实");
  const input = screen.getByRole("searchbox", { name: "搜索历史" });
  input.focus();
  fireEvent.change(input, { target: { value: "new" } });
  expect(input).toHaveFocus();
  expect(api.history).toHaveBeenLastCalledWith(expect.objectContaining({ q: "new", page: 1 }), expect.anything());
  expect(screen.queryByText("旧查询事实")).not.toBeInTheDocument();
  expect(screen.queryByText("共 41 条")).not.toBeInTheDocument();
});
it.each([cases[5], cases[12]])("$kind pagination control never relabels old rows as the next page", async (c) => {
  c.mock.mockResolvedValueOnce(result(c.kind, "旧查询事实")).mockReturnValueOnce(deferred().promise);
  mount(c);
  await screen.findByText("旧查询事实");
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  expect(c.mock).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }), expect.anything());
  expect(screen.queryByText("旧查询事实")).not.toBeInTheDocument();
});
it("History chart ignores a late response for a superseded range without clearing current list rows", async () => {
  const old = deferred();
  api.history.mockResolvedValue(result("history", "保留列表事实"));
  api.chart.mockReturnValueOnce(old.promise).mockResolvedValueOnce({ labels: ["new"], series: [{ name: "task", data: [2] }], total: 2, range: "1w" });
  render(<MemoryRouter><HistoryPage embedded /></MemoryRouter>);
  await screen.findByText("保留列表事实");
  const signal = api.chart.mock.calls[0][1] as AbortSignal;
  fireEvent.click(screen.getByRole("tab", { name: "1 周" }));
  await screen.findByText("2 events");
  await act(async () => old.resolve({ labels: ["old"], series: [{ name: "task", data: [7] }], total: 7, range: "24h" }));
  expect(signal.aborted).toBe(true);
  expect(screen.queryByText("7 events")).not.toBeInTheDocument();
  expect(screen.getByText("2 events")).toBeInTheDocument();
  expect(screen.getByText("保留列表事实")).toBeInTheDocument();
  expect(api.history).toHaveBeenCalledTimes(1);
});
it("History shows a current chart request failure without claiming an empty event range", async () => {
  api.history.mockResolvedValue(result("history", "保留列表事实"));
  api.chart.mockRejectedValueOnce(new Error("图表读取失败"));
  render(<MemoryRouter><HistoryPage embedded /></MemoryRouter>);
  await screen.findByText("保留列表事实");
  expect(screen.getByRole("alert")).toHaveTextContent("图表读取失败");
  expect(screen.queryByText("暂无事件")).not.toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "1 周" })).toBeInTheDocument();
});
it("a project confirmation completed after navigation does not restart the new list read", async () => {
  const confirmation = deferred();
  const next = deferred();
  api.confirm.mockReturnValueOnce(confirmation.promise);
  api.projects.mockResolvedValueOnce({ ...result("projects", "旧查询事实"), candidates: [{ id: "4", title: "候选项目", reason: "已有线索", status: "proposed", cluster_id: 2, provisional: true, confirmed_project_id: null }], candidate_meta: { ...meta, total: 1 } }).mockReturnValue(next.promise);
  mount(cases[7]);
  await screen.findByText("候选项目");
  fireEvent.click(screen.getByRole("button", { name: "确认正式项目" }));
  expect(api.confirm).toHaveBeenCalledWith("4");
  fireEvent.click(screen.getByRole("link", { name: "切换查询" }));
  const signal = api.projects.mock.calls[1][1] as AbortSignal;
  await act(async () => confirmation.resolve({ ok: true }));
  expect(api.projects).toHaveBeenCalledTimes(2);
  expect(signal.aborted).toBe(false);
  await act(async () => next.resolve(result("projects", "当前查询事实", 1)));
  expect(screen.getByText("当前查询事实")).toBeInTheDocument();
});
