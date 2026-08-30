import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

const getTaskDetail = vi.hoisted(() => vi.fn());
vi.mock("../api/console", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/console")>()),
  getTaskDetail,
}));

import { TaskDetailPage } from "./TaskDetailPage";

describe("TaskDetailPage", () => {
  it("renders facts as expandable records with safe source metadata", async () => {
    getTaskDetail.mockResolvedValue({
      item: {
        id: "836", title: "客户项目", status: "active", category: "projects", priority: "high", risk: "low", owner: "Shawn", progress: "3/5", todo_count: 1,
        state_summary: "等待确认", next_summary: "准备下一次同步", description: "项目说明", background: "背景", blocker: "", follow_up_mode: "none", tags: [],
        facts: [{ id: "fact-1", description: "一段很长的事实", source: "/Users/derek/Documents/memory/source.md#sha256=abc:size=10", created: "2026-08-28", updated: "2026-08-29" }], todos: [], updates: [], memory: [],
      },
      meta: { snapshot_at: "2026-08-29T00:00:00Z" },
    });

    render(<MemoryRouter><TaskDetailPage projectId="836" /></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "客户项目" })).toBeInTheDocument();
    expect(screen.getByText("source.md")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Facts" }).closest("section")).toHaveClass("task-facts-section");
    expect(screen.getAllByRole("button", { name: "展开详情" }).length).toBeGreaterThan(0);
    expect(screen.queryByText("[object Object]")).not.toBeInTheDocument();
  });
});
