import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getResource = vi.hoisted(() => vi.fn());
const command = vi.hoisted(() => vi.fn());
vi.mock("../api/console", () => ({
  command,
  displayValue: (value: unknown) => typeof value === "string" ? value || "未提供" : JSON.stringify(value),
  getResource,
}));

import { BusinessDetailPage } from "./BusinessDetailPage";

function renderDetail(kind: string, endpoint: string, path: string, initialEntry: string, attemptActions = false) {
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path={path} element={<BusinessDetailPage kind={kind} endpoint={endpoint} attemptActions={attemptActions} />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("BusinessDetailPage", () => {
  beforeEach(() => {
    getResource.mockResolvedValue({
      item: {
        status: "done", title: "Business result", runtime: {},
        conversation: { label: "会议", title: "Business result", subtitle: "" },
        metadata: [], trigger: { title: "Trigger", text: "" },
        audit_explanation: { title: "Codex reason", text: "" },
        generated_reply: { title: "生成回复", text: "" }, audit_summary: "",
        tool_uses: [], actions: { agent_url: "" }, id: 1,
      },
      meta: { snapshot_at: "2026-08-30T08:00:00Z" },
    });
    command.mockReset();
  });

  it("shows Attempt commands on an Attempt detail", async () => {
    renderDetail("Attempt", "/api/console/history/:id", "/attempts/:attemptId", "/attempts/8337", true);

    expect(await screen.findByText("Business result")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重跑" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交反馈" })).toBeInTheDocument();
  });

  it.each([
    ["Meeting Attempt", "/api/console/meeting-attempts/:id", "/meeting-attempts/:runId", "/meeting-attempts/1957"],
    ["OA Approval", "/api/console/oa-approvals/:id", "/oa-approvals/:processInstanceId", "/oa-approvals/process-1"],
  ])("does not expose Attempt commands on %s details", async (kind, endpoint, path, initialEntry) => {
    renderDetail(kind, endpoint, path, initialEntry);

    expect(await screen.findByText("Business result")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重跑" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "提交反馈" })).not.toBeInTheDocument();
  });

  it("renders Meeting Attempt in the legacy business reading order", async () => {
    getResource.mockResolvedValueOnce({
      item: {
        id: 1974,
        title: "Friday Beta 产品发布",
        status: "sent",
        conversation: { label: "会议", title: "Friday Beta 产品发布", subtitle: "参会人：张毅倜(ET), 磊哥" },
        metadata: [
          { label: "meeting id", value: "meeting-1974" },
          { label: "action", value: "send" },
          { label: "status", value: "sent" },
        ],
        trigger: { title: "Trigger", text: "title: Friday Beta 产品发布" },
        audit_explanation: { title: "Codex reason", text: "会议已完成对齐分析。" },
        generated_reply: { title: "生成回复", text: "今天发布 Beta。" },
        audit_summary: "会议存在未决验收问题。",
        tool_uses: [{ title: "读取会议记忆", tool: "memory_recall", call_id: "call-1", relevance: "确认历史判断", source: "memory.md", format: "mcp/json", args: { query: "上线范围" }, output: "{\"summary\":\"风险预算需要确认\"}" }],
        runtime: { run_status: "ready_to_send", job_status: "sent" },
        actions: { agent_url: "", dingtalk_url: "/open-dingtalk-popup?conversation_id=cid-meeting" },
      },
      meta: { snapshot_at: "2026-08-30T08:00:00Z" },
    });

    renderDetail("Meeting Attempt", "/api/console/meeting-attempts/:id", "/meeting-attempts/:runId", "/meeting-attempts/1974");

    expect(await screen.findByText("会议")).toBeInTheDocument();
    expect(screen.getByText("Friday Beta 产品发布")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Trigger" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Codex reason" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "生成回复" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Audit summary" })).toBeInTheDocument();
    expect(screen.getByText("Tool uses")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看钉钉消息" })).toHaveAttribute("href", "/open-dingtalk-popup?conversation_id=cid-meeting");
    expect(screen.getByText("读取会议记忆")).toBeInTheDocument();
    expect(screen.getByText("relevance")).toBeInTheDocument();
    expect(screen.getByText("args")).toBeInTheDocument();
    expect(screen.getByText("query")).toBeInTheDocument();
    expect(screen.getByText("上线范围")).toBeInTheDocument();
    expect(screen.getByText("output")).toBeInTheDocument();
    expect(screen.queryByText("input")).not.toBeInTheDocument();
  });
});
