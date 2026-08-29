import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listTasks = vi.hoisted(() => vi.fn());
vi.mock("../api/console", () => ({ listTasks }));

import { TasksPage } from "./TasksPage";

describe("TasksPage", () => {
  beforeEach(() => {
    listTasks.mockResolvedValue({
      items: [{ id: "836", title: "客户项目", status: "active", category: "projects", priority: "high", risk: "low", owner: "Shawn", progress: "3/5", todo_count: 5, state_summary: "等待客户确认", next_summary: "准备下一次同步" }],
      meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" },
    });
  });

  it("renders task summaries and exposes a detail route", async () => {
    render(<MemoryRouter><TasksPage /></MemoryRouter>);

    expect(await screen.findByText("客户项目")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看详情 客户项目" })).toHaveAttribute("href", "/tasks/836");
    expect(screen.getByText("5 个 TODO")).toBeInTheDocument();
  });
});
