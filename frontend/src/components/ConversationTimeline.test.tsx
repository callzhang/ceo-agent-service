import { StrictMode } from "react";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ArtifactList } from "./ArtifactList";
import { ConfirmationCard } from "./ConfirmationCard";
import { ConversationTimeline } from "./ConversationTimeline";
import { TurnInspector } from "./TurnInspector";
import type { Timeline } from "../types";

const turn = {
  id: "turn-1", task_id: "task-1", client_request_id: "request-1", user_text: "请生成 **报告**",
  status: "waiting_confirmation" as const, stop_requested: false, final_text: "", error_code: "", error_detail: "",
  started_at: "2026-08-13 10:00:00", completed_at: "", created_at: "2026-08-13 10:00:00", updated_at: "2026-08-13 10:00:01",
};

const timeline: Timeline = {
  task: { id: "task-1", title: "报告", runtime_kind: "codex", archived_at: "", state: "waiting_confirmation", created_at: "", updated_at: "" },
  turns: [turn],
  events: [
    { id: 1, turn_id: turn.id, sequence: 1, event_type: "text_delta", payload: { text: "[官网](https://example.com) [坏链接](javascript:alert(1))\n\n```ts\nconst ok = true\n```\n<div>raw</div>" }, created_at: "" },
    { id: 2, turn_id: turn.id, sequence: 2, event_type: "tool_started", payload: { tool: "read", tool_call_id: "tool-1", summary: "/Users/private/secret api_key=visible-secret" }, created_at: "" },
    { id: 3, turn_id: turn.id, sequence: 3, event_type: "confirmation_required", payload: { confirmation_id: "confirmation-1" }, created_at: "" },
    { id: 4, turn_id: turn.id, sequence: 4, event_type: "artifact_created", payload: { artifact_id: "artifact-1" }, created_at: "" },
  ],
  attachments: [],
  artifacts: [{ id: "artifact-1", turn_id: turn.id, label: "结果.txt", media_type: "text/plain", created_at: "", download_url: "/private/wrong" }],
  confirmations: [{
    id: "confirmation-1", turn_id: turn.id, action_kind: "send", target: "fallback", summary: "发送消息", risk: "外部可见",
    canonical_capability: "dingtalk-chat", canonical_operation: "发送群消息", canonical_targets: ["CEO 群"], status: "pending",
    decision_requested: "confirm", decision_requested_at: "", proposer_quiesced: false, created_at: "", decided_at: "",
  }],
  next_cursor: "older", has_more: true, events_has_more: true, events_next_cursor: 1,
  artifacts_has_more: false, artifacts_next_cursor: "", confirmations_has_more: false, confirmations_next_cursor: "",
  attachments_has_more: false, attachments_next_cursor: "",
};

describe("ConversationTimeline", () => {
  it("renders ordered safe markdown, execution, confirmation and artifact blocks", () => {
    render(<ConversationTimeline timeline={timeline} activeTurnId={turn.id} onConfirm={vi.fn()} onCancel={vi.fn()} />);

    expect(screen.getByText("请生成 **报告**")).toBeInTheDocument();
    const safe = screen.getByRole("link", { name: "官网" });
    expect(safe).toHaveAttribute("href", "https://example.com/");
    expect(safe).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText("坏链接")).not.toHaveAttribute("href");
    expect(screen.getByText("const ok = true")).toBeInTheDocument();
    expect(screen.queryByText("raw")).not.toBeInTheDocument();
    expect(screen.queryByText(/Users\/private/)).not.toBeInTheDocument();
    expect(screen.queryByText(/visible-secret/)).not.toBeInTheDocument();
    expect(screen.getByText("发送群消息")).toBeInTheDocument();
    expect(screen.getByText("等待执行器安全停稳")).toBeInTheDocument();
    const artifact = screen.getByRole("link", { name: /结果.txt/ });
    expect(artifact).toHaveAttribute("href", "/api/workbench/tasks/task-1/turns/turn-1/artifacts/artifact-1/download");
    expect(artifact).toHaveAttribute("target", "_blank");
  });

  it("shows every turn lifecycle state in Chinese", () => {
    const states = ["queued", "running", "waiting_confirmation", "completed", "stopped", "failed"] as const;
    const turns = states.map((status, index) => ({
      ...turn,
      id: `turn-${status}`,
      client_request_id: `request-${index}`,
      user_text: `message-${status}`,
      status,
      error_detail: status === "failed" ? "公开错误" : "",
    }));
    render(<ConversationTimeline timeline={{ ...timeline, turns, events: [], confirmations: [], artifacts: [] }} activeTurnId="turn-running" onConfirm={vi.fn()} onCancel={vi.fn()} />);

    for (const label of ["已排队", "执行中", "等待确认", "已完成", "已停止", "执行失败：公开错误"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });
});

describe("ConfirmationCard", () => {
  it("guards a decision immediately and permits retry after an unpersisted failure", async () => {
    const user = userEvent.setup();
    const confirmation = timeline.confirmations[0];
    let rejectFirst!: (error: Error) => void;
    const firstDecision = new Promise<void>((_resolve, reject) => { rejectFirst = reject; });
    const onConfirm = vi.fn().mockReturnValueOnce(firstDecision).mockResolvedValue(undefined);
    render(
      <StrictMode>
        <ConfirmationCard confirmation={{ ...confirmation, decision_requested: "", proposer_quiesced: true }} onConfirm={onConfirm} onCancel={vi.fn()} />
      </StrictMode>,
    );

    const confirm = screen.getByRole("button", { name: "确认执行" });
    await user.click(confirm);
    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(confirm).toBeDisabled();
    await act(async () => rejectFirst(new Error("offline")));
    expect(await screen.findByRole("alert")).toHaveTextContent("确认失败，请重试");
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(2);
  });
});

describe("ArtifactList", () => {
  it("never trusts a stored download URL", () => {
    render(<ArtifactList taskId="task-1" turnId="turn-1" artifacts={timeline.artifacts} />);
    expect(screen.getByRole("link")).not.toHaveAttribute("href", "/private/wrong");
  });
});

describe("TurnInspector", () => {
  it("labels page-local counts and reports truncation and unavailable runtimes", () => {
    render(<TurnInspector task={timeline.task} timeline={timeline} capabilities={[]} stats={null} />);
    const inspector = screen.getByTestId("turn-inspector");
    expect(within(inspector).getByText("当前已加载页面")).toBeInTheDocument();
    expect(within(inspector).getByText(/统计可能不完整/)).toBeInTheDocument();
    expect(within(inspector).getByText(/Codex 运行时当前不可用/)).toBeInTheDocument();
  });
});
