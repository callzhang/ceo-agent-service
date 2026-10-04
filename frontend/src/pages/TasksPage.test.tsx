import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BusinessAttentionSummary, BusinessTaskList, BusinessTaskSummary } from "../api/console";

const api = vi.hoisted(() => ({ attention: vi.fn<typeof import("../api/console").listBusinessAttention>(), tasks: vi.fn<typeof import("../api/console").listBusinessTasks>(), projects: vi.fn<typeof import("../api/console").listBusinessProjects>(), decide: vi.fn(), confirmProject: vi.fn() }));
vi.mock("../api/console", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/console")>()),
  listBusinessAttention: api.attention,
  listBusinessTasks: api.tasks,
  listBusinessProjects: api.projects,
  decideCandidateTask: api.decide,
  confirmBusinessProjectCandidate: api.confirmProject,
}));
import { TasksPage } from "./TasksPage";

const meta = { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-09-24T08:00:00Z" };
const attention: BusinessAttentionSummary = { id: "7", category: "watch", business_area: "海外业务", title: "美国客户报价", why_attention: "客户等待首版报价", current_state: "负责人已接单", ceo_action: "当前无需处理", anchor_label: "美国市场", linked_task_count: 2, updated_at: "2026-09-24T08:00:00Z", detail_url: "/tasks/attention/7" };
const routine: BusinessTaskSummary = { id: "9", title: "整理办公室绿植", origin: "source", suggested_owner: "", suggestion_reason: "", stage: "formal", status: "open", commitment_status: "accepted", owner: "Avery", deadline_at: "", deadline_type: "", business_relevance: "not_relevant", anchor_labels: [], updated_at: "2026-09-24T08:00:00Z", detail_url: "/tasks/item/9" };

describe("TasksPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.attention.mockResolvedValue({ items: [attention], meta });
    api.tasks.mockResolvedValue({ items: [routine], meta });
    api.projects.mockResolvedValue({ items: [], candidates: [], candidate_meta: { ...meta, total: 0 }, meta: { ...meta, total: 0 } });
  });

  it("defaults to 需关注 and shows the approved attention card without unrelated routine work", async () => {
    render(<MemoryRouter initialEntries={["/tasks"]}><TasksPage /></MemoryRouter>);
    const card = await screen.findByRole("article", { name: "美国客户报价" });
    expect(screen.getByRole("link", { name: "需关注" })).toHaveAttribute("href", "/tasks");
    expect(screen.getByRole("link", { name: "全部任务" })).toHaveAttribute("href", "/tasks?view=all");
    expect(screen.getByRole("link", { name: "正式项目" })).toHaveAttribute("href", "/tasks?view=projects");
    for (const text of ["持续观察", "海外业务", "客户等待首版报价", "负责人已接单", "当前无需处理", "美国市场", "2 个关联任务"]) expect(within(card).getByText(text)).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "美国客户报价" })).toHaveAttribute("href", "/tasks/attention/7");
    expect(within(card).getByText("关注点")).toBeInTheDocument();
    expect(within(card).queryByText("你的动作")).not.toBeInTheDocument();
    expect(screen.queryByText("整理办公室绿植")).not.toBeInTheDocument();
    expect(api.tasks).not.toHaveBeenCalled();
  });

  it.each(["decision", "push"] as const)("keeps the action label for %s", async (category) => {
    api.attention.mockResolvedValue({ items: [{ ...attention, category }], meta });
    render(<MemoryRouter><TasksPage /></MemoryRouter>);
    expect(within(await screen.findByRole("article")).getByText("你的动作")).toBeInTheDocument();
  });

  it("separates formal Tasks from candidate evidence instead of making candidates the default task list", async () => {
    api.tasks.mockResolvedValue({ items: [routine], meta });
    render(<MemoryRouter initialEntries={["/tasks?view=formal"]}><TasksPage /></MemoryRouter>);
    expect(await screen.findByRole("link", { name: "整理办公室绿植" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "正式任务" })).toHaveAttribute("href", "/tasks?view=formal");
    expect(screen.getByRole("link", { name: "待确认线索" })).toHaveAttribute("href", "/tasks?view=candidates");
    expect(api.tasks).toHaveBeenCalledWith(expect.objectContaining({ stage: "formal" }), expect.anything());
  });

  it("gives candidate evidence its own view", async () => {
    const candidate: BusinessTaskSummary = { ...routine, id: "10", title: "讨论拓展方案", stage: "candidate", detail_url: "/tasks/item/10" };
    api.tasks.mockResolvedValue({ items: [candidate], meta });
    render(<MemoryRouter initialEntries={["/tasks?view=candidates"]}><TasksPage /></MemoryRouter>);
    expect(await screen.findByRole("link", { name: "讨论拓展方案" })).toBeInTheDocument();
    expect(api.tasks).toHaveBeenCalledWith(expect.objectContaining({ stage: "candidate" }), expect.anything());
  });

  it("filters the four attention categories", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/tasks"]}><TasksPage /></MemoryRouter>);
    expect(await screen.findByRole("article", { name: "美国客户报价" })).toBeInTheDocument();
    for (const label of ["仅需知晓", "持续观察", "需要决策", "需要推动"]) expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "需要决策" }));
    expect(api.attention).toHaveBeenLastCalledWith(expect.objectContaining({ category: "decision" }), expect.anything());
  });

  it("keeps unrelated tasks in 全部任务 and marks candidates as provisional", async () => {
    api.tasks.mockResolvedValue({ items: [routine, { ...routine, id: "10", title: "讨论拓展方案", stage: "candidate", detail_url: "/tasks/item/10" }], meta: { ...meta, total: 2 } });
    render(<MemoryRouter initialEntries={["/tasks?view=all"]}><TasksPage /></MemoryRouter>);
    expect(await screen.findByRole("link", { name: "整理办公室绿植" })).toHaveAttribute("href", "/tasks/item/9");
    const rows = screen.getAllByRole("listitem");
    expect(within(rows[1]).getByText("候选任务")).toBeInTheDocument();
    expect(within(rows[0]).getByText("负责人：Avery")).toBeInTheDocument();
    expect(within(rows[0]).getByText("已接受")).toBeInTheDocument();
    expect(screen.queryByText(/\bopen\b/)).not.toBeInTheDocument();
    expect(api.attention).not.toHaveBeenCalled();
  });

  it("shows provisional project candidates separately from the official count", async () => {
    api.projects.mockResolvedValue({ items: [], candidates: [{ id: "4", title: "海外渠道拓展", reason: "两项关联任务", status: "proposed", cluster_id: 2, provisional: true, confirmed_project_id: null }, { id: "5", title: "已确认候选记录", reason: "已转正式项目", status: "confirmed", cluster_id: 3, provisional: false, confirmed_project_id: 9 }], candidate_meta: { ...meta, total: 1 }, meta: { ...meta, total: 0 } });
    render(<MemoryRouter initialEntries={["/tasks?view=projects"]}><TasksPage /></MemoryRouter>);
    expect(await screen.findByText("海外渠道拓展")).toBeInTheDocument();
    expect(screen.getByText("候选项目 · 尚未确认")).toBeInTheDocument();
    expect(screen.getByText("正式项目 0 个")).toBeInTheDocument();
    expect(screen.queryByText("已确认候选记录")).not.toBeInTheDocument();
  });

  it("confirms a project candidate and refreshes the project list", async () => {
    const user = userEvent.setup();
    const candidate = { id: "4", title: "海外渠道拓展", reason: "两项关联任务", status: "proposed", cluster_id: 2, provisional: true, confirmed_project_id: null };
    api.projects.mockResolvedValue({ items: [], candidates: [candidate], candidate_meta: { ...meta, total: 1 }, meta: { ...meta, total: 0 } });
    api.confirmProject.mockResolvedValue({ ok: true, item: { candidate_id: 4, project_id: 8 }, message: "已确认", meta: { updated_at: "" } });
    render(<MemoryRouter initialEntries={["/tasks?view=projects"]}><TasksPage /></MemoryRouter>);
    await screen.findByText("海外渠道拓展");
    await user.click(screen.getByRole("button", { name: "确认正式项目" }));
    expect(api.confirmProject).toHaveBeenCalledWith("4");
    expect(api.projects).toHaveBeenCalledTimes(2);
  });

  it("pages project candidates independently from official projects", async () => {
    const user = userEvent.setup();
    const candidateMeta = { ...meta, page: 1, page_size: 20, total: 21, has_more: true, next_cursor: "candidate-page-2" };
    api.projects.mockResolvedValue({
      items: [],
      candidates: [{ id: "4", title: "海外渠道拓展", reason: "待确认", status: "proposed", cluster_id: 2, provisional: true, confirmed_project_id: null }],
      candidate_meta: candidateMeta,
      meta: { ...meta, total: 0 },
    });
    render(<MemoryRouter initialEntries={["/tasks?view=projects&page=1&candidate_page=1"]}><TasksPage /></MemoryRouter>);
    expect(await screen.findByText("海外渠道拓展")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "项目线索分页" })).toHaveTextContent("1 / 2");
    await user.click(screen.getByRole("button", { name: "项目线索下一页" }));
    await waitFor(() => expect(api.projects).toHaveBeenLastCalledWith(expect.objectContaining({ candidate_page: 2 }), expect.anything()));
  });

  it("marks an unknown candidate owner honestly without inventing a project", async () => {
    api.tasks.mockResolvedValue({ items: [{ ...routine, id: "10", title: "讨论拓展方案", stage: "candidate", owner: "", commitment_status: "none", detail_url: "/tasks/item/10" }], meta });
    render(<MemoryRouter initialEntries={["/tasks?view=all"]}><TasksPage /></MemoryRouter>);
    const row = (await screen.findByRole("link", { name: "讨论拓展方案" })).closest("li")!;
    expect(within(row).getByText("负责人：待明确")).toBeInTheDocument();
    expect(within(row).getByText("来源线索")).toBeInTheDocument();
    expect(row).not.toHaveTextContent("负责人未明确");
    expect(row).not.toHaveTextContent("暂无业务主线关联");
  });

  it("filters 全部任务 by stage and status through the API and can clear them", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/tasks?view=all"]}><TasksPage /></MemoryRouter>);
    await screen.findByRole("link", { name: "整理办公室绿植" });
    await user.selectOptions(screen.getByRole("combobox", { name: "任务阶段" }), "candidate");
    await waitFor(() => expect(api.tasks).toHaveBeenLastCalledWith(expect.objectContaining({ stage: "candidate" }), expect.anything()));
    await user.selectOptions(screen.getByRole("combobox", { name: "任务状态" }), "done");
    await waitFor(() => expect(api.tasks).toHaveBeenLastCalledWith(expect.objectContaining({ stage: "candidate", status: "done" }), expect.anything()));
    api.tasks.mockResolvedValue({ items: [], meta: { ...meta, total: 0 } });
    await user.selectOptions(screen.getByRole("combobox", { name: "任务状态" }), "waiting");
    expect(await screen.findByText("没有符合条件的任务。")).toBeInTheDocument();
    api.tasks.mockResolvedValue({ items: [routine], meta });
    await user.click(screen.getByRole("button", { name: "清除筛选" }));
    expect(await screen.findByRole("link", { name: "整理办公室绿植" })).toBeInTheDocument();
    expect(api.tasks).toHaveBeenLastCalledWith(expect.objectContaining({ stage: "", status: "" }), expect.anything());
  });

  it("offers a way on from an empty 需关注, and never draws another tab's rows while the next tab loads", async () => {
    const user = userEvent.setup();
    api.attention.mockResolvedValue({ items: [], meta: { ...meta, total: 0 } });
    let release: (value: BusinessTaskList) => void = () => undefined;
    api.tasks.mockReturnValue(new Promise((resolve) => { release = resolve; }));
    render(<MemoryRouter initialEntries={["/tasks"]}><TasksPage /></MemoryRouter>);
    expect(await screen.findByText("当前没有需要关注的事项。")).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "查看全部任务" }));
    expect(await screen.findByRole("status", { name: "正在加载" })).toBeInTheDocument();
    expect(screen.queryByText("当前没有需要关注的事项。")).not.toBeInTheDocument();
    release({ items: [routine], meta });
    expect(await screen.findByRole("link", { name: "整理办公室绿植" })).toBeInTheDocument();
  });

  it("folds project leads once official projects exist and shows the count", async () => {
    const project = { id: "1", title: "美国市场拓展", registry_source: "经营会确认", canonical_anchor_id: 1, confirmed_task_count: 2, detail_url: "/tasks/project/1", overall_owner: "张三", overall_responsibility: "销售与交付", attention_reason: "客户回款日期尚未确认", goal: "完成美国客户首轮验证", responsible_content: "销售与交付", deadline: "", current_status: "进行中", source_title: "", reporting_period: "", source_url: "", open_task_count: 1, done_task_count: 1, updated_at: "2026-10-04T08:00:00Z" };
    api.projects.mockResolvedValue({ items: [project], candidates: [{ id: "4", title: "海外渠道拓展", reason: "待确认", status: "proposed", cluster_id: 2, provisional: true, confirmed_project_id: null }], candidate_meta: { ...meta, total: 1 }, meta });
    render(<MemoryRouter initialEntries={["/tasks?view=projects"]}><TasksPage /></MemoryRouter>);
    expect(await screen.findByRole("link", { name: "美国市场拓展" })).toBeInTheDocument();
    expect(screen.getByText("待确认的项目线索").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("正式项目 1 个")).toBeInTheDocument();
    expect(screen.getByText("目标：完成美国客户首轮验证")).toBeInTheDocument();
    expect(screen.getByText("总负责人：张三")).toBeInTheDocument();
    expect(screen.getByText("整体负责事项：销售与交付")).toBeInTheDocument();
    expect(screen.getByText("总体情况：进行中")).toBeInTheDocument();
    expect(screen.getByText("为什么关注：客户回款日期尚未确认")).toBeInTheDocument();
    expect(screen.getByText(/资料更新/)).toBeInTheDocument();
    expect(screen.getByText("登记依据：经营会确认")).toBeInTheDocument();
    expect(screen.queryByText(/权威周报|DDL/)).not.toBeInTheDocument();
  });

  it("groups source leads separately from Agent suggestions without assigning their proposed owners", async () => {
    const source: BusinessTaskSummary = { ...routine, id: "10", title: "会议提到准备验收材料", stage: "candidate", owner: "", commitment_status: "none", anchor_labels: ["客户项目"] };
    const suggestion: BusinessTaskSummary = { ...source, id: "11", title: "确认客户回款时间", origin: "agent_suggestion", suggested_owner: "王五", suggestion_reason: "回款日期未知，王五负责商务与回款。", detail_url: "/tasks/item/11" };
    api.tasks.mockResolvedValue({ items: [source, suggestion], meta: { ...meta, total: 2 } });
    render(<MemoryRouter initialEntries={["/tasks?view=candidates"]}><TasksPage /></MemoryRouter>);
    const sourceGroup = await screen.findByRole("region", { name: "来源线索" });
    const suggestionGroup = screen.getByRole("region", { name: "Agent 建议" });
    expect(within(sourceGroup).getByRole("link", { name: source.title })).toBeInTheDocument();
    expect(within(sourceGroup).queryByText(suggestion.title)).not.toBeInTheDocument();
    expect(within(suggestionGroup).getByText("建议负责人：王五")).toBeInTheDocument();
    expect(within(suggestionGroup).getByText(suggestion.suggestion_reason)).toBeInTheDocument();
    expect(within(suggestionGroup).getByText("项目：客户项目")).toBeInTheDocument();
    expect(within(suggestionGroup).queryByText(/^负责人：王五$/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "自动派发" })).not.toBeInTheDocument();
  });

  it("labels requested and committed dates explicitly and never guesses an untyped deadline", async () => {
    api.tasks.mockResolvedValue({ items: [
      { ...routine, id: "21", title: "要求完成项", deadline_at: "2026-10-08", deadline_type: "requested_deadline_at" },
      { ...routine, id: "22", title: "承诺完成项", deadline_at: "2026-10-09", deadline_type: "committed_deadline_at" },
      { ...routine, id: "23", title: "未分类历史日期", deadline_at: "2026-10-10", deadline_type: "" },
    ], meta: { ...meta, total: 3 } });
    render(<MemoryRouter initialEntries={["/tasks?view=formal"]}><TasksPage /></MemoryRouter>);
    expect(await screen.findByText("要求完成日期：2026-10-08")).toBeInTheDocument();
    expect(screen.getByText("承诺完成日期：2026-10-09")).toBeInTheDocument();
    expect(screen.queryByText(/2026-10-10/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^截止 /)).not.toBeInTheDocument();
  });

  it("shows a promoted suggestion as a formal Task with its actual owner and date", async () => {
    api.tasks.mockResolvedValue({ items: [{ ...routine, id: "90", title: "已指派的回款确认", origin: "agent_suggestion", stage: "formal", owner: "赵六", suggested_owner: "", suggestion_reason: "", commitment_status: "accepted", deadline_at: "2026-10-12", deadline_type: "committed_deadline_at" }], meta });
    render(<MemoryRouter initialEntries={["/tasks?view=formal"]}><TasksPage /></MemoryRouter>);
    const row = (await screen.findByRole("link", { name: "已指派的回款确认" })).closest("li")!;
    expect(within(row).getByText("负责人：赵六")).toBeInTheDocument();
    expect(within(row).getByText("源于 Agent 建议")).toBeInTheDocument();
    expect(within(row).getByText("承诺完成日期：2026-10-12")).toBeInTheDocument();
    expect(within(row).queryByText(/^建议负责人/)).not.toBeInTheDocument();
    expect(within(row).queryByText("Agent 建议", { exact: true })).not.toBeInTheDocument();
  });

  it("shows unknown project accountability without a weekly-report fallback", async () => {
    api.projects.mockResolvedValue({ items: [{ id: "3", title: "无人员信息项目", registry_source: "", canonical_anchor_id: 3, confirmed_task_count: 0, detail_url: "/tasks/project/3", overall_owner: "", overall_responsibility: "", attention_reason: "", updated_at: "2026-10-04" }], candidates: [], candidate_meta: { ...meta, total: 0 }, meta });
    render(<MemoryRouter initialEntries={["/tasks?view=projects"]}><TasksPage /></MemoryRouter>);
    await screen.findByRole("link", { name: "无人员信息项目" });
    expect(screen.getByText("总负责人：待明确")).toBeInTheDocument();
    expect(screen.getByText("整体负责事项：待明确")).toBeInTheDocument();
    expect(screen.getByText("总体情况：待明确")).toBeInTheDocument();
    expect(screen.queryByText(/周报/)).not.toBeInTheDocument();
  });

  it("filters by owner and sort through the API", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/tasks?view=all"]}><TasksPage /></MemoryRouter>);
    await screen.findByRole("link", { name: "整理办公室绿植" });
    await user.selectOptions(screen.getByRole("combobox", { name: "负责人" }), "unassigned");
    await waitFor(() => expect(api.tasks).toHaveBeenLastCalledWith(expect.objectContaining({ owner: "unassigned" }), expect.anything()));
    await user.selectOptions(screen.getByRole("combobox", { name: "排序" }), "created");
    await waitFor(() => expect(api.tasks).toHaveBeenLastCalledWith(expect.objectContaining({ owner: "unassigned", sort: "created" }), expect.anything()));
  });

  it("lets Derek ignore a candidate and take it back, and offers no button on a formal Task", async () => {
    const user = userEvent.setup();
    const candidate: BusinessTaskSummary = { ...routine, id: "10", title: "讨论拓展方案", stage: "candidate", owner: "", commitment_status: "none", detail_url: "/tasks/item/10" };
    api.tasks.mockResolvedValue({ items: [routine, candidate], meta: { ...meta, total: 2 } });
    api.decide.mockResolvedValue({ ok: true, message: "已忽略这个候选任务", meta: { updated_at: "" } });
    render(<MemoryRouter initialEntries={["/tasks?view=all"]}><TasksPage /></MemoryRouter>);
    await screen.findByRole("link", { name: "讨论拓展方案" });
    expect(screen.queryByRole("button", { name: /整理办公室绿植/ })).not.toBeInTheDocument();
    api.tasks.mockResolvedValue({ items: [routine, { ...candidate, status: "cancelled" }], meta: { ...meta, total: 2 } });
    await user.click(screen.getByRole("button", { name: "忽略 讨论拓展方案" }));
    expect(api.decide).toHaveBeenCalledWith("10", "ignore");
    const restore = await screen.findByRole("button", { name: "恢复 讨论拓展方案" });
    expect(within(screen.getByRole("link", { name: "讨论拓展方案" }).closest("li")!).getByText("已取消")).toBeInTheDocument();
    api.decide.mockClear();
    api.tasks.mockResolvedValue({ items: [routine, candidate], meta: { ...meta, total: 2 } });
    await user.click(restore);
    expect(api.decide).toHaveBeenCalledWith("10", "restore");
    expect(await screen.findByRole("button", { name: "忽略 讨论拓展方案" })).toBeInTheDocument();
  });
});
