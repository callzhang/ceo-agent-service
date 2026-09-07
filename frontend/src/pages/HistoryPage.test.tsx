import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listHistory = vi.hoisted(() => vi.fn());
const getHistoryChart = vi.hoisted(() => vi.fn());
vi.mock("../api/console", () => ({
  listHistory,
  getHistoryChart,
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
      chart: { labels: ["00:00", "01:00"], series: [{ name: "reply", data: [1, 0] }], total: 1, range: "24h" },
    });
    getHistoryChart.mockResolvedValue({ labels: ["00:00", "01:00"], series: [{ name: "reply", data: [1, 0] }], total: 1, range: "24h" });
  });

  it("preserves the legacy history workspace hierarchy", async () => {
    render(<MemoryRouter><HistoryPage /></MemoryRouter>);

    expect(await screen.findByRole("region", { name: "History workspace" })).toBeInTheDocument();
    expect(screen.queryByText("CEO AGENT CONSOLE")).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "状态" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "对象" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Recent 24 hour events" })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: /客户项目/ })).toBeInTheDocument();
    expect(screen.getByText("问")).toBeInTheDocument();
    expect(screen.getByText("答")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "分页导航" })).toBeInTheDocument();
  });

  it("changes the event chart time range through the history query", async () => {
    const user = (await import("@testing-library/user-event")).default.setup();
    render(<MemoryRouter><HistoryPage /></MemoryRouter>);

    await user.click(await screen.findByRole("tab", { name: "1 周" }));
    expect(getHistoryChart).toHaveBeenLastCalledWith("1w", expect.anything());
  });

  it("maps the completed quick filter to the completed history statuses", async () => {
    const user = (await import("@testing-library/user-event")).default.setup();
    render(<MemoryRouter><HistoryPage /></MemoryRouter>);

    await user.click(await screen.findByRole("button", { name: "已完成" }));
    expect(listHistory).toHaveBeenLastCalledWith(expect.objectContaining({ status: "done" }), expect.anything());
  });

  it("filters the live queue by its real processing status", async () => {
    const user = (await import("@testing-library/user-event")).default.setup();
    render(<MemoryRouter><HistoryPage /></MemoryRouter>);

    await user.click(await screen.findByRole("button", { name: "执行中" }));

    expect(listHistory).toHaveBeenLastCalledWith(expect.objectContaining({ status: "processing" }), expect.anything());
  });

  it("gives immediate visual feedback while a status filter is loading", async () => {
    const user = (await import("@testing-library/user-event")).default.setup();
    let resolveRequest!: (value: unknown) => void;
    const pendingRequest = new Promise((resolve) => { resolveRequest = resolve; });
    render(<MemoryRouter><HistoryPage /></MemoryRouter>);
    expect(await screen.findByRole("article", { name: /客户项目/ })).toBeInTheDocument();
    listHistory.mockImplementationOnce(() => pendingRequest);

    await user.click(screen.getByRole("button", { name: "失败" }));

    expect(screen.getByRole("button", { name: "失败" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "失败" })).toHaveAttribute("aria-busy", "true");
    expect(screen.getByText("正在应用筛选…")).toBeInTheDocument();
    resolveRequest({ items: [], meta: { page: 1, page_size: 20, total: 0, next_cursor: "", has_more: false, snapshot_at: "" } });
  });
});
