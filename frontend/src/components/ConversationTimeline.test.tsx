import { StrictMode } from "react";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ArtifactList } from "./ArtifactList";
import { ConfirmationCard } from "./ConfirmationCard";
import { assistantTurnKey, ConversationTimeline } from "./ConversationTimeline";
import { TurnInspector } from "./TurnInspector";
import type { Timeline, TurnStatus } from "../types";

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
  it("uses the exact stable assistant item key contract", () => {
    expect(assistantTurnKey(turn)).toBe("turn:turn-1:assistant");
  });

  it("bounds the initial virtualized mount for a 100-turn page", () => {
    const turns = Array.from({ length: 100 }, (_, index) => ({
      ...turn,
      id: `turn-${index}`,
      client_request_id: `request-${index}`,
      user_text: `message-${index}`,
    }));
    render(
      <ConversationTimeline
        timeline={{ ...timeline, turns, events: [], confirmations: [], artifacts: [] }}
        activeTurnId={null}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(document.querySelectorAll(".conversation-turn").length).toBeLessThanOrEqual(12);
  });

  it("renders ordered safe markdown, execution, confirmation and artifact blocks", () => {
    render(<ConversationTimeline timeline={timeline} activeTurnId={turn.id} onConfirm={vi.fn()} onCancel={vi.fn()} />);

    expect(screen.getByText("请生成 **报告**")).toBeInTheDocument();
    const safe = screen.getByRole("link", { name: "官网" });
    expect(safe).toHaveAttribute("href", "https://example.com/");
    expect(safe).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText("坏链接")).not.toHaveAttribute("href");
    expect(screen.getByText("const ok = true")).toBeInTheDocument();
    expect(screen.queryByText("raw")).not.toBeInTheDocument();
    expect(screen.getByText(/\/Users\/private\/secret/)).toBeInTheDocument();
    expect(screen.queryByText(/visible-secret/)).not.toBeInTheDocument();
    expect(screen.getByText("发送群消息")).toBeInTheDocument();
    expect(screen.getByText("等待执行器安全停稳")).toBeInTheDocument();
    const artifact = screen.getByRole("link", { name: /结果.txt/ });
    expect(artifact).toHaveAttribute("href", "/api/workbench/tasks/task-1/turns/turn-1/artifacts/artifact-1/download");
    expect(artifact).toHaveAttribute("target", "_blank");
  });

  it("localizes legacy execution details without exposing unknown tool names", () => {
    render(
      <ConversationTimeline
        timeline={{
          ...timeline,
          events: [{
            id: 10,
            turn_id: turn.id,
            sequence: 1,
            event_type: "tool_completed",
            payload: {
              tool: "untrusted.provider.secret_tool",
              tool_call_id: "tool-legacy",
              summary: "Tool completed",
              status: "completed",
            },
            created_at: "",
          }],
          confirmations: [],
          artifacts: [],
        }}
        activeTurnId={turn.id}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText("MCP 工具")).toBeInTheDocument();
    expect(screen.getAllByText("已完成")).toHaveLength(2);
    expect(screen.queryByText("untrusted.provider.secret_tool")).not.toBeInTheDocument();
    expect(screen.queryByText("Tool completed")).not.toBeInTheDocument();
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

  it("uses authoritative final text when persisted deltas are only a partial page", () => {
    const completed = { ...turn, status: "completed" as const, final_text: "完整的最终答案" };
    render(
      <ConversationTimeline
        timeline={{
          ...timeline,
          turns: [completed],
          events: [{ id: 20, turn_id: completed.id, sequence: 20, event_type: "text_delta", payload: { text: "不完整片段" }, created_at: "" }],
          events_has_more: true,
        }}
        activeTurnId={null}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText("完整的最终答案")).toBeInTheDocument();
    expect(screen.queryByText("不完整片段")).not.toBeInTheDocument();
  });

  it("blocks remote markdown images while preserving ordinary web links", () => {
    render(
      <ConversationTimeline
        timeline={{
          ...timeline,
          events: [{
            id: 30,
            turn_id: turn.id,
            sequence: 30,
            event_type: "text_delta",
            payload: { text: "![tracking pixel](https://tracker.example/pixel.png) [documentation](https://example.com/docs)" },
            created_at: "",
          }],
          confirmations: [],
          artifacts: [],
        }}
        activeTurnId={turn.id}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText("[图片已阻止：tracking pixel]")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "documentation" })).toHaveAttribute("href", "https://example.com/docs");
  });

  it("renders streamed deltas only for nonterminal turns", () => {
    const stopped = { ...turn, status: "stopped" as const, final_text: "停止前的完整文本" };
    render(
      <ConversationTimeline
        timeline={{
          ...timeline,
          turns: [stopped],
          events: [{ id: 21, turn_id: stopped.id, sequence: 21, event_type: "text_delta", payload: { text: "分页片段" }, created_at: "" }],
        }}
        activeTurnId={null}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText("停止前的完整文本")).toBeInTheDocument();
    expect(screen.queryByText("分页片段")).not.toBeInTheDocument();
  });

  it("shows an aborted explanation for an unmatched tool on a failed historical turn", () => {
    const failed = { ...turn, status: "failed" as const, error_detail: "执行器中断" };
    render(
      <ConversationTimeline
        timeline={{
          ...timeline,
          turns: [failed],
          events: [{ id: 40, turn_id: failed.id, sequence: 40, event_type: "tool_started", payload: { tool: "read", tool_call_id: "tool-40", summary: "读取文件" }, created_at: "" }],
          confirmations: [],
          artifacts: [],
        }}
        activeTurnId={null}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    const execution = document.querySelector(".execution-step");
    expect(execution).not.toBeNull();
    expect(within(execution as HTMLElement).getByText("已中止")).toBeInTheDocument();
    expect(within(execution as HTMLElement).getByText("任务已结束，未收到工具完成事件。")).toBeInTheDocument();
    expect(within(execution as HTMLElement).queryByText("执行中")).not.toBeInTheDocument();
  });

  it("shows local paths verbatim in a transparent failure", () => {
    const failed = {
      ...turn,
      status: "failed" as const,
      error_detail: "读取 /Users/derek/Documents/Projects/ceo-agent-service/README.md 失败",
    };
    render(
      <ConversationTimeline
        timeline={{ ...timeline, turns: [failed], events: [], confirmations: [], artifacts: [] }}
        activeTurnId={null}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "/Users/derek/Documents/Projects/ceo-agent-service/README.md",
    );
    expect(screen.queryByText(/已隐藏本地路径/)).not.toBeInTheDocument();
  });

  it("updates unmatched tool presentation when the same turn becomes terminal", () => {
    const events = [{ id: 41, turn_id: turn.id, sequence: 41, event_type: "tool_started" as const, payload: { tool: "read", tool_call_id: "tool-41", summary: "读取文件" }, created_at: "" }];
    const renderTimeline = (status: TurnStatus) => (
      <ConversationTimeline
        timeline={{ ...timeline, turns: [{ ...turn, status }], events, confirmations: [], artifacts: [] }}
        activeTurnId={turn.id}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    const { rerender } = render(renderTimeline("running"));
    const execution = () => {
      const card = document.querySelector(".execution-step");
      expect(card).not.toBeNull();
      return within(card as HTMLElement);
    };

    expect(execution().getByText("执行中")).toBeInTheDocument();
    for (const status of ["queued", "waiting_confirmation"] as const) {
      rerender(renderTimeline(status));
      expect(execution().getByText("执行中")).toBeInTheDocument();
      expect(execution().queryByText("已中止")).not.toBeInTheDocument();
    }
    for (const status of ["failed", "completed", "stopped"] as const) {
      rerender(renderTimeline(status));
      expect(execution().getByText("已中止")).toBeInTheDocument();
      expect(execution().queryByText("执行中")).not.toBeInTheDocument();
    }
  });

  it("keeps persisted terminal tool completions completed or failed", () => {
    const failed = { ...turn, id: "turn-completed-tool", status: "failed" as const, error_detail: "上游失败" };
    const stopped = { ...turn, id: "turn-failed-tool", status: "stopped" as const };
    render(
      <ConversationTimeline
        timeline={{
          ...timeline,
          turns: [failed, stopped],
          events: [
            { id: 42, turn_id: failed.id, sequence: 42, event_type: "tool_started", payload: { tool: "read", tool_call_id: "tool-42", summary: "读取文件" }, created_at: "" },
            { id: 43, turn_id: failed.id, sequence: 43, event_type: "tool_completed", payload: { tool: "read", tool_call_id: "tool-42", status: "completed", summary: "读取完成" }, created_at: "" },
            { id: 44, turn_id: stopped.id, sequence: 44, event_type: "tool_started", payload: { tool: "shell", tool_call_id: "tool-44", summary: "运行命令" }, created_at: "" },
            { id: 45, turn_id: stopped.id, sequence: 45, event_type: "tool_completed", payload: { tool: "shell", tool_call_id: "tool-44", status: "failed", summary: "命令失败" }, created_at: "" },
          ],
          confirmations: [],
          artifacts: [],
        }}
        activeTurnId={null}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    const executions = document.querySelectorAll(".execution-step");
    expect(executions).toHaveLength(2);
    expect(within(executions[0] as HTMLElement).getByText("失败")).toBeInTheDocument();
    expect(within(executions[1] as HTMLElement).getByText("已完成")).toBeInTheDocument();
    expect(screen.queryByText("已中止")).not.toBeInTheDocument();
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

  it("accepts one decision intent before proposer quiescence", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(
      <ConfirmationCard
        confirmation={{ ...timeline.confirmations[0], decision_requested: "", proposer_quiesced: false }}
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    );

    const confirm = screen.getByRole("button", { name: "确认执行" });
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("describes persisted confirm and cancel intents according to quiescence", () => {
    const { rerender } = render(
      <ConfirmationCard
        confirmation={{ ...timeline.confirmations[0], decision_requested: "confirm", proposer_quiesced: true }}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText("正在执行已确认操作")).toBeInTheDocument();
    rerender(
      <ConfirmationCard
        confirmation={{ ...timeline.confirmations[0], decision_requested: "cancel", proposer_quiesced: true }}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText("正在取消操作")).toBeInTheDocument();
  });

  it("shows persisted confirmation outcomes instead of an in-progress label", () => {
    render(
      <ConfirmationCard
        confirmation={{ ...timeline.confirmations[0], status: "executed", decision_requested: "confirm", proposer_quiesced: true }}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText("操作已执行")).toBeInTheDocument();
    expect(screen.queryByText("正在执行已确认操作")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认执行" })).toBeDisabled();
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

  it("localizes failed status and renders the latest update in local time", () => {
    vi.stubEnv("TZ", "Asia/Shanghai");
    try {
      const failedTurn = { ...turn, status: "failed" as const, error_detail: "执行失败" };
      const failedTimeline = {
        ...timeline,
        task: { ...timeline.task, state: "failed" as const, updated_at: "2026-08-13 15:14:36" },
        turns: [failedTurn],
      };
      const { rerender } = render(<TurnInspector task={failedTimeline.task} timeline={failedTimeline} capabilities={[]} stats={null} />);

      const inspector = screen.getByTestId("turn-inspector");
      expect(within(inspector).getByText("失败")).toBeInTheDocument();
      expect(within(inspector).queryByText("failed")).not.toBeInTheDocument();
      const timestamp = within(inspector).getByText(/23[:：]14[:：]36/);
      expect(timestamp.tagName).toBe("TIME");
      expect(timestamp).toHaveAttribute("dateTime", "2026-08-13T15:14:36.000Z");
      expect(timestamp).toHaveTextContent(/2026.*08.*13/);

      rerender(<TurnInspector task={{ ...failedTimeline.task, updated_at: "not-a-date" }} timeline={failedTimeline} capabilities={[]} stats={null} />);
      expect(within(inspector).getByText("时间未知")).toBeInTheDocument();
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it("reports unknown duration for invalid or reversed completed turn timestamps", () => {
    const completedTurn = {
      ...turn,
      status: "completed" as const,
      started_at: "2026-08-13 10:00:00",
      completed_at: "not-a-date",
    };
    const { rerender } = render(<TurnInspector task={timeline.task} timeline={{ ...timeline, turns: [completedTurn] }} capabilities={[]} stats={null} />);
    expect(within(screen.getByText("耗时").parentElement!).getByText("耗时未知")).toBeInTheDocument();

    rerender(<TurnInspector task={timeline.task} timeline={{ ...timeline, turns: [{ ...completedTurn, completed_at: "2026-08-13 09:59:59" }] }} capabilities={[]} stats={null} />);
    expect(within(screen.getByText("耗时").parentElement!).getByText("耗时未知")).toBeInTheDocument();
  });
});
