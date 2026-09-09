import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getCodexSession = vi.hoisted(() => vi.fn());

vi.mock("../api/console", () => ({
  displayValue: (value: unknown) => String(value || ""),
  getCodexSession,
}));

import { CodexSessionDetailPage } from "./CodexPages";

describe("CodexSessionDetailPage", () => {
  beforeEach(() => {
    getCodexSession.mockResolvedValue({
      item: {
        available: false,
        message: "本机执行记录不可用",
        events: [],
        related_attempts: [{ id: 8840, status: "needs_human" }],
      },
      meta: { snapshot_at: "2026-09-08T23:00:00Z" },
    });
  });

  it("shows related attempts instead of an empty runtime detail panel when the transcript is unavailable", async () => {
    render(<MemoryRouter initialEntries={["/codex/session-1"]}><Routes><Route path="/codex/:sessionId" element={<CodexSessionDetailPage />} /></Routes></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "本机执行记录不可用" })).toBeInTheDocument();
    expect(screen.getByText("本机的 session 文件已被清理或当前不可读取。")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Attempt #8840" })).toHaveAttribute("href", "/attempts/8840");
    expect(screen.queryByText("Runtime details")).not.toBeInTheDocument();
  });

  it("renders available session events as a readable execution timeline rather than raw JSON", async () => {
    getCodexSession.mockResolvedValueOnce({
      item: {
        available: true,
        events: [
          { timestamp: "2026-09-08T23:43:20Z", kind: "user", title: "User", body: "请核验这条回复", expanded: true },
          { timestamp: "2026-09-08T23:43:26Z", kind: "reasoning", title: "Reasoning", body: "Reasoning summary unavailable", expanded: false },
          { timestamp: "2026-09-08T23:44:13Z", kind: "assistant", title: "Assistant", body: "已完成回复并回读确认。", expanded: true },
        ],
        related_attempts: [{ id: 8841, status: "completed" }],
      },
      meta: { snapshot_at: "2026-09-08T23:45:00Z" },
    });

    render(<MemoryRouter initialEntries={["/codex/session-1"]}><Routes><Route path="/codex/:sessionId" element={<CodexSessionDetailPage />} /></Routes></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "Agent 执行过程" })).toBeInTheDocument();
    expect(screen.getByText("3 条记录")).toBeInTheDocument();
    expect(screen.getByText("请核验这条回复")).toBeInTheDocument();
    expect(screen.getByText("已完成回复并回读确认。")).toBeInTheDocument();
    expect(screen.getByText("思考摘要未保留")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Attempt #8841" })).toHaveAttribute("href", "/attempts/8841");
    expect(screen.queryByText("Runtime details")).not.toBeInTheDocument();
    expect(screen.queryByText("session-1")).not.toBeInTheDocument();
  });

  it("renders Markdown and pairs a tool call with its input and output", async () => {
    getCodexSession.mockResolvedValueOnce({
      item: {
        available: true,
        events: [
          { timestamp: "2026-09-08T23:43:20Z", kind: "assistant", title: "Assistant", body: "已读取 **项目资料**\n\n- 结论一\n- 结论二" },
          { timestamp: "2026-09-08T23:43:21Z", kind: "tool_call", title: "Tool call: functions.exec_command", body: "{\"cmd\":\"rg --files app\"}", trace: { call_id: "call-1", name: "functions.exec_command", input: "{\"cmd\":\"rg --files app\"}" } },
          { timestamp: "2026-09-08T23:43:22Z", kind: "tool_output", title: "Tool output: call-1", body: "app/codex_history.py", trace: { call_id: "call-1", output: "app/codex_history.py" } },
        ],
      },
      meta: { snapshot_at: "2026-09-08T23:45:00Z" },
    });

    render(<MemoryRouter initialEntries={["/codex/session-1"]}><Routes><Route path="/codex/:sessionId" element={<CodexSessionDetailPage />} /></Routes></MemoryRouter>);

    expect(await screen.findByText("项目资料")).toBeInTheDocument();
    expect(screen.getByText("结论一")).toBeInTheDocument();
    expect(screen.getAllByText("functions.exec_command")).toHaveLength(2);
    expect(screen.getAllByText("输入").length).toBeGreaterThan(0);
    expect(screen.getAllByText("输出").length).toBeGreaterThan(0);
    expect(screen.queryByText("Tool output: call-1")).not.toBeInTheDocument();
  });
});
