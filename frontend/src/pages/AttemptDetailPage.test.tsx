import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getAttemptDetail = vi.hoisted(() => vi.fn());
const command = vi.hoisted(() => vi.fn());

vi.mock("../api/attempts", () => ({ getAttemptDetail }));
vi.mock("../api/console", () => ({
  command,
  displayValue: (value: unknown) => typeof value === "string" ? value || "未提供" : JSON.stringify(value),
}));

import { AttemptDetailPage } from "./AttemptDetailPage";

const detail = {
  id: 8448,
  title: "吴柯欣",
  type: "reply",
  conversation: { label: "群名", title: "吴柯欣", trigger_sender: "Derek" },
  status: { raw: "completed", subject: "请跟进", message: "这条事项已完成，无需你操作。", requires_decision: false, attention: { kind: "", reason: "", external_effect: "", retry_at: "" } },
  metadata: [
    { label: "trigger message id", value: "msg-1" },
    { label: "action", value: "reply" },
  ],
  trigger: { title: "Trigger", text: "Derek: 请跟进" },
  audit_explanation: { title: "审计说明", text: "已核验背景" },
  generated_reply: { title: "生成回复", text: "已完成跟进" },
  feedback: { reviewer_feedback: "需要更具体", corrected_reply: "建议回复", feedback_url: "/api/console/history/8448/feedback", events: [] },
  decision_options: [],
  audit_summary: "审计摘要",
  draft_reply: "原始草稿",
  failure_reason: "",
  recovery_state: "",
  action_pills: [{ label: "💬 Completed", status: "completed" }],
  quality_warnings: [],
  context_only_info: "",
  tool_uses: [],
  agent_execution_record: true,
  revision_count: 0,
  oa: { process_instance_id: "", task_id: "", url: "", action: "", remark: "", result: {} },
  calendar: { event_id: "", response_status: "", result: {} },
  actions: {
    can_rerun: false,
    can_recall: false,
    can_submit_feedback: true,
    rerun_url: "/api/console/history/8448/rerun",
    recall_url: "/api/console/history/8448/recall",
    feedback_url: "/api/console/history/8448/feedback",
    consumer_url: "/attempts/8448/execution/consumer",
    audit_url: "/attempts/8448/execution/audit",
    dingtalk_url: "",
    terminal: true,
    action_label: "无需操作",
  },
  runtime_attempts: [{ route: "consumer", runtime: "codex", credential_mode: "configured", model: "qwen", session_available: true, status: "completed", failure_code: "", failover_permitted: false, transcript_start: 1, transcript_end: 2, effect_started_at: "" }],
  created_at: "2026-08-29T10:00:00Z",
  updated_at: "2026-08-29T10:01:00Z",
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/attempts/8448"]}>
      <Routes>
        <Route path="/attempts/:attemptId" element={<AttemptDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AttemptDetailPage", () => {
  beforeEach(() => {
    getAttemptDetail.mockReset();
    command.mockReset();
    getAttemptDetail.mockResolvedValue({ item: detail, meta: { snapshot_at: "2026-08-29T10:01:00Z" } });
    command.mockResolvedValue({ ok: true, message: "反馈已保存", meta: { updated_at: "" } });
  });

  it("renders the legacy business sections and hides internal session identifiers", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "Attempt #8448" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Trigger" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "审计说明" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "生成回复" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "内部反馈/建议修改" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Audit summary" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Draft reply (raw Codex reply)" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Runtime attempts" })).toBeInTheDocument();
    expect(screen.getByText("已关联会话（标识已隐藏）")).toBeInTheDocument();
    expect(screen.queryByText("session-8448")).not.toBeInTheDocument();
  });

  it("submits the inline feedback form with the edited values", async () => {
    const user = userEvent.setup();
    renderPage();

    const feedback = await screen.findByLabelText("反馈意见");
    await user.clear(feedback);
    await user.type(feedback, "补充来源");
    await user.click(screen.getByRole("button", { name: "保存反馈" }));

    expect(command).toHaveBeenCalledWith(
      "/api/console/history/8448/feedback",
      { feedback: "补充来源", corrected_reply: "建议回复" },
    );
    expect(await screen.findByText("反馈已保存")).toBeInTheDocument();
  });

  it("requires confirmation before rerunning a failed Attempt", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    getAttemptDetail.mockResolvedValueOnce({
      item: { ...detail, status: { ...detail.status, raw: "failed" }, actions: { ...detail.actions, can_rerun: true, terminal: false, action_label: "需要处理" } },
      meta: { snapshot_at: "2026-08-29T10:01:00Z" },
    });
    renderPage();

    await user.click(await screen.findByRole("button", { name: "重新处理" }));
    expect(command).toHaveBeenCalledWith("/api/console/history/8448/rerun");
    vi.restoreAllMocks();
  });
});
