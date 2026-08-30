import { createEvent, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FeedbackItem, FeedbackProcessingRound } from "../api/console";

const listFeedback = vi.hoisted(() => vi.fn());
const getFeedbackDetail = vi.hoisted(() => vi.fn());
const reopenFeedback = vi.hoisted(() => vi.fn());
const syncFeedback = vi.hoisted(() => vi.fn());
vi.mock("../api/console", () => ({
  listFeedback,
  getFeedbackDetail,
  reopenFeedback,
  syncFeedback,
  displayValue: (value: unknown) => typeof value === "string" ? value || "未提供" : JSON.stringify(value),
}));

import { FeedbackPage } from "./FeedbackPage";

const meta = { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" };

function round(roundNumber: number, overrides: Partial<FeedbackProcessingRound> = {}): FeedbackProcessingRound {
  return {
    id: roundNumber,
    feedback_key: "feedback-1",
    round_number: roundNumber,
    batch_id: `batch-${roundNumber}`,
    status: "resolved",
    workbench_task_id: `task-${roundNumber}`,
    workbench_turn_id: `turn-${roundNumber}`,
    attempt_id: 8308,
    agent_run_id: 444 + roundNumber,
    commit_sha: `${roundNumber}`.repeat(40),
    test_evidence: { command: "pnpm test", passed: 12 + roundNumber },
    restart_evidence: { new_pid: 1200 + roundNumber },
    health_evidence: { ok: true },
    backlog_evidence: { processing: 0, failed: 0, retryable: 0 },
    receipt_version: 2,
    note: "",
    started_at: `2026-08-2${roundNumber}T01:00:00Z`,
    resolved_at: `2026-08-2${roundNumber}T02:00:00Z`,
    reopened_at: roundNumber === 1 ? "2026-08-29T03:00:00Z" : "",
    reopen_reason: roundNumber === 1 ? "第一次修复尚未覆盖重新打开后的场景。" : "",
    created_at: `2026-08-2${roundNumber}T01:00:00Z`,
    updated_at: `2026-08-2${roundNumber}T02:00:00Z`,
    ...overrides,
  };
}

function feedback(overrides: Partial<FeedbackItem> = {}): FeedbackItem {
  const history = [round(2), round(1)];
  return {
    id: "feedback-1",
    feedback_key: "feedback-1",
    attempt_id: "8308",
    status: "resolved",
    processing_status: "resolved",
    rating: "不太有用",
    comment: "请修复这个反馈",
    context: "产品群 · Mina",
    created_at: "2026-08-29T00:00:00Z",
    summary: "修复任务状态",
    references: [
      { label: "attempt#8308", route: "/attempts/8308" },
      { label: "run#445", route: "/attempts/8308/execution/consumer" },
    ],
    batch_id: "batch-2",
    processing_task_id: "task-2",
    current_processing: history[0],
    processing_history: history,
    ...overrides,
  };
}

function page(items: FeedbackItem[], pendingCount = 0) {
  return { items, pending_count: pendingCount, meta: { ...meta, total: items.length } };
}

describe("FeedbackPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listFeedback.mockResolvedValue(page([feedback()]));
    getFeedbackDetail.mockResolvedValue({ item: feedback(), meta: { snapshot_at: "2026-08-29T00:00:00Z" } });
  });

  it("shows the reopen action only for resolved feedback", async () => {
    const { unmount } = render(<MemoryRouter><FeedbackPage /></MemoryRouter>);
    expect(await screen.findByRole("button", { name: "重新打开反馈" })).toBeInTheDocument();

    unmount();
    listFeedback.mockResolvedValue(page([feedback({ status: "processing", processing_status: "processing" })]));
    render(<MemoryRouter><FeedbackPage /></MemoryRouter>);
    await screen.findByText("处理中", { selector: ".status-badge" });
    expect(screen.queryByRole("button", { name: "重新打开反馈" })).not.toBeInTheDocument();
  });

  it("requires a factual nonblank reason and supports accessible cancel", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><FeedbackPage /></MemoryRouter>);

    await user.click(await screen.findByRole("button", { name: "重新打开反馈" }));
    const dialog = screen.getByRole("dialog", { name: "重新打开反馈" });
    expect(within(dialog).getByLabelText("重新打开原因")).toHaveFocus();
    expect(within(dialog).getByText("请写明此前为何过早完成，以及还缺少哪项可核验结果。")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "确认重新打开" })).toBeDisabled();
    await user.type(within(dialog).getByLabelText("重新打开原因"), "   ");
    expect(within(dialog).getByRole("button", { name: "确认重新打开" })).toBeDisabled();
    await user.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog", { name: "重新打开反馈" })).not.toBeInTheDocument();
  });

  it("prevents duplicate submissions and shows loading feedback", async () => {
    const user = userEvent.setup();
    let finish!: (value: unknown) => void;
    reopenFeedback.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    render(<MemoryRouter><FeedbackPage /></MemoryRouter>);

    await user.click(await screen.findByRole("button", { name: "重新打开反馈" }));
    await user.type(screen.getByLabelText("重新打开原因"), "测试未覆盖重新处理后的第二轮结果。");
    await user.click(screen.getByRole("button", { name: "确认重新打开" }));
    expect(screen.getByRole("button", { name: "正在重新打开…" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "正在重新打开…" }));
    expect(reopenFeedback).toHaveBeenCalledTimes(1);
    expect(reopenFeedback).toHaveBeenCalledWith("feedback-1", "测试未覆盖重新处理后的第二轮结果。");
    finish({ ok: true, item: { status: "pending", processing_history: [round(2), round(1)] }, message: "反馈已重新打开", meta: { updated_at: "2026-08-30T00:00:00Z" } });
  });

  it("closes on success, refreshes the pending projection and shows success feedback", async () => {
    const user = userEvent.setup();
    const history = [round(2), round(1)];
    listFeedback
      .mockResolvedValueOnce(page([feedback()]))
      .mockResolvedValueOnce(page([feedback({ status: "pending", processing_status: "pending", batch_id: "", processing_task_id: "", current_processing: null, processing_history: history })], 1));
    reopenFeedback.mockResolvedValue({ ok: true, item: { feedback_key: "feedback-1", status: "pending", current_processing: null, processing_history: history }, message: "反馈已重新打开", meta: { updated_at: "2026-08-30T00:00:00Z" } });
    render(<MemoryRouter><FeedbackPage /></MemoryRouter>);

    await user.click(await screen.findByRole("button", { name: "重新打开反馈" }));
    await user.type(screen.getByLabelText("重新打开原因"), "服务重启前就被标记完成，尚未验证新进程。");
    await user.click(screen.getByRole("button", { name: "确认重新打开" }));

    expect(await screen.findByRole("status", { name: "操作成功" })).toHaveTextContent("反馈已重新打开，已回到待处理列表。");
    expect(screen.queryByRole("dialog", { name: "重新打开反馈" })).not.toBeInTheDocument();
    expect(screen.getByText("待处理 1")).toBeInTheDocument();
    expect(screen.getByText("待处理", { selector: ".status-badge" })).toBeInTheDocument();
    expect(listFeedback).toHaveBeenCalledTimes(2);
  });

  it("keeps the reason after an error and permits retry", async () => {
    const user = userEvent.setup();
    reopenFeedback
      .mockRejectedValueOnce(new Error("反馈历史不完整"))
      .mockResolvedValueOnce({ ok: true, item: { status: "pending", processing_history: [] }, message: "反馈已重新打开", meta: { updated_at: "2026-08-30T00:00:00Z" } });
    render(<MemoryRouter><FeedbackPage /></MemoryRouter>);

    await user.click(await screen.findByRole("button", { name: "重新打开反馈" }));
    const reason = "提交已存在，但没有完成服务重启核验。";
    await user.type(screen.getByLabelText("重新打开原因"), reason);
    await user.click(screen.getByRole("button", { name: "确认重新打开" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("反馈历史不完整");
    expect(screen.getByLabelText("重新打开原因")).toHaveValue(reason);
    await user.click(screen.getByRole("button", { name: "确认重新打开" }));
    await waitFor(() => expect(reopenFeedback).toHaveBeenCalledTimes(2));
  });

  it("renders newest-first immutable round summaries and retains persisted links", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><FeedbackPage /></MemoryRouter>);

    expect(await screen.findByRole("link", { name: "Attempt" })).toHaveAttribute("href", "/attempts/8308");
    expect(screen.getByRole("link", { name: "Workbench task" })).toHaveAttribute("href", "/?task=task-2");
    expect(screen.getByRole("link", { name: "Processing batch" })).toHaveAttribute("href", "/api/console/feedback/batches/batch-2");
    await user.click(screen.getAllByRole("button", { name: "展开详情" }).at(-1)!);

    const history = screen.getByRole("list", { name: "处理历史" });
    const entries = within(history).getAllByRole("listitem");
    expect(entries).toHaveLength(2);
    expect(entries[0]).toHaveTextContent("第 2 轮");
    expect(entries[1]).toHaveTextContent("第 1 轮");
    expect(entries[0]).toHaveTextContent("测试：pnpm test · 14 项通过");
    expect(entries[0]).toHaveTextContent("重启：新进程 1202");
    expect(entries[0]).toHaveTextContent("健康：通过；积压 processing 0 / failed 0 / retryable 0");
    expect(entries[1]).toHaveTextContent("重新打开原因：第一次修复尚未覆盖重新打开后的场景。");
    expect(within(history).getByRole("link", { name: "batch-2" })).toHaveAttribute("href", "/api/console/feedback/batches/batch-2");
    expect(screen.getAllByRole("link", { name: "attempt#8308" }).some((link) => link.getAttribute("href") === "/attempts/8308")).toBe(true);
    expect(screen.getByRole("link", { name: "run#445" })).toHaveAttribute("href", "/attempts/8308/execution/consumer");
    expect(screen.getAllByRole("link", { name: "查看 Workbench task" })[0]).toHaveAttribute("href", "/?task=task-2");
  });

  it("loads history from the item detail when the list projection omits it", async () => {
    const user = userEvent.setup();
    listFeedback.mockResolvedValue(page([feedback({ current_processing: undefined, processing_history: undefined })]));
    getFeedbackDetail.mockResolvedValue({ item: feedback(), meta: { snapshot_at: "2026-08-30T00:00:00Z" } });
    render(<MemoryRouter><FeedbackPage /></MemoryRouter>);

    await screen.findByRole("button", { name: "重新打开反馈" });
    await user.click(screen.getAllByRole("button", { name: "展开详情" }).at(-1)!);
    await user.click(screen.getByRole("button", { name: "加载处理历史" }));

    expect(getFeedbackDetail).toHaveBeenCalledWith("feedback-1");
    expect(await screen.findByRole("list", { name: "处理历史" })).toBeInTheDocument();
  });

  it("keeps the batch destination as a native navigation outside the SPA router", async () => {
    render(<MemoryRouter><FeedbackPage /></MemoryRouter>);

    const link = await screen.findByRole("link", { name: "Processing batch" });
    expect(link.tagName).toBe("A");
    let preventedBeforeDocument = false;
    document.addEventListener("click", (event) => {
      preventedBeforeDocument = event.defaultPrevented;
      event.preventDefault();
    }, { once: true });
    fireEvent(link, createEvent.click(link));
    expect(preventedBeforeDocument).toBe(false);
  });
});
