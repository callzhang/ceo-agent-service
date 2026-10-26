import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

const getLegacyProjectDetail = vi.hoisted(() => vi.fn<typeof import("../api/console").getLegacyProjectDetail>());
vi.mock("../api/console", async (importOriginal) => ({ ...(await importOriginal<typeof import("../api/console")>()), getLegacyProjectDetail }));
import { LegacyProjectDetailPage } from "./TaskDetailPage";

describe("LegacyProjectDetailPage", () => {
  it("shows retained historical records, provenance and receipts with canonical anchors and no actions", async () => {
    getLegacyProjectDetail.mockResolvedValue({ item: {
      id: "17", title: "历史客户交付项目", status: "active", owner: "王五",
      description: "旧项目说明", facts: [
        { id: "fact-1", description: "客户要求先做小范围试点", source: "meeting-71", created: "2026-09-01", updated: "2026-09-02" },
      ],
      source_conversations: [{ id: "conversation-1", title: "客户试点讨论", source_ref: "chat-80" }],
      todos: [{ id: 21, title: "准备试点方案", description: "整理范围和时间表", status: "done", owner_name: "王五", completion_evidence: { source_ref: "chat-81", note: "方案已经发给客户" }, follow_ups: [{ id: 41, title: "确认试点范围", question_text: "客户是否确认范围？", status: "sent", send_result_json: JSON.stringify({ ok: true, external_id: "msg-41" }) }], dingtalk_todos: [{ id: 51, work_todo_id: 21, dingtalk_task_id: "provider-51", executor_name: "王五", title_snapshot: "准备试点方案", status: "completed", last_dingtalk_payload_json: JSON.stringify({ task_id: "provider-51", title: "准备试点方案" }) }] }],
      updates: [{ id: 31, source_type: "chat", source_ref: "chat-82", summary: "客户确认先试点一个团队", changes: { current_state: "等待试点反馈" }, created_at: "2026-09-03" }],
      evidence_candidates: [{ id: 61, project_id: 17, todo_id: 21, source_type: "meeting", source_ref: "meeting-83", evidence_text: "周五前给出试点方案", reason: "任务完成依据", status: "accepted", detail_url: "/tasks/legacy-project/17#evidence-61" }],
      unlinked_follow_ups: [{ id: 42, question_text: "旧项目是否归档？", status: "sent", send_result_json: JSON.stringify({ external_id: "msg-42" }) }],
    }, meta: { snapshot_at: "2026-09-04" } });

    render(<MemoryRouter><LegacyProjectDetailPage legacyProjectId="17" /></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "历史客户交付项目" })).toBeInTheDocument();
    expect(screen.getByText("历史记录 · 非正式项目")).toBeInTheDocument();
    expect(screen.getByText("旧项目说明")).toBeInTheDocument();
    expect(screen.getByText("客户要求先做小范围试点")).toBeInTheDocument();
    expect(screen.getByText(/meeting-71/)).toBeInTheDocument();
    expect(screen.getByText("客户试点讨论")).toBeInTheDocument();
    expect(screen.getByText("查看来源会话记录").closest("details")?.querySelector("pre")).toHaveTextContent("chat-80");

    const todo = document.getElementById("todo-21");
    expect(todo).toBeInTheDocument();
    expect(within(todo as HTMLElement).getByText("准备试点方案")).toBeInTheDocument();
    expect(within(todo as HTMLElement).getByText(/记录状态（历史）：done/)).toBeInTheDocument();
    expect(document.getElementById("follow-up-41")).toBeInTheDocument();
    const unlinkedFollowUp = document.getElementById("follow-up-42");
    expect(unlinkedFollowUp).toBeInTheDocument();
    expect(unlinkedFollowUp?.closest("details")).toHaveAttribute("open");
    const evidence = document.getElementById("evidence-61");
    expect(evidence).toBeInTheDocument();
    expect(evidence?.closest("details")).toHaveAttribute("open");
    expect(screen.getByText("客户确认先试点一个团队")).toBeInTheDocument();
    expect(screen.getByText(/chat-82/)).toBeInTheDocument();
    expect(screen.getByText("周五前给出试点方案")).toBeInTheDocument();
    expect(screen.getByText("旧项目是否归档？")).toBeInTheDocument();
    expect(screen.getAllByText("查看历史发送回执")[0].closest("details")?.querySelector("pre")).toHaveTextContent("msg-41");
    expect(screen.getByText("查看钉钉待办同步回执").closest("details")?.querySelector("pre")).toHaveTextContent("provider-51");
    expect(screen.getAllByText(/记录状态（历史）/)).toHaveLength(6);
    expect(screen.queryByRole("button", { name: /发送|重发|确认|完成/ })).not.toBeInTheDocument();
  });
});
