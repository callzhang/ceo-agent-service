import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listTasks = vi.hoisted(() => vi.fn());
const listSentTodos = vi.hoisted(() => vi.fn());
vi.mock("../api/console", () => ({ listTasks, listSentTodos }));

import { TasksPage } from "./TasksPage";

describe("TasksPage", () => {
  beforeEach(() => {
    listTasks.mockResolvedValue({
      items: [{ id: "836", title: "客户项目", status: "active", category: "projects", priority: "high", risk: "low", owner: "Shawn", progress: "3/5", todo_count: 5, state_summary: "等待客户确认", next_summary: "准备下一次同步" }],
      meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" },
    });
    listSentTodos.mockResolvedValue({ items: [], meta: { page: 1, page_size: 20, total: 0, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" } });
  });

  it("renders task summaries and exposes a detail route", async () => {
    render(<MemoryRouter><TasksPage /></MemoryRouter>);

    expect(await screen.findByRole("link", { name: "查看详情 客户项目" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看详情 客户项目" })).toHaveAttribute("href", "/tasks/836");
    expect(screen.getByText("5 个 TODO")).toBeInTheDocument();
  });

  it("keeps the legacy task workspace controls and sent TODO section", async () => {
    render(<MemoryRouter><TasksPage /></MemoryRouter>);

    expect(await screen.findByRole("region", { name: "Tasks workspace" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "类型" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "状态" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "排序" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Tasks" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Sent TODOs" })).toBeInTheDocument();
  });

  it("requests real server pages for tasks and sent TODOs", async () => {
    const user = userEvent.setup();
    listTasks.mockResolvedValue({
      items: [{ id: "836", title: "客户项目", status: "active", category: "projects", priority: "high", risk: "low", owner: "Shawn", progress: "3/5", todo_count: 5, state_summary: "等待客户确认", next_summary: "准备下一次同步" }],
      meta: { page: 1, page_size: 20, total: 45, next_cursor: "2", has_more: true, snapshot_at: "2026-08-29T00:00:00Z" },
      filters: { categories: ["finance", "projects"], task_states: ["active", "completed"] },
    });
    listSentTodos.mockResolvedValue({
      items: [{ id: "sent-1", kind: "follow_up", kind_label: "Follow-up", sent_at: "2026-08-29", status: "sent", owner: "Alex", project_title: "客户项目", todo_title: "确认结果", description: "确认结果", original_text: "确认结果", deadline: "", priority: "", target: "cid", external_id: "", detail_url: "/tasks/836" }],
      meta: { page: 1, page_size: 20, total: 45, next_cursor: "2", has_more: true, snapshot_at: "2026-08-29T00:00:00Z" },
    });

    render(<MemoryRouter initialEntries={["/tasks?sort=project_asc"]}><TasksPage /></MemoryRouter>);

    expect(await screen.findByRole("link", { name: "查看详情 客户项目" })).toBeInTheDocument();
    expect(listTasks).toHaveBeenCalledWith(expect.objectContaining({ page: 1, page_size: 20, sort: "project_asc" }), expect.anything());
    expect(listSentTodos).toHaveBeenCalledWith(expect.objectContaining({ page: 1, page_size: 20 }), expect.anything());
    expect(screen.getByRole("option", { name: "finance" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "completed" })).toBeInTheDocument();

    await user.click(within(screen.getByRole("navigation", { name: "Task pages" })).getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(listTasks).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2, page_size: 20 }), expect.anything()));

    await user.click(within(screen.getByRole("navigation", { name: "Sent TODO pages" })).getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(listSentTodos).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2, page_size: 20 }), expect.anything()));
  });
});
