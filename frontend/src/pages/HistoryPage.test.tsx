import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listHistory = vi.hoisted(() => vi.fn());
vi.mock("../api/console", () => ({
  listHistory,
  displayValue: (value: unknown) => typeof value === "string" ? value : JSON.stringify(value),
}));

import { HistoryPage } from "./HistoryPage";

describe("HistoryPage", () => {
  beforeEach(() => {
    listHistory.mockResolvedValue({
      items: [{
        id: "836",
        occurred_at: "2026-08-29T00:00:00Z",
        title: "客户项目",
        type: "task",
        status: "sent",
        summary: "已经完成客户项目的任务同步。",
        actor: "Task Agent",
        detail_url: "/tasks/836",
        kind: "task",
        input: "请同步客户项目状态",
        output: "已完成同步",
        action: "task_update",
      }],
      meta: { page: 1, page_size: 20, total: 40, next_cursor: "2", has_more: true, snapshot_at: "2026-08-29T00:00:00Z" },
    });
  });

  it("preserves the legacy history workspace hierarchy", async () => {
    render(<MemoryRouter><HistoryPage /></MemoryRouter>);

    expect(await screen.findByRole("region", { name: "History workspace" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "History status filter" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "History object filter" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Recent 24 hour events" })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: /客户项目/ })).toBeInTheDocument();
    expect(screen.getByText("问")).toBeInTheDocument();
    expect(screen.getByText("答")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "分页导航" })).toBeInTheDocument();
  });
});
