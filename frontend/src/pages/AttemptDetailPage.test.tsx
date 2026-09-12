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
  feedback: {
    reviewer_feedback: "需要更具体",
    corrected_reply: "建议回复",
    feedback_url: "/api/console/history/8448/feedback",
    events: [{ rating: "useful", rating_label: "很有用", rating_stars: "★★★★☆ · 4/5", comment: "回复已经解决问题", source: "dingtalk", received_at: "2026-09-08T10:01:00Z" }],
  },
  decision_options: [],
  audit_summary: "审计摘要",
  draft_reply: "原始草稿",
  failure_reason: "",
  recovery_state: "",
  action_pills: [{ label: "💬 Completed", status: "completed" }],
  quality_warnings: [],
  context_only_info: "",
  references: [{ title: "岗位画像", source: "面试/岗位画像.md · document", relevance: "判断岗位要求" }],
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
    agent_url: "/codex/session-8448",
    dingtalk_url: "",
    terminal: true,
    action_label: "无需操作",
  },
  tool_uses: [],
  agent_sessions: [
    { role: "consumer", label: "处理过程", session_id: "session-consumer", url: "/codex/session-consumer", tool_uses: [{ title: "read_thread", tool: "read_thread", call_id: "call-1", relevance: "", source: "dingtalk", args: { conversation_id: "cid-1" }, format: "", output: "最近 3 条消息" }] },
    { role: "audit", label: "审计过程", session_id: "session-8448", url: "/codex/session-8448", tool_uses: [{ title: "unsubscribe_email", tool: "unsubscribe_email", call_id: "call-2", relevance: "", source: "", args: { task_id: 383926 }, format: "", output: "skipped_no_reliable_entry" }] },
  ],
  runtime_attempts: [{ role: "consumer", session_url: "/codex/session-consumer", proposal_revision: 0, turn_attempt: 0, route: "consumer", runtime: "codex", credential_mode: "configured", model: "qwen", session_available: true, status: "completed", failure_code: "", failover_permitted: false, transcript_start: 1, transcript_end: 2, effect_started_at: "" }],
  created_at: "2026-08-29T10:00:00Z",
  updated_at: "2026-08-29T10:01:00Z",
};

function renderPage(path = "/attempts/8448") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/attempts/:attemptId" element={<AttemptDetailPage />} />
        <Route path="/attempts/:attemptId/execution/:role" element={<AttemptDetailPage />} />
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

  it("renders one generated reply section and hides internal session identifiers", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "Attempt #8448" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Trigger" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "审计说明" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "生成回复" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "反馈迭代" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "内部反馈/建议修改" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("内部反馈意见")).toHaveAttribute("rows", "1");
    expect(screen.getByLabelText("建议回复")).toHaveAttribute("rows", "1");
    expect(screen.getByText("对方反馈（1-5星）")).toBeInTheDocument();
    expect(screen.getByText("★★★★☆ · 4/5")).toBeInTheDocument();
    expect(screen.getByText("对方反馈内容")).toBeInTheDocument();
    expect(screen.getByText("回复已经解决问题")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Audit summary" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "参考资料" })).toBeInTheDocument();
    expect(screen.getByText("岗位画像")).toBeInTheDocument();
    expect(screen.getByText("判断岗位要求")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Tool uses" })).not.toBeInTheDocument();
    expect(screen.getByTestId("attempt-conversation-summary")).toHaveTextContent("群名：吴柯欣");
    expect(screen.getByTestId("attempt-title-row")).not.toContainElement(screen.getByRole("link", { name: "查看 Agent session" }));
    expect(screen.getByTestId("attempt-conversation-actions")).toContainElement(screen.getByRole("link", { name: "查看 Agent session" }));
    expect(screen.getByRole("link", { name: "查看 Agent session" })).toHaveAttribute("href", "/codex/session-8448");
    expect(screen.queryByRole("heading", { name: "Draft reply (raw Codex reply)" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "处理过程" })).toBeInTheDocument();
    expect(screen.getByText(/每一轮会先由处理 Agent 形成方案，再由审计 Agent 核验。多条记录表示修订、重试或重新核验，不代表重复发送。/)).toBeInTheDocument();
    expect(screen.getByText("处理判断 · 第 1 轮")).toBeInTheDocument();
    expect(document.querySelector(".attempt-review-grid")).toBeInTheDocument();
    expect(document.querySelector(".attempt-review-side")).toBeInTheDocument();
    expect(document.querySelector(".attempt-review-main .attempt-review-block + .attempt-review-block")).toBeInTheDocument();
    expect(document.querySelector(".attempt-status-card")).not.toBeInTheDocument();
    expect(screen.queryByText("session-8448")).not.toBeInTheDocument();
  });

  it("opens the requested Consumer execution instead of silently rendering the generic Attempt page", async () => {
    renderPage("/attempts/8448/execution/consumer");

    expect(await screen.findByRole("heading", { name: "处理过程 · Consumer" })).toBeInTheDocument();
    expect(screen.getByText("这里只展示处理 Agent 形成方案的记录。")).toBeInTheDocument();
    expect(screen.getByText("处理判断 · 第 1 轮")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回 Attempt" })).toHaveAttribute("href", "/attempts/8448");
    expect(screen.queryByRole("heading", { name: "生成回复" })).not.toBeInTheDocument();
  });

  it("shows what each role actually called, not only that a role ran", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "执行过程" })).toBeInTheDocument();
    expect(screen.getByText("unsubscribe_email")).toBeInTheDocument();
    expect(screen.getByText('{"task_id":383926}')).toBeInTheDocument();
    expect(screen.getByText("skipped_no_reliable_entry")).toBeInTheDocument();
    expect(screen.getByText("read_thread")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "查看完整 Agent 记录" })[0]).toHaveAttribute("href", "/codex/session-consumer");
  });

  it("shows the Consumer calls on the Consumer execution page", async () => {
    renderPage("/attempts/8448/execution/consumer");

    expect(await screen.findByRole("heading", { name: "调用记录" })).toBeInTheDocument();
    expect(screen.getByText("read_thread")).toBeInTheDocument();
    expect(screen.getByText("最近 3 条消息")).toBeInTheDocument();
    expect(screen.queryByText("unsubscribe_email")).not.toBeInTheDocument();
  });

  it("keeps the process visible for an Attempt whose transcripts are gone", async () => {
    getAttemptDetail.mockResolvedValue({
      item: {
        ...detail,
        agent_sessions: [],
        tool_uses: [{ title: "exec_command", tool: "exec_command", call_id: "call-9", relevance: "", source: "", args: { command: "rg 岗位" }, format: "", output: "2 个匹配" }],
      },
      meta: { snapshot_at: "2026-08-29T10:01:00Z" },
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: "执行过程" })).toBeInTheDocument();
    expect(screen.getByText("exec_command")).toBeInTheDocument();
    expect(screen.getByText("2 个匹配")).toBeInTheDocument();
  });

  it("submits the inline feedback form with the edited values", async () => {
    const user = userEvent.setup();
    renderPage();

    const feedback = await screen.findByLabelText("内部反馈意见");
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

  it("refreshes the Attempt after submitting a custom human decision", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const decisionDetail = {
      ...detail,
      status: { ...detail.status, raw: "needs_human", requires_decision: true },
      decision_options: [{
        label: "授权通知申请人",
        instruction: "向申请人发送审核结论。",
        consequence: "会向实际申请人发送一条钉钉消息。",
        url: "/api/console/history/8448/human-decision",
      }],
    };
    getAttemptDetail.mockResolvedValueOnce({ item: decisionDetail, meta: { snapshot_at: "2026-09-08T10:01:00Z" } });
    getAttemptDetail.mockResolvedValueOnce({ item: detail, meta: { snapshot_at: "2026-09-08T10:02:00Z" } });
    command.mockResolvedValueOnce({ ok: true, message: "人工决策已提交", meta: { updated_at: "" } });
    renderPage();

    const instruction = await screen.findByLabelText("其他处理指令（默认仅本次）");
    expect(screen.getByText("请填写其他处理指令后提交；下方“反馈迭代”只保存反馈，不会执行处理。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交处理指令" })).toBeDisabled();
    await user.type(instruction, "保留审批已执行事实，不向申请人发送额外通知。");
    await user.click(screen.getByRole("button", { name: "提交处理指令" }));

    expect(command).toHaveBeenCalledWith(
      "/api/console/history/8448/human-decision",
      { instruction: "保留审批已执行事实，不向申请人发送额外通知。", feedback_scope: "one_time", skill_update_requested: false },
    );
    expect(await screen.findByText("人工决策已提交")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "需要你的判断" })).not.toBeInTheDocument();
    expect(getAttemptDetail).toHaveBeenCalledTimes(2);
    vi.restoreAllMocks();
  });

  it("shows a dedicated retry action for an expired WeChat delivery", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    getAttemptDetail.mockResolvedValueOnce({
      item: {
        ...detail,
        actions: {
          ...detail.actions,
          delivery_action_label: "重试发送",
          delivery_action_url: "/api/console/wechat/deliveries/101/retry",
          wechat_open_url: "/api/console/history/8448/open-wechat-message",
        },
      },
      meta: { snapshot_at: "2026-09-08T22:39:47Z" },
    });
    renderPage();

    await user.click(await screen.findByRole("button", { name: "重试发送" }));
    expect(command).toHaveBeenCalledWith("/api/console/wechat/deliveries/101/retry");
    vi.restoreAllMocks();
  });

  it("keeps navigation and WeChat controls in the Attempt title row", async () => {
    getAttemptDetail.mockResolvedValueOnce({
      item: {
        ...detail,
        actions: {
          ...detail.actions,
          delivery_action_label: "重试发送",
          delivery_action_url: "/api/console/wechat/deliveries/101/retry",
          wechat_open_url: "/api/console/history/8448/open-wechat-message",
        },
      },
      meta: { snapshot_at: "2026-09-08T22:39:47Z" },
    });
    renderPage();

    const header = await screen.findByTestId("attempt-title-row");
    const backLink = screen.getByRole("link", { name: "返回 History" });
    expect(header).toContainElement(backLink);
    expect(backLink).toHaveTextContent("←");
    expect(header).toContainElement(screen.getByRole("button", { name: "查看微信消息" }));
    expect(header).toContainElement(screen.getByRole("button", { name: "重试发送" }));
    expect(document.querySelector(".attempt-bottom-actions")).toBeNull();
  });
});
