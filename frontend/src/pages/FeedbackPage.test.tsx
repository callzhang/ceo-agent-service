import { createEvent, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listFeedback = vi.hoisted(() => vi.fn());
const syncFeedback = vi.hoisted(() => vi.fn());
vi.mock("../api/console", () => ({
  listFeedback,
  syncFeedback,
  displayValue: (value: unknown) => typeof value === "string" ? value || "未提供" : JSON.stringify(value),
}));

import { FeedbackPage } from "./FeedbackPage";

describe("FeedbackPage", () => {
  beforeEach(() => {
    listFeedback.mockResolvedValue({
      items: [
        {
          id: "feedback-1",
          attempt_id: "8308",
          status: "processing",
          processing_status: "processing",
          rating: "不太有用",
          comment: "请修复这个反馈",
          context: "产品群 · Mina",
          created_at: "2026-08-29T00:00:00Z",
          summary: "修复任务状态",
          references: [],
          batch_id: "batch-1",
          processing_task_id: "task-1",
        },
      ],
      meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" },
    });
  });

  it("renders processing state and links to the attempt, Workbench task, and batch", async () => {
    render(<MemoryRouter><FeedbackPage /></MemoryRouter>);

    expect(await screen.findByText("处理中")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Attempt" })).toHaveAttribute("href", "/attempts/8308");
    expect(screen.getByRole("link", { name: "Workbench task" })).toHaveAttribute("href", "/?task=task-1");
    expect(screen.getByRole("link", { name: "Processing batch" })).toHaveAttribute("href", "/api/console/feedback/batches/batch-1");
    expect(screen.queryByRole("button", { name: "标记已处理" })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "处理中" })).toBeInTheDocument();
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
