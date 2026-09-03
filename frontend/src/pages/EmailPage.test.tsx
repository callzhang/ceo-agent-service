import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listEmailClassifications = vi.hoisted(() => vi.fn());
const confirmEmailClassification = vi.hoisted(() => vi.fn());
const listEmailConfigs = vi.hoisted(() => vi.fn());
const saveEmailConfig = vi.hoisted(() => vi.fn());
const listEmailLearning = vi.hoisted(() => vi.fn());
const getEmailClassification = vi.hoisted(() => vi.fn());

vi.mock("../api/console", () => ({
  listEmailClassifications,
  confirmEmailClassification,
  listEmailConfigs,
  saveEmailConfig,
  listEmailLearning,
  getEmailClassification,
  displayValue: (value: unknown) => String(value || "未提供"),
}));

import { EmailPage } from "./EmailPage";

function renderEmail(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/email" element={<EmailPage />} /></Routes></MemoryRouter>);
}

describe("EmailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listEmailClassifications.mockResolvedValue({
      items: [{
        id: 1, provider: "dingtalk_mail", mailbox: "INBOX", message_id: "msg-1", thread_id: "",
        sender: "sender@example.com", subject: "需要确认", preview: "邮件摘要", received_at: "2026-08-29T00:00:00Z",
        category: "work", confidence: 0.61, margin: 0.04, probabilities: { work: 0.61, important: 0.57 },
        model_version: "model-1", config_version: "email-v1", status: "pending_feedback", classification_source: "model",
        action_plan: {}, current_action_plan_id: null, confirmed_at: "", created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T00:00:00Z",
      }],
      meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" },
    });
    confirmEmailClassification.mockResolvedValue({ ok: true, item: {}, message: "邮件分类反馈已保存" });
    listEmailConfigs.mockResolvedValue({ items: [], meta: { snapshot_at: "2026-08-29T00:00:00Z" } });
    listEmailLearning.mockResolvedValue({ learning: { active_model_id: "email-tfidf-lr-v1", pending_examples: 2, last_trained_feedback_count: 20, last_trained_at: "2026-08-29T00:00:00Z", last_feedback_at: "2026-08-29T00:00:00Z", active_run_id: null, category_thresholds: {}, models: [{ model_id: "email-tfidf-lr-v1", model_version: "email-tfidf-lr-v1", status: "active", trained_at: "2026-08-29T00:00:00Z", training_started_at: "2026-08-29T00:00:00Z", training_finished_at: "2026-08-29T00:00:00Z", sample_count: 20, new_sample_count: 20, category_counts: {}, validation_method: "time-ordered-holdout", accuracy: 0.9, macro_f1: 0.8, per_category_metrics: {}, prediction_latency_p50_ms: 1, prediction_latency_p95_ms: 2 }] } });
    getEmailClassification.mockResolvedValue({
      ok: true,
      item: { id: 2, subject: "订阅邮件", sender: "news@example.com", preview: "每周资讯", category: "subscription", confidence: 0.99, margin: 0.2, classification_source: "model", received_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T00:00:00Z" },
      observability: [{ kind: "unsubscribe", operation: "unsubscribe", lifecycle_version: "email_unsubscribe_audited_v2", task_id: 42, task_status: "done", consumer_run_ids: [101], audit_run_ids: [102], status: "done", receipt_id: "receipt-2", result_text: "退订成功\n[REDACTED_URL]", evidence: "最终结果页：已成功退订", observation_digest: "digest-2", steps: [{ sequence: 1, operation: "open_entry", state: "done", reference: "receipt-2" }] }],
      meta: { snapshot_at: "2026-08-29T00:00:00Z" },
    });
  });

  it("shows model suggestions in pending feedback and records a human choice", async () => {
    const user = userEvent.setup();
    renderEmail("/email?tab=pending_feedback");

    expect(await screen.findByText("需要确认")).toBeInTheDocument();
    expect(screen.getByText("61.0%")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重要" }));

    expect(confirmEmailClassification).toHaveBeenCalledWith(1, "important", "email-feedback:1", null);
    expect(await screen.findByText("邮件分类反馈已保存")).toBeInTheDocument();
    expect(screen.queryByText("需要确认")).not.toBeInTheDocument();
  });

  it("states that category configuration does not execute provider writes", async () => {
    renderEmail("/email?tab=config");

    expect(await screen.findByRole("heading", { name: "邮件类型配置" })).toBeInTheDocument();
    expect(screen.getByText("类别、描述、置信度阈值和固定动作。这里不直接执行邮箱动作。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存本地配置" })).toBeInTheDocument();
  });

  it("explains the configured email action lifecycle accurately after saving", async () => {
    const user = userEvent.setup();
    saveEmailConfig.mockResolvedValue({
      ok: true,
      item: { category: "important", description: "", threshold: 0.9, actions: ["label"], enabled: true, config_version: "email-v1", updated_at: "2026-08-29T00:00:00Z" },
      message: "邮件配置已保存",
    });
    renderEmail("/email?tab=config");

    await user.click(await screen.findByRole("button", { name: "保存本地配置" }));

    expect(await screen.findByText("配置已保存：确定性动作由 Email worker 执行并回读；退订由 Consumer 提案、Audit 审核执行。邮件回复已全局禁用。"))
      .toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "auto_reply" })).not.toBeInTheDocument();
  });

  it("shows model version and training evidence", async () => {
    renderEmail("/email?tab=learning");
    expect(await screen.findAllByText("email-tfidf-lr-v1")).toHaveLength(2);
    expect(screen.getByText(/待训练样本：2/)).toBeInTheDocument();
    expect(screen.getByText(/20（新增 20）/)).toBeInTheDocument();
  });

  it("shows unsubscribe observability only after opening a processed email detail", async () => {
    const user = userEvent.setup();
    listEmailClassifications.mockResolvedValueOnce({
      items: [{ id: 2, subject: "订阅邮件", sender: "news@example.com", preview: "每周资讯", category: "subscription", confidence: 0.99, margin: 0.2, classification_source: "model", received_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T00:00:00Z" }],
      meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" },
    });
    renderEmail("/email");

    expect(await screen.findByText("订阅邮件")).toBeInTheDocument();
    expect(screen.queryByText("退订成功")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看处理详情" }));

    expect(getEmailClassification).toHaveBeenCalledWith(2);
    expect(await screen.findByText(/退订成功/)).toBeInTheDocument();
    expect(screen.getByText("自动退订")).toBeInTheDocument();
    expect(screen.getByText("Consumer → Audit")).toBeInTheDocument();
    expect(screen.getByText("email_unsubscribe_audited_v2")).toBeInTheDocument();
    expect(screen.getByText(/Task 42.*done/)).toBeInTheDocument();
    expect(screen.getByText(/Consumer run.*101/)).toBeInTheDocument();
    expect(screen.getByText(/Audit run.*102/)).toBeInTheDocument();
    expect(screen.getAllByText(/receipt-2/)).toHaveLength(2);
    expect(screen.getByText(/digest-2/)).toBeInTheDocument();
    expect(screen.getByText(/open_entry.*done.*receipt-2/)).toBeInTheDocument();
    expect(screen.getByText(/最终结果页：已成功退订/)).toBeInTheDocument();
    expect(screen.queryByText(new RegExp(["Consumer", "direct"].join("-"))))
      .not.toBeInTheDocument();
  });
});
