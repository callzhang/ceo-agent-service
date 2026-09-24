import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ attention: vi.fn(), tasks: vi.fn(), projects: vi.fn() }));
vi.mock("../api/console", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/console")>()),
  listBusinessAttention: api.attention,
  listBusinessTasks: api.tasks,
  listBusinessProjects: api.projects,
}));
import { TasksPage } from "./TasksPage";

const meta = { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-09-24T08:00:00Z" };
const attention = { id: "7", category: "watch", business_area: "海外业务", title: "美国客户报价", why_attention: "客户等待首版报价", current_state: "负责人已接单", ceo_action: "当前无需处理", anchor_label: "美国市场", linked_task_count: 2, updated_at: "2026-09-24T08:00:00Z", detail_url: "/tasks/attention/7" };
const routine = { id: "9", title: "整理办公室绿植", stage: "formal", status: "open", commitment_status: "accepted", owner: "Avery", deadline_at: "", business_relevance: "not_relevant", anchor_labels: [], updated_at: "2026-09-24T08:00:00Z", detail_url: "/tasks/item/9" };

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
    expect(screen.queryByText("整理办公室绿植")).not.toBeInTheDocument();
    expect(api.tasks).not.toHaveBeenCalled();
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
    expect(screen.getByText("候选任务")).toBeInTheDocument();
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
});
