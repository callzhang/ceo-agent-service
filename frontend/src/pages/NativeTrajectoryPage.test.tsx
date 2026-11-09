import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
const getResource = vi.hoisted(() => vi.fn());
vi.mock("../api/console", () => ({ getResource, displayValue: (v: unknown) => String(v || ""), getCodexSession: vi.fn() }));
import { NativeTrajectoryPage } from "./NativeTrajectoryPage";
import { RuntimeErrorDetailPage } from "./RuntimeErrorDetailPage";

const attempt = { id: 7, runtime: "claude_cli", status: "failed", route: "claude", model: "test", url: "/runtime-attempts/7/trajectory", created_at: "now", finished_at: "now" };
function renderPage() { return render(<MemoryRouter initialEntries={[attempt.url]}><Routes><Route path="/runtime-attempts/:runtimeAttemptId/trajectory" element={<NativeTrajectoryPage />} /></Routes></MemoryRouter>); }
function response(overrides: Record<string, unknown> = {}) { return { item: { available: true, message: "", events: [{ kind: "user", body: "完整任务输入" }], has_more: false, next_cursor: null, range_start: 0, range_end: 4, read_through: 4, attempt, runtime_attempts: [attempt], ...overrides }, meta: { snapshot_at: "now" } }; }

describe("NativeTrajectoryPage", () => {
  it("loads every remaining page and preserves paired failing tool output", async () => {
    getResource.mockReset().mockResolvedValueOnce(response({ has_more: true, next_cursor: 2, read_through: 2, events: [{ kind: "tool_call", trace: { call_id: "call", name: "Bash", input: "pytest" } }] }))
      .mockResolvedValueOnce(response({ has_more: true, next_cursor: 3, read_through: 3, events: [{ kind: "tool_output", trace: { call_id: "call", output: "actual failure", status: "failed" } }] }))
      .mockResolvedValueOnce(response({ events: [{ kind: "assistant", body: "末尾输出" }] }));
    renderPage();
    const load = await screen.findByRole("button", { name: "加载剩余轨迹" });
    expect(screen.queryByText("本次运行已结束，原生记录中未收到此工具的返回。")).not.toBeInTheDocument();
    await userEvent.click(load);
    expect(await screen.findByText("末尾输出")).toBeVisible();
    expect(screen.getByText("已完整读取本次调用范围 · 4 条原生记录")).toBeVisible();
    expect(getResource.mock.calls.map(([url]) => url)).toEqual(["/api/console/runtime-attempts/7/trajectory", "/api/console/runtime-attempts/7/trajectory?after=2", "/api/console/runtime-attempts/7/trajectory?after=3"]);
    expect(screen.getByText("actual failure").closest("details")).toHaveClass("execution-failed");
  });
  it("retains loaded evidence when later pages become unavailable", async () => {
    getResource.mockReset().mockResolvedValueOnce(response({ has_more: true, next_cursor: 2 }))
      .mockResolvedValueOnce(response({ available: false, message: "原生文件不可读" }));
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "加载剩余轨迹" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("原生文件不可读");
    expect(screen.getByText("完整任务输入")).toBeVisible();
    expect(screen.queryByText(/已完整读取/)).not.toBeInTheDocument();
  });
  it("states unavailable source honestly while keeping other invocation links", async () => {
    getResource.mockReset().mockResolvedValueOnce(response({ available: false, message: "原生文件已清理" }));
    renderPage();
    expect(await screen.findByRole("heading", { name: "原生轨迹不可用" })).toBeVisible();
    expect(screen.getByRole("link", { name: "#7 · claude_cli · failed" })).toHaveAttribute("href", attempt.url);
  });
});


it("opens native trajectories for every linked Runtime from service error details", async () => {
  const entries = ["codex_cli", "claude_cli", "friday_runtime"].map((runtime, index) => ({ id: index + 1, runtime, status: "failed", url: `/runtime-attempts/${index + 1}/trajectory` }));
  getResource.mockReset().mockResolvedValue({ item: { status: "failed", summary: "source error", runtime_attempts: entries }, meta: { snapshot_at: "" } });
  render(<MemoryRouter initialEntries={["/history/errors/3"]}><Routes><Route path="/history/errors/:errorId" element={<RuntimeErrorDetailPage />} /></Routes></MemoryRouter>);
  for (const entry of entries) expect(await screen.findByRole("link", { name: `#${entry.id} · ${entry.runtime} · failed` })).toHaveAttribute("href", entry.url);
});
