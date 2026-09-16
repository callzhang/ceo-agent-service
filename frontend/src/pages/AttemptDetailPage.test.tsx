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
  email: null,
  agent_sessions: [
    { role: "consumer", label: "处理过程", session_id: "session-consumer", url: "/codex/session-consumer" },
    { role: "audit", label: "审计过程", session_id: "session-8448", url: "/codex/session-8448" },
  ],
  runtime_attempts: [{ role: "consumer", session_url: "/codex/session-consumer", proposal_revision: 0, turn_attempt: 0, route: "consumer", runtime: "codex", credential_mode: "configured", model: "qwen", session_available: true, status: "completed", failure_code: "", failover_permitted: false, transcript_start: 1, transcript_end: 2, effect_started_at: "" }],
  created_at: "2026-08-29T10:00:00Z",
  updated_at: "2026-08-29T10:01:00Z",
};

type ConsumerResultFixture = {
  confidence: string;
  information_completeness: string;
  rule_coverage: string;
  risk: string;
  error_reason: string;
  current_run: { id: number | null; status: "pending" | "running" } | null;
};

function withConsumerResult(consumer_result: ConsumerResultFixture) {
  return { ...detail, consumer_result } as typeof detail & { consumer_result: ConsumerResultFixture };
}

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
    expect(screen.getByRole("heading", { name: "处理历史" })).toBeInTheDocument();
    expect(screen.getByText("这里保留当前处理和历史重试；历史重试不会等同于重复发送。")).toBeInTheDocument();
    expect(screen.getByText("处理判断 · 第 1 轮")).toBeInTheDocument();
    expect(document.querySelector(".attempt-review-grid")).toBeInTheDocument();
    expect(document.querySelector(".attempt-review-side")).toBeInTheDocument();
    expect(document.querySelector(".attempt-review-main .attempt-review-block + .attempt-review-block")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "当前状态" })).not.toBeInTheDocument();
    expect(screen.queryByText("session-8448")).not.toBeInTheDocument();
  });

  it("groups runtime retries into collapsed processing batches", async () => {
    getAttemptDetail.mockResolvedValue({
      item: {
        ...detail,
        runtime_attempts: [
          { role: "consumer", session_url: "", proposal_revision: 0, turn_attempt: 0, route: "codex_oauth", runtime: "codex_cli", credential_mode: "local_oauth", model: "gpt-5.6-sol", session_available: false, status: "failed", failure_code: "service_restart_before_effect", failover_permitted: true, transcript_start: 0, transcript_end: 0, effect_started_at: "", run_id: 1, execution_generation: "generation-old", attempt_number: 1, created_at: "2026-08-24 04:11:51", finished_at: "2026-08-24 04:12:31" },
          { role: "audit", session_url: "", proposal_revision: 0, turn_attempt: 0, route: "codex_oauth", runtime: "codex_cli", credential_mode: "local_oauth", model: "gpt-5.6-sol", session_available: false, status: "failed", failure_code: "runtime_capability_missing", failover_permitted: true, transcript_start: 0, transcript_end: 0, effect_started_at: "", run_id: 2, execution_generation: "generation-old", attempt_number: 2, created_at: "2026-08-24 04:14:58", finished_at: "2026-08-24 04:15:02" },
          { role: "consumer", session_url: "", proposal_revision: 0, turn_attempt: 0, route: "codex_oauth", runtime: "codex_cli", credential_mode: "local_oauth", model: "gpt-5.6-sol", session_available: false, status: "completed", failure_code: "", failover_permitted: false, transcript_start: 0, transcript_end: 4, effect_started_at: "", run_id: 3, execution_generation: "generation-new", attempt_number: 1, created_at: "2026-08-25 18:26:05", finished_at: "2026-08-25 18:27:42" },
          { role: "audit", session_url: "", proposal_revision: 0, turn_attempt: 0, route: "codex_oauth", runtime: "codex_cli", credential_mode: "local_oauth", model: "gpt-5.6-sol", session_available: false, status: "completed", failure_code: "", failover_permitted: false, transcript_start: 0, transcript_end: 4, effect_started_at: "", run_id: 4, execution_generation: "generation-new", attempt_number: 1, created_at: "2026-08-25 18:27:51", finished_at: "2026-08-27 06:34:48" },
          { role: "consumer", session_url: "", proposal_revision: 0, turn_attempt: 0, route: "codex_oauth", runtime: "codex_cli", credential_mode: "local_oauth", model: "gpt-5.6-sol", session_available: false, status: "completed", failure_code: "", failover_permitted: false, transcript_start: 0, transcript_end: 4, effect_started_at: "", run_id: 5, execution_generation: "generation-newest", attempt_number: 1, created_at: "2026-09-08 02:17:28", finished_at: "2026-09-08 02:18:12" },
        ],
      },
      meta: { snapshot_at: "2026-09-14T10:01:00Z" },
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: "处理历史" })).toBeInTheDocument();
    expect(screen.getByText("3 个处理批次")).toBeInTheDocument();
    expect(screen.getByText(/5 个内部运行记录/)).toBeInTheDocument();
    expect(document.querySelector(".attempt-process-count")).toHaveTextContent(
      "历史重试不会等同于重复发送",
    );
    const batches = document.querySelectorAll<HTMLDetailsElement>(".attempt-process-batch");
    expect(batches).toHaveLength(3);
    expect(batches[0].open).toBe(false);
    expect(batches[1].open).toBe(false);
    expect(batches[2].open).toBe(true);
  });

  it("turns internal audit labels into a readable explanation", async () => {
    getAttemptDetail.mockResolvedValue({
      item: {
        ...detail,
        status: { ...detail.status, raw: "done", message: "当前事项已完成，无需你操作。" },
        audit_explanation: {
          title: "审计说明",
          text: "Reviewer feedback: Human decision for source attempt #7178: 确认硅谷行程不推迟。\n\nOriginal ambiguity summary:\n当时无法确认是否推迟行程。\nSuggested response:\nreviewed_message_reply",
        },
      },
      meta: { snapshot_at: "2026-09-14T10:01:00Z" },
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: "审计说明" })).toBeInTheDocument();
    expect(screen.getByText("审计结论")).toBeInTheDocument();
    expect(screen.getByText("人工决定")).toBeInTheDocument();
    expect(screen.getByText("原始疑点")).toBeInTheDocument();
    expect(screen.getByText("确认硅谷行程不推迟。")).toBeInTheDocument();
    expect(screen.getByText("当时无法确认是否推迟行程。")).toBeInTheDocument();
    expect(screen.queryByText("Reviewer feedback:")).not.toBeInTheDocument();
    expect(screen.queryByText("Original ambiguity summary:")).not.toBeInTheDocument();
    expect(screen.queryByText("Suggested response:")).not.toBeInTheDocument();
    expect(screen.queryByText("reviewed_message_reply")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "当前状态" })).not.toBeInTheDocument();
  });

  it("shows the linked Consumer result group with its scores and risk", async () => {
    getAttemptDetail.mockResolvedValue({
      item: withConsumerResult({
        confidence: "82%",
        information_completeness: "75%",
        rule_coverage: "100%",
        risk: "medium",
        error_reason: "",
        current_run: null,
      }),
      meta: { snapshot_at: "2026-09-14T10:01:00Z" },
    });
    renderPage();

    await screen.findByRole("heading", { name: "Attempt #8448" });
    expect(screen.queryByRole("heading", { name: "Consumer 执行结果" })).not.toBeInTheDocument();
    expect(screen.getByText("trigger message id").closest(".attempt-review-side")).toBeInTheDocument();
    expect(screen.getByText("confidence")).toBeInTheDocument();
    expect(screen.getByText("82%")).toBeInTheDocument();
    expect(screen.getByText("information_completeness")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("rule_coverage")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText("risk")).toBeInTheDocument();
    expect(screen.getByText("medium")).toBeInTheDocument();
    expect(screen.getByText("82%").closest(".attempt-consumer-metric")).toHaveClass("attempt-consumer-metric-good");
    expect(screen.getByText("75%").closest(".attempt-consumer-metric")).toHaveClass("attempt-consumer-metric-warning");
    expect(screen.getByText("100%").closest(".attempt-consumer-metric")).toHaveClass("attempt-consumer-metric-good");
    expect(screen.getByText("medium").closest(".attempt-consumer-metric")).toHaveClass("attempt-consumer-metric-warning");
    const side = document.querySelector(".attempt-review-side");
    expect(side).toContainElement(screen.getByText("confidence"));
    expect(side).toContainElement(screen.getByText("medium"));
    expect(document.querySelector(".attempt-detail-main .attempt-consumer-metrics-card")).not.toBeInTheDocument();
    expect(side).toContainElement(screen.getByText("trigger message id"));
    expect(side).toContainElement(screen.getByText("msg-1"));
    expect(side).toContainElement(screen.getByText("action"));
    expect(side).toContainElement(screen.getByText("reply"));
    expect(document.querySelector(".attempt-detail-main .attempt-metadata-card")).not.toBeInTheDocument();
    expect(screen.queryByText("Consumer error")).not.toBeInTheDocument();
    expect(document.querySelectorAll(".attempt-metadata-grid")).toHaveLength(1);
  });

  it("keeps all Consumer metrics visible as unavailable with the safe error reason", async () => {
    getAttemptDetail.mockResolvedValue({
      item: withConsumerResult({
        confidence: "—",
        information_completeness: "—",
        rule_coverage: "—",
        risk: "—",
        error_reason: "Consumer 结果不符合当前契约",
        current_run: null,
      }),
      meta: { snapshot_at: "2026-09-14T10:01:00Z" },
    });
    renderPage();

    await screen.findByRole("heading", { name: "Attempt #8448" });
    expect(screen.queryByRole("heading", { name: "Consumer 执行结果" })).not.toBeInTheDocument();
    expect(screen.getByText("confidence")).toBeInTheDocument();
    expect(screen.getByText("information_completeness")).toBeInTheDocument();
    expect(screen.getByText("rule_coverage")).toBeInTheDocument();
    expect(screen.getByText("risk")).toBeInTheDocument();
    expect(screen.getAllByText("—")).toHaveLength(4);
    expect(document.querySelectorAll(".attempt-consumer-metric-neutral")).toHaveLength(4);
    expect(screen.getByText("Consumer error")).toBeInTheDocument();
    expect(screen.getByText("Consumer 结果不符合当前契约")).toBeInTheDocument();
  });

  it.each([
    { id: 912, apiStatus: "pending" as const, displayStatus: "等待中" },
    { id: 913, apiStatus: "running" as const, displayStatus: "运行中" },
  ])("shows the newer Consumer run #$id as $displayStatus without replacing the linked metrics", async ({ id, apiStatus, displayStatus }) => {
    getAttemptDetail.mockResolvedValue({
      item: withConsumerResult({
        confidence: "82%",
        information_completeness: "75%",
        rule_coverage: "100%",
        risk: "medium",
        error_reason: "",
        current_run: { id, status: apiStatus },
      }),
      meta: { snapshot_at: "2026-09-14T10:01:00Z" },
    });
    renderPage();

    await screen.findByRole("heading", { name: "Attempt #8448" });
    expect(screen.queryByRole("heading", { name: "Consumer 执行结果" })).not.toBeInTheDocument();
    expect(screen.getByText(`新 Consumer run #${id}`)).toBeInTheDocument();
    expect(screen.getByText(displayStatus)).toBeInTheDocument();
    expect(screen.getByText("82%")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText("medium")).toBeInTheDocument();
  });

  it("shows a queued new Consumer run without rendering a null run id", async () => {
    getAttemptDetail.mockResolvedValue({
      item: withConsumerResult({
        confidence: "82%",
        information_completeness: "75%",
        rule_coverage: "100%",
        risk: "medium",
        error_reason: "",
        current_run: { id: null, status: "pending" },
      }),
      meta: { snapshot_at: "2026-09-14T10:01:00Z" },
    });
    renderPage();

    await screen.findByRole("heading", { name: "Attempt #8448" });
    expect(screen.queryByRole("heading", { name: "Consumer 执行结果" })).not.toBeInTheDocument();
    expect(screen.getByText("新 Consumer run")).toBeInTheDocument();
    expect(screen.getByText("等待中")).toBeInTheDocument();
    expect(screen.queryByText("#null")).not.toBeInTheDocument();
    expect(screen.getByText("82%")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText("medium")).toBeInTheDocument();
  });

  it("opens the requested Consumer execution instead of silently rendering the generic Attempt page", async () => {
    renderPage("/attempts/8448/execution/consumer");

    expect(await screen.findByRole("heading", { name: "处理过程 · Consumer" })).toBeInTheDocument();
    expect(screen.getByText("这里只展示处理 Agent 形成方案的记录。")).toBeInTheDocument();
    expect(screen.getByText("处理判断 · 第 1 轮")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回 Attempt" })).toHaveAttribute("href", "/attempts/8448");
    expect(screen.queryByRole("heading", { name: "生成回复" })).not.toBeInTheDocument();
  });

  it("reaches both roles' Agent records rather than repeating their calls", async () => {
    renderPage();

    expect(await screen.findByRole("link", { name: "查看处理过程" })).toHaveAttribute("href", "/attempts/8448/execution/consumer");
    expect(screen.getByRole("link", { name: "查看审计过程" })).toHaveAttribute("href", "/attempts/8448/execution/audit");
    // The overview keeps a single merged processing section; role-specific
    // Agent records remain available through the action bar.
    expect(screen.getByRole("heading", { name: "处理历史" })).toBeInTheDocument();
  });

  it("puts the Consumer execution page's Agent-record link in the page actions, not a separate card", async () => {
    renderPage("/attempts/8448/execution/consumer");

    await screen.findByRole("heading", { name: "处理过程 · Consumer" });
    // This used to be its own "调用记录" card whose entire content was this
    // one link (or, once the session rotated off disk, one sentence saying
    // so) - strictly less than what the Attempt page banner already offered
    // one click earlier. It carries no record of its own.
    expect(screen.queryByRole("heading", { name: "调用记录" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看 Agent 记录" })).toHaveAttribute("href", "/codex/session-consumer");
  });

  it("says the Agent record is gone inline instead of a dead card, when the session has rotated off disk", async () => {
    getAttemptDetail.mockResolvedValue({
      item: { ...detail, agent_sessions: [] },
      meta: { snapshot_at: "2026-08-29T10:01:00Z" },
    });
    renderPage("/attempts/8448/execution/consumer");

    await screen.findByRole("heading", { name: "处理过程 · Consumer" });
    expect(screen.queryByRole("link", { name: "查看 Agent 记录" })).not.toBeInTheDocument();
    expect(screen.getByText("本次执行的 Agent 记录已不在本机")).toBeInTheDocument();
  });

  it("shows the email an email Attempt acted on and the receipt it earned", async () => {
    getAttemptDetail.mockResolvedValue({
      item: {
        ...detail,
        conversation: { label: "邮件", title: "Email unsubscribe", trigger_sender: "noreply@email.openai.com" },
        email: {
          classification_id: "6811963115514558557",
          classification_url: "/email?tab=list&selected=6811963115514558557",
          account_id: "dingtalk_primary",
          action_type: "unsubscribe",
          category: "junk",
          action_plan_id: "email-action-plan:dd0fff87",
          stable_message_identity: "dingtalk_primary:message-id:<msg@host>",
          subject: "有 4 种新图像风格等你尝试",
          sender: "noreply@email.openai.com",
          folder: "已删除邮件",
          received_at: "Sat, 12 Sep 2026 06:18:39 +0000",
          rfc_message_id: "<msg@host>",
          candidate_source: "body_html_https",
          unsubscribe: {
            outcome: "skipped_no_reliable_entry",
            evidence: "page-not-operable",
            result_text: "host='r.openai.com' control_count=0",
            receipt_id: "unsubscribe-receipt:5fd912d9",
            entry_reference: "unsubscribe-entry:870a914f",
            entry_url: "https://r.openai.com/asm/unsubscribe?token=private-token",
            started_at: "2026-09-12T06:21:29+00:00",
            completed_at: "2026-09-12T06:21:29+00:00",
            steps: [{ sequence: 1, operation: "open_entry", state: "skipped_no_reliable_entry" }],
          },
        },
      },
      meta: { snapshot_at: "2026-09-12T06:21:40Z" },
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: "关联邮件" })).toBeInTheDocument();
    expect(screen.getByText("有 4 种新图像风格等你尝试")).toBeInTheDocument();
    expect(screen.getByText("已删除邮件")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "打开这封邮件" })).toHaveAttribute("href", "/email?tab=list&selected=6811963115514558557");
    expect(screen.getByTestId("attempt-conversation-actions")).toContainElement(screen.getByRole("link", { name: "打开这封邮件" }));
    expect(screen.getByRole("heading", { name: "退订回执" })).toBeInTheDocument();
    expect(screen.getAllByText("skipped_no_reliable_entry").length).toBeGreaterThan(0);
    expect(screen.getByText("page-not-operable")).toBeInTheDocument();
    expect(screen.getByText("host='r.openai.com' control_count=0")).toBeInTheDocument();
    expect(screen.getByText("open_entry")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "https://r.openai.com/asm/unsubscribe?token=private-token" })).not.toBeInTheDocument();
    expect(screen.queryByText("退订入口（打开会真实执行退订）")).not.toBeInTheDocument();
  });

  it("does not expand an empty counterparty feedback section", async () => {
    getAttemptDetail.mockResolvedValueOnce({
      item: { ...detail, feedback: { ...detail.feedback, events: [] } },
      meta: { snapshot_at: "2026-08-29T10:01:00Z" },
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: "反馈迭代" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "对方反馈" })).not.toBeInTheDocument();
    expect(screen.queryByText("对方反馈（1-5星）")).not.toBeInTheDocument();
  });

  it("keeps unsubscribe attempts focused on the email receipt instead of duplicate context/process cards", async () => {
    getAttemptDetail.mockResolvedValueOnce({
      item: {
        ...detail,
        context_only_info: "重复的审计上下文",
        email: {
          classification_id: "email-1",
          classification_url: "/email?tab=list&selected=email-1",
          account_id: "primary",
          action_type: "unsubscribe",
          category: "subscription",
          action_plan_id: "plan-1",
          stable_message_identity: "message-1",
          subject: "退订测试",
          sender: "sender@example.com",
          folder: "收件箱",
          received_at: "2026-09-12T00:00:00Z",
          rfc_message_id: "<message-1>",
          candidate_source: "header",
          unsubscribe: null,
        },
      },
      meta: { snapshot_at: "2026-09-12T00:00:00Z" },
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: "关联邮件" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Audit context" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "处理历史" })).toBeInTheDocument();
    expect(screen.getByText("重复的审计上下文")).toBeInTheDocument();
    expect(document.querySelector(".attempt-detail-layout + .attempt-process-card")).not.toBeInTheDocument();
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

    expect(await screen.findByRole("heading", { name: "处理历史" })).toBeInTheDocument();
    expect(screen.getByText("exec_command")).toBeInTheDocument();
    expect(screen.getByText("2 个匹配")).toBeInTheDocument();
  });

  it("collapses a plain file-view command to the one line that matters: which file, which lines", async () => {
    getAttemptDetail.mockResolvedValue({
      item: {
        ...detail,
        agent_sessions: [],
        tool_uses: [
          {
            title: `/bin/zsh -lc "sed -n '1,240p' /Users/derek/.agents/skills/ceo-mail-review/SKILL.md"`,
            tool: "command_execution",
            call_id: "call-read",
            relevance: "",
            source: "command_execution · sed",
            args: { command: `/bin/zsh -lc "sed -n '1,240p' /Users/derek/.agents/skills/ceo-mail-review/SKILL.md"` },
            format: "terminal",
            output: "--- name: ceo-mail-review\ndescription: ...\n(the entire 240-line skill file)",
          },
        ],
      },
      meta: { snapshot_at: "2026-08-29T10:01:00Z" },
    });
    renderPage();

    await screen.findByRole("heading", { name: "处理历史" });
    expect(screen.getByText("/Users/derek/.agents/skills/ceo-mail-review/SKILL.md")).toBeInTheDocument();
    expect(screen.getByText("第 1-240 行")).toBeInTheDocument();
    // The point is not showing the wrapper command, the raw args, or the
    // file content it read back.
    expect(screen.queryByText(/bin\/zsh/)).not.toBeInTheDocument();
    expect(screen.queryByText(/entire 240-line skill file/)).not.toBeInTheDocument();
  });

  it("leaves a command that isn't a plain file view fully detailed", async () => {
    getAttemptDetail.mockResolvedValue({
      item: {
        ...detail,
        agent_sessions: [],
        tool_uses: [
          {
            title: `/bin/zsh -lc "rg 岗位 file.md | head -5"`,
            tool: "command_execution",
            call_id: "call-search",
            relevance: "",
            source: "command_execution · rg",
            args: { command: `/bin/zsh -lc "rg 岗位 file.md | head -5"` },
            format: "terminal",
            output: "3 处匹配",
          },
        ],
      },
      meta: { snapshot_at: "2026-08-29T10:01:00Z" },
    });
    renderPage();

    await screen.findByRole("heading", { name: "处理历史" });
    // Piped into something else, so it did more than view a file - keep the
    // full command/args/output rendering rather than guessing a summary.
    expect(screen.getAllByText(/rg 岗位 file\.md \| head -5/).length).toBeGreaterThan(0);
    expect(screen.getByText("3 处匹配")).toBeInTheDocument();
  });

  it("unwraps a double-encoded MCP tool result instead of showing raw \\n and \\\" escapes", async () => {
    getAttemptDetail.mockResolvedValue({
      item: {
        ...detail,
        agent_sessions: [],
        tool_uses: [
          {
            title: "upload_interview_result",
            tool: "upload_interview_result",
            call_id: "call-upload",
            relevance: "",
            source: "hr_mcp · upload_interview_result",
            args: { interview_id: "int-97c2d24b", feedback_summary: "技术强，售前弱" },
            format: "json",
            // What app/codex_history.py actually persists: a pre-stringified
            // envelope whose "text" field is itself JSON-encoded again.
            output: JSON.stringify({
              content: [{ type: "text", text: JSON.stringify({ status: "error", error_code: "INVALID_INPUT", message: "review_basis 不能为空" }) }],
            }, null, 2),
          },
        ],
      },
      meta: { snapshot_at: "2026-09-16T00:00:00Z" },
    });
    renderPage();

    await screen.findByRole("heading", { name: "处理历史" });
    expect(screen.getByText(/"error_code": "INVALID_INPUT"/)).toBeInTheDocument();
    expect(screen.getByText(/"message": "review_basis 不能为空"/)).toBeInTheDocument();
    // The envelope wrapper and the escaped duplicate are gone, not just
    // reformatted alongside the readable version.
    expect(screen.queryByText(/"content":/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\\n/)).not.toBeInTheDocument();
  });

  it("prefers structured_content over re-parsing the text field when both are present", async () => {
    getAttemptDetail.mockResolvedValue({
      item: {
        ...detail,
        agent_sessions: [],
        tool_uses: [
          {
            title: "get_interview_context",
            tool: "get_interview_context",
            call_id: "call-context",
            relevance: "",
            source: "hr_mcp · get_interview_context",
            args: { interview_id: "int-97c2d24b" },
            format: "json",
            output: JSON.stringify({
              // Deliberately a different shape from structured_content below -
              // if the fix ever regresses to re-parsing this field instead,
              // this marker (not the real one) is what would show up.
              content: [{ type: "text", text: JSON.stringify({ candidate_name: "STALE-DO-NOT-SHOW" }) }],
              structured_content: { status: "ok", candidate_name: "孙英双" },
            }, null, 2),
          },
        ],
      },
      meta: { snapshot_at: "2026-09-16T00:00:00Z" },
    });
    renderPage();

    await screen.findByRole("heading", { name: "处理历史" });
    expect(screen.getByText(/"candidate_name": "孙英双"/)).toBeInTheDocument();
    expect(screen.queryByText(/STALE-DO-NOT-SHOW/)).not.toBeInTheDocument();
  });

  it("keeps plain-text output (not JSON at all) exactly as returned", async () => {
    getAttemptDetail.mockResolvedValue({
      item: {
        ...detail,
        agent_sessions: [],
        tool_uses: [
          {
            title: `/bin/zsh -lc 'ls -la /Users/derek/.codex/skills/dingtalk-chat/'`,
            tool: "command_execution",
            call_id: "call-ls",
            relevance: "",
            source: "command_execution · ls",
            args: { command: `/bin/zsh -lc 'ls -la /Users/derek/.codex/skills/dingtalk-chat/'` },
            format: "terminal",
            output: "ls: /Users/derek/.codex/skills/dingtalk-chat/: No such file or directory\n",
          },
        ],
      },
      meta: { snapshot_at: "2026-09-16T00:00:00Z" },
    });
    renderPage();

    await screen.findByRole("heading", { name: "处理历史" });
    expect(screen.getByText(/No such file or directory/)).toBeInTheDocument();
  });

  it("keeps every post-header section inside the main and sidebar columns", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "Attempt #8448" })).toBeInTheDocument();
    const layout = document.querySelector(".attempt-detail-layout");
    expect(layout).toBeInTheDocument();
    expect(layout?.querySelector(".attempt-detail-main")).toContainElement(screen.getByRole("heading", { name: "Trigger" }));
    expect(layout?.querySelector(".attempt-detail-main")).toContainElement(screen.getByRole("heading", { name: "处理历史" }));
    expect(layout?.querySelector(".attempt-review-side")).toContainElement(screen.getByRole("heading", { name: "反馈迭代" }));
    expect(screen.queryByRole("heading", { name: "Audit context" })).not.toBeInTheDocument();
    expect(document.querySelector(".attempt-detail-layout + *")).toBeNull();
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

  it("keeps navigation in the title row and moves WeChat controls into the first action bar", async () => {
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
    expect(header).not.toContainElement(screen.getByRole("button", { name: "查看微信消息" }));
    expect(header).not.toContainElement(screen.getByRole("button", { name: "重试发送" }));
    const actionBar = screen.getByTestId("attempt-conversation-actions");
    expect(actionBar).toContainElement(screen.getByRole("button", { name: "查看微信消息" }));
    expect(actionBar).toContainElement(screen.getByRole("button", { name: "重试发送" }));
    expect(document.querySelector(".attempt-bottom-actions")).toBeNull();
  });
});
