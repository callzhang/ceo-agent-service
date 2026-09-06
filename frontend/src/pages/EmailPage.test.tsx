import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import workbenchStyles from "../styles.css?raw";

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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("EmailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listEmailClassifications.mockResolvedValue({
      items: [{
        id: "1", provider: "dingtalk_mail", mailbox: "INBOX", message_id: "msg-1", thread_id: "",
        sender: "sender@example.com", subject: "需要确认", preview: "邮件摘要", received_at: "2026-08-29T00:00:00Z",
        category: "work", confidence: 0.61, margin: 0.04, probabilities: { work: 0.61, important: 0.57 },
        model_version: "email-tfidf-lr-20260829T000000Z-deadbeef", config_version: "email-v1", status: "pending_feedback", classification_source: "model",
        attachment_metadata: [{ filename: "quarterly-report.pdf", mime_type: "application/pdf", size_bytes: 253952, inline: false, content: "ATTACHMENT-BODY-MUST-NOT-RENDER", path: "/private/mail/quarterly-report.pdf" }],
        action_plan: {}, current_action_plan_id: null, confirmed_at: "", created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T00:00:00Z",
      }],
      meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" },
    });
    confirmEmailClassification.mockResolvedValue({ ok: true, item: {}, message: "邮件分类反馈已保存" });
    listEmailConfigs.mockResolvedValue({ items: [], meta: { snapshot_at: "2026-08-29T00:00:00Z" } });
    listEmailLearning.mockResolvedValue({ learning: { active_model_id: "email-tfidf-lr-20260829T000000Z-active0001", pending_examples: 2, last_trained_feedback_count: 20, last_trained_at: "2026-08-29T00:00:00Z", last_feedback_at: "2026-08-29T00:00:00Z", active_run_id: null, category_thresholds: {}, models: [
      {
        model_id: "email-tfidf-lr-20260829T000000Z-active0001", model_version: "email-tfidf-lr-20260829T000000Z-active0001", status: "active", status_reason: "current production candidate passed validation", candidate_reason: "candidate validation pending", promotion_reason: "macro F1 and latency gates passed", rejection_reason: "", failure_reason: "", superseded_reason: "", integrity_status: "verified", integrity_error: "", lifecycle: [],
        parent_model_id: "email-tfidf-lr-20260828T000000Z-parent001", model_family: "tfidf-logistic-regression", tokenizer_version: "jieba-default-v1", feature_version: "tfidf-v1", training_dataset_version: "feedback-20260829-v3",
        trained_at: "2026-08-29T00:00:00Z", training_started_at: "2026-08-28T23:58:00Z", training_finished_at: "2026-08-29T00:00:00Z", sample_count: 20, new_sample_count: 5,
        category_counts: { work: 12, subscription: 8 }, account_counts: { "derek@stardust.ai": 14, "ops@stardust.ai": 6 }, validation_method: "time-ordered-holdout", accuracy: 0.9, macro_f1: 0.8,
        per_category_metrics: { work: { precision: 0.92, recall: 0.88, f1: 0.9, support: 12, validation_sample_count: 20, minimum_validation_samples: 20, configured_threshold: 0.9 }, subscription: { precision: 0.97, recall: 0.95, f1: 0.96, support: 8 } }, prediction_latency_p50_ms: 1.2, prediction_latency_p95_ms: 2.4,
        artifact_sha256: "a".repeat(64),
      },
      { model_id: "email-tfidf-lr-20260830T000000Z-candidate1", model_version: "email-tfidf-lr-20260830T000000Z-candidate1", status: "candidate", status_reason: "awaiting promotion decision", trained_at: "2026-08-30T00:00:00Z", training_started_at: "2026-08-29T23:59:00Z", training_finished_at: "2026-08-30T00:00:00Z", sample_count: 24, new_sample_count: 4, category_counts: {}, account_counts: {}, validation_method: "time-ordered-holdout", accuracy: 0.88, macro_f1: 0.79, per_category_metrics: {}, prediction_latency_p50_ms: 1.3, prediction_latency_p95_ms: 2.7, artifact_sha256: "b".repeat(64), candidate_reason: "awaiting promotion decision", promotion_reason: "", rejection_reason: "", failure_reason: "", superseded_reason: "", integrity_status: "verified", integrity_error: "", lifecycle: [] },
      { model_id: "email-tfidf-lr-20260827T000000Z-rejected01", model_version: "email-tfidf-lr-20260827T000000Z-rejected01", status: "rejected", status_reason: "subscription precision below 0.95", trained_at: "2026-08-27T00:00:00Z", training_started_at: "2026-08-26T23:59:00Z", training_finished_at: "2026-08-27T00:00:00Z", sample_count: 18, new_sample_count: 3, category_counts: {}, account_counts: {}, validation_method: "time-ordered-holdout", accuracy: 0.8, macro_f1: 0.7, per_category_metrics: {}, prediction_latency_p50_ms: 1.1, prediction_latency_p95_ms: 2.2, artifact_sha256: "c".repeat(64), candidate_reason: "candidate validation pending", promotion_reason: "", rejection_reason: "subscription precision below 0.95", failure_reason: "", superseded_reason: "", integrity_status: "verified", integrity_error: "", lifecycle: [] },
      { model_id: "email-tfidf-lr-20260826T000000Z-failed0001", model_version: "email-tfidf-lr-20260826T000000Z-failed0001", status: "failed", status_reason: "training run failed", trained_at: "2026-08-26T00:00:00Z", training_started_at: "2026-08-25T23:59:00Z", training_finished_at: "2026-08-26T00:00:00Z", sample_count: 16, new_sample_count: 2, category_counts: {}, account_counts: {}, validation_method: "time-ordered-holdout", accuracy: 0, macro_f1: 0, per_category_metrics: {}, prediction_latency_p50_ms: 0, prediction_latency_p95_ms: 0, artifact_sha256: "d".repeat(64), candidate_reason: "candidate validation pending", promotion_reason: "", rejection_reason: "", failure_reason: "classifier artifact write failed", superseded_reason: "", integrity_status: "verified", integrity_error: "", lifecycle: [] },
    ], registry_issues: [] } });
    getEmailClassification.mockResolvedValue({
      ok: true,
      item: { id: "2", subject: "订阅邮件", sender: "news@example.com", preview: "每周资讯", category: "subscription", confidence: 0.99, margin: 0.2, probabilities: { subscription: 0.99 }, model_version: "email-tfidf-lr-20260829T000000Z-feedface", config_version: "email-v7", classification_source: "model", current_action_plan_id: "email-action-plan:full-id-002", action_plan: { action_plan_id: "email-action-plan:full-id-002", action_plan_version: 7, actions: ["label", "archive", "unsubscribe"] }, received_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T00:00:00Z" },
      observability: [{ kind: "unsubscribe", operation: "unsubscribe", lifecycle_version: "email_unsubscribe_audited_v2", task_id: 42, task_status: "done", consumer_run_ids: [101], audit_run_ids: [102], status: "done", receipt_id: "receipt-2", result_text: "退订成功\n[REDACTED_URL]", evidence: "最终结果页：已成功退订", observation_digest: "digest-2", steps: [{ sequence: 1, operation: "open_entry", state: "done", reference: "receipt-2" }] }],
      meta: { snapshot_at: "2026-08-29T00:00:00Z" },
    });
  });

  it("shows model suggestions in pending feedback and records a human choice", async () => {
    const user = userEvent.setup();
    renderEmail("/email?tab=pending_feedback");

    expect(await screen.findByRole("heading", { name: "需要确认" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^重要/ }));
    expect(confirmEmailClassification).not.toHaveBeenCalled();
    listEmailClassifications.mockResolvedValue({ items: [], meta: { total: 0, snapshot_at: "" } });
    await user.click(screen.getByRole("button", { name: "保存为「重要」并继续 →" }));

    expect(confirmEmailClassification).toHaveBeenCalledWith("1", "important", "email-feedback:1", null);
    expect(await screen.findByText("邮件分类反馈已保存")).toBeInTheDocument();
    expect(screen.queryByText("需要确认")).not.toBeInTheDocument();
  });

  it("shows ranked pending evidence and attachment metadata without attachment contents or paths", async () => {
    const user = userEvent.setup();
    renderEmail("/email?tab=pending_feedback");
    await user.click(await screen.findByText("查看分类依据"));
    const evidence = await screen.findByRole("region", { name: "需要确认 分类证据" });
    expect(screen.getByText("邮件摘要")).toBeInTheDocument();
    expect(evidence).toHaveTextContent("前两名概率差：4.0%");
    const alternatives = within(evidence).getByRole("list", { name: "模型候选" });
    expect(within(alternatives).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["工作 61.0%", "重要 57.0%"]);
    expect(screen.getByText("quarterly-report.pdf · 248 KB")).toBeInTheDocument();
    expect(evidence).not.toHaveTextContent("ATTACHMENT-BODY-MUST-NOT-RENDER");
    expect(evidence).not.toHaveTextContent("/private/mail/quarterly-report.pdf");
  });

  it("shows category definitions while asking for feedback", async () => {
    const user = userEvent.setup();
    renderEmail("/email?tab=pending_feedback");

    await user.click(await screen.findByRole("button", { name: "账单" }));
    expect(screen.getByText("发票、账单、付款、续费")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "订阅" }));
    expect(screen.getByText("用户不希望继续接收的批量订阅")).toBeInTheDocument();
  });

  it("prevents duplicate saves and preserves selection after a failed request", async () => {
    const user = userEvent.setup();
    const pending = deferred<unknown>();
    confirmEmailClassification.mockReturnValueOnce(pending.promise);
    renderEmail("/email?tab=pending_feedback");
    await user.click(await screen.findByRole("button", { name: "工作" }));
    await user.click(screen.getByRole("button", { name: "保存为「工作」并继续 →" }));
    expect(screen.getByRole("button", { name: "正在保存…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "重要" })).toBeDisabled();
    pending.reject(new Error("保存失败，请重试"));
    expect(await screen.findByRole("alert")).toHaveTextContent("保存失败，请重试");
    expect(screen.getByRole("button", { name: "工作" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "保存为「工作」并继续 →" })).toBeEnabled();
    expect(confirmEmailClassification).toHaveBeenCalledTimes(1);
  });

  it("uses server totals for pagination and advances to the next email after save", async () => {
    const user = userEvent.setup();
    const seeded = await listEmailClassifications();
    const first = seeded.items[0];
    const second = { ...first, id: "2", subject: "下一封邮件" };
    listEmailClassifications.mockResolvedValue({ items: [first, second], meta: { ...seeded.meta, total: 23 } });
    renderEmail("/email?tab=pending_feedback");
    expect(await screen.findByText("第 1 / 2 页 · 共 23 封")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "工作" }));
    listEmailClassifications.mockResolvedValue({ items: [second], meta: { ...seeded.meta, total: 22 } });
    await user.click(screen.getByRole("button", { name: "保存为「工作」并继续 →" }));
    expect(await screen.findByRole("heading", { name: "下一封邮件" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "下一页" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(listEmailClassifications).toHaveBeenLastCalledWith("pending_feedback", {page:2,page_size:20}, expect.any(AbortSignal)));
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
    expect(await screen.findAllByText("email-tfidf-lr-20260829T000000Z-active0001")).toHaveLength(3);
    expect(screen.getByText(/待训练样本：2/)).toBeInTheDocument();
    expect(screen.getByText(/20（新增 5）/)).toBeInTheDocument();
  });

  it("shows complete lifecycle, coverage, validation, metric, latency, and artifact evidence for every model status", async () => {
    renderEmail("/email?tab=learning");

    const active = await screen.findByRole("article", { name: "模型 email-tfidf-lr-20260829T000000Z-active0001" });
    expect(active).toHaveTextContent("active");
    expect(active).toHaveTextContent("2026");
    expect(active).toHaveTextContent("20（新增 5）");
    expect(active).toHaveTextContent("工作（work）：12");
    expect(active).toHaveTextContent("derek@stardust.ai：14");
    expect(active).toHaveTextContent("time-ordered-holdout");
    expect(active).toHaveTextContent("工作（work）");
    expect(active).toHaveTextContent("precision：92.0%");
    expect(active).toHaveTextContent("support：12");
    expect(active).toHaveTextContent("validation_sample_count：20");
    expect(active).toHaveTextContent("minimum_validation_samples：20");
    expect(active).not.toHaveTextContent("validation_sample_count：2000.0%");
    expect(active).toHaveTextContent("configured_threshold：90.0%");
    expect(active).toHaveTextContent("P50 1.2 ms / P95 2.4 ms");
    expect(active).toHaveTextContent("a".repeat(64));
    expect(active).toHaveTextContent("candidate validation pending");
    expect(active).toHaveTextContent("macro F1 and latency gates passed");
    expect(active).toHaveTextContent("email-tfidf-lr-20260828T000000Z-parent001");
    expect(active).toHaveTextContent("tfidf-logistic-regression");
    expect(active).toHaveTextContent("jieba-default-v1");
    expect(active).toHaveTextContent("tfidf-v1");
    expect(active).toHaveTextContent("feedback-20260829-v3");

    const candidate = screen.getByRole("article", { name: "模型 email-tfidf-lr-20260830T000000Z-candidate1" });
    expect(candidate).toHaveTextContent("状态：candidate");
    expect(candidate).toHaveTextContent("awaiting promotion decision");
    const rejected = screen.getByRole("article", { name: "模型 email-tfidf-lr-20260827T000000Z-rejected01" });
    expect(rejected).toHaveTextContent("状态：rejected");
    expect(rejected).toHaveTextContent("subscription precision below 0.95");
    const failed = screen.getByRole("article", { name: "模型 email-tfidf-lr-20260826T000000Z-failed0001" });
    expect(failed).toHaveTextContent("状态：failed");
    expect(failed).toHaveTextContent("classifier artifact write failed");
  });

  it("shows unsubscribe observability only after opening a processed email detail", async () => {
    const user = userEvent.setup();
    listEmailClassifications.mockResolvedValueOnce({
      items: [{ id: "2", subject: "订阅邮件", sender: "news@example.com", preview: "每周资讯", category: "subscription", confidence: 0.99, margin: 0.2, classification_source: "model", received_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T00:00:00Z" }],
      meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" },
    });
    renderEmail("/email");

    expect(await screen.findByText("订阅邮件")).toBeInTheDocument();
    expect(screen.queryByText("退订成功")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看处理详情" }));

    expect(getEmailClassification).toHaveBeenCalledWith("2");
    const processingEvidence = await screen.findByRole("region", { name: "分类与 ActionPlan 证据" });
    expect(processingEvidence).toHaveTextContent("email-tfidf-lr-20260829T000000Z-feedface");
    expect(processingEvidence).toHaveTextContent("email-v7");
    expect(processingEvidence).toHaveTextContent("email-action-plan:full-id-002");
    expect(processingEvidence).toHaveTextContent("版本 7");
    expect(processingEvidence).toHaveTextContent("label、archive、unsubscribe");
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
    expect(screen.queryByText(/记录时间/)).not.toBeInTheDocument();
    expect(screen.queryByText(new RegExp(["Consumer", "direct"].join("-"))))
      .not.toBeInTheDocument();
  });

  it("preserves and submits required label and move parameters", async () => {
    const user = userEvent.setup();
    listEmailConfigs.mockResolvedValueOnce({
      items: [{
        category: "important", description: "Priority mail", threshold: 0.95,
        actions: ["label", "move"],
        action_parameters: {
          label: { labels: ["Priority", "CEO"] },
          move: { target_folder: "Archive/Priority" },
        },
        enabled: true, config_version: "email-v4", updated_at: "2026-08-29T00:00:00Z",
      }],
      meta: { snapshot_at: "2026-08-29T00:00:00Z" },
    });
    saveEmailConfig.mockResolvedValue({
      ok: true,
      item: { category: "important", description: "Priority mail", threshold: 0.95, actions: ["label", "move"], action_parameters: { label: { labels: ["Board"] }, move: { target_folder: "Archive/Priority" } }, enabled: true, config_version: "email-v4", updated_at: "2026-08-29T00:00:00Z" },
      message: "邮件配置已保存",
    });
    renderEmail("/email?tab=config");

    const labels = await screen.findByRole("textbox", { name: "标签" });
    expect(labels).toHaveValue("Priority, CEO");
    expect(screen.getByRole("textbox", { name: "目标文件夹" })).toHaveValue("Archive/Priority");

    await user.click(screen.getByRole("button", { name: "label" }));
    expect(screen.queryByRole("textbox", { name: "标签" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "label" }));
    const clearedLabels = screen.getByRole("textbox", { name: "标签" });
    expect(clearedLabels).toHaveValue("");
    await user.type(clearedLabels, "Board");
    await user.click(screen.getByRole("button", { name: "保存本地配置" }));

    expect(saveEmailConfig).toHaveBeenCalledWith("important", {
      description: "Priority mail",
      threshold: 0.95,
      actions: ["move", "label"],
      action_parameters: {
        label: { labels: ["Board"] },
        move: { target_folder: "Archive/Priority" },
      },
      enabled: true,
      config_version: "email-v4",
    });
  });

  it("offers unsubscribe only for the subscription category", async () => {
    const user = userEvent.setup();
    renderEmail("/email?tab=config");

    const unsubscribe = await screen.findByRole("button", { name: "unsubscribe" });
    expect(unsubscribe).toBeDisabled();
    expect(screen.getByText("退订只适用于订阅邮件。" )).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "订阅" }));
    expect(screen.getByRole("button", { name: "unsubscribe" })).toBeEnabled();
  });

  it("does not retain a legacy unsubscribe selection on a non-subscription category", async () => {
    listEmailConfigs.mockResolvedValueOnce({
      items: [{ category: "important", description: "Legacy config", threshold: 0.95, actions: ["unsubscribe"], action_parameters: {}, enabled: true, config_version: "email-v1", updated_at: "2026-09-05T00:00:00Z" }],
      meta: { snapshot_at: "2026-09-05T00:00:00Z" },
    });
    renderEmail("/email?tab=config");

    await waitFor(() => expect(screen.getByRole("textbox", { name: "描述" })).toHaveValue("Legacy config"));
    const unsubscribe = await screen.findByRole("button", { name: "unsubscribe" });
    expect(unsubscribe).toBeDisabled();
    expect(unsubscribe).toHaveAttribute("aria-pressed", "false");
  });

  it("lays out primary configuration fields as a responsive form grid", async () => {
    renderEmail("/email?tab=config");

    const description = await screen.findByRole("textbox", { name: "描述" });
    const fieldGrid = description.closest(".email-config-field-grid");

    expect(fieldGrid).not.toBeNull();
    expect(within(fieldGrid as HTMLElement).getByRole("spinbutton", { name: "自动处理阈值" })).toBeInTheDocument();
    expect(within(fieldGrid as HTMLElement).getByRole("textbox", { name: "配置版本" })).toBeInTheDocument();
    expect(workbenchStyles).toMatch(/\.email-config-field-grid\s*\{[^}]*display:\s*grid/s);
    expect(workbenchStyles).toMatch(/\.email-config-field\s*\{[^}]*display:\s*grid/s);
  });

  it("does not send an invalid label action without labels", async () => {
    const user = userEvent.setup();
    renderEmail("/email?tab=config");

    await user.click(await screen.findByRole("button", { name: "label" }));
    await user.click(screen.getByRole("button", { name: "保存本地配置" }));

    expect(saveEmailConfig).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("请至少填写一个标签");
  });

  it.each(["", "-0.01", "1.01"])("rejects invalid threshold %j before calling the API", async (value) => {
    const user = userEvent.setup();
    renderEmail("/email?tab=config");
    const threshold = await screen.findByRole("spinbutton", { name: "自动处理阈值" });
    await waitFor(() => expect(threshold).toBeEnabled());
    await user.clear(threshold);
    if (value) await user.type(threshold, value);
    await user.click(screen.getByRole("button", { name: "保存本地配置" }));

    expect(saveEmailConfig).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("阈值必须是 0 到 1 之间的数字");
  });

  it("rejects a blank config version before calling the API", async () => {
    const user = userEvent.setup();
    renderEmail("/email?tab=config");
    const version = await screen.findByRole("textbox", { name: "配置版本" });
    await waitFor(() => expect(version).toBeEnabled());
    await user.clear(version);
    await user.click(screen.getByRole("button", { name: "保存本地配置" }));

    expect(saveEmailConfig).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("请填写配置版本");
  });

  it("locks configuration controls until load completes and while a save is pending", async () => {
    const user = userEvent.setup();
    const loading = deferred<{ items: never[]; meta: { snapshot_at: string } }>();
    const saving = deferred<{ ok: boolean; item: { category: string; description: string; threshold: number; actions: string[]; action_parameters: Record<string, Record<string, unknown>>; enabled: boolean; config_version: string; updated_at: string }; message: string }>();
    listEmailConfigs.mockReturnValueOnce(loading.promise);
    saveEmailConfig.mockReturnValueOnce(saving.promise);
    renderEmail("/email?tab=config");

    const save = screen.getByRole("button", { name: "保存本地配置" });
    expect(save).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "描述" })).toBeDisabled();
    loading.resolve({ items: [], meta: { snapshot_at: "2026-08-29T00:00:00Z" } });
    await waitFor(() => expect(save).toBeEnabled());
    await user.click(save);

    expect(save).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "描述" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "工作" })).toBeDisabled();
    saving.resolve({ ok: true, item: { category: "important", description: "", threshold: 0.9, actions: [], action_parameters: {}, enabled: true, config_version: "email-v1", updated_at: "2026-08-29T00:00:00Z" }, message: "saved" });
    expect(await screen.findByText(/配置已保存/)).toBeInTheDocument();
    await waitFor(() => expect(save).toBeEnabled());
  });

  it("keeps archive, move, and trash mutually exclusive", async () => {
    const user = userEvent.setup();
    renderEmail("/email?tab=config");
    const archive = await screen.findByRole("button", { name: "archive" });
    await waitFor(() => expect(archive).toBeEnabled());
    const move = screen.getByRole("button", { name: "move" });
    const trash = screen.getByRole("button", { name: "trash" });

    await user.click(archive);
    expect(archive).toHaveAttribute("aria-pressed", "true");
    await user.click(move);
    expect(archive).toHaveAttribute("aria-pressed", "false");
    expect(move).toHaveAttribute("aria-pressed", "true");
    await user.click(trash);
    expect(move).toHaveAttribute("aria-pressed", "false");
    expect(trash).toHaveAttribute("aria-pressed", "true");
  });

  it("clears a move target when another terminal action removes move", async () => {
    const user = userEvent.setup();
    renderEmail("/email?tab=config");
    const move = await screen.findByRole("button", { name: "move" });
    await waitFor(() => expect(move).toBeEnabled());

    await user.click(move);
    const target = screen.getByRole("textbox", { name: "目标文件夹" });
    await user.type(target, "Archive/Old");
    await user.click(screen.getByRole("button", { name: "archive" }));
    expect(screen.queryByRole("textbox", { name: "目标文件夹" })).not.toBeInTheDocument();
    await user.click(move);

    expect(screen.getByRole("textbox", { name: "目标文件夹" })).toHaveValue("");
  });

  it("supports accessible tab state and keyboard navigation", async () => {
    const user = userEvent.setup();
    renderEmail("/email");
    const processed = screen.getByRole("tab", { name: "已处理" });
    const pending = screen.getByRole("tab", { name: "待反馈" });
    expect(processed).toHaveAttribute("tabindex", "0");
    expect(pending).toHaveAttribute("tabindex", "-1");
    expect(processed).toHaveAttribute("aria-controls", "email-panel-processed");
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", "email-tab-processed");

    processed.focus();
    await user.keyboard("{ArrowRight}");
    expect(pending).toHaveFocus();
    expect(pending).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", "email-tab-pending_feedback");
  });

  it("renders registry corruption as explicit evidence without hiding healthy models", async () => {
    listEmailLearning.mockResolvedValueOnce({
      learning: {
        active_model_id: null, pending_examples: 0, last_trained_feedback_count: 0, last_trained_at: null, last_feedback_at: null, active_run_id: null, category_thresholds: {}, models: [],
        registry_issues: [{ model_id: "email-tfidf-lr-corrupt", integrity_status: "corrupt", integrity_error: "artifact_digest_mismatch" }],
      },
    });
    renderEmail("/email?tab=learning");

    expect(await screen.findByRole("alert")).toHaveTextContent("email-tfidf-lr-corrupt（artifact_digest_mismatch）");
  });
});
