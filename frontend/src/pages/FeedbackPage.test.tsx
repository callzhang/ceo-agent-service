import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listFeedback = vi.hoisted(() => vi.fn());
const resolveFeedback = vi.hoisted(() => vi.fn());
const syncFeedback = vi.hoisted(() => vi.fn());
vi.mock("../api/console", () => ({
  listFeedback,
  resolveFeedback,
  syncFeedback,
  displayValue: (value: unknown) => typeof value === "string" ? value : JSON.stringify(value),
}));

import { FeedbackPage } from "./FeedbackPage";

describe("FeedbackPage", () => {
  beforeEach(() => {
    listFeedback.mockResolvedValue({
      items: [{ id: "feedback-1", attempt_id: "836", status: "pending", rating: "很有用", comment: "需要补充下一步。", context: "Friday · Shawn · 请补充下一步", created_at: "2026-08-29T00:00:00Z" }],
      meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" },
      pending_count: 1,
    });
    resolveFeedback.mockResolvedValue({ ok: true, message: "已标记为已处理" });
    syncFeedback.mockResolvedValue({ ok: true, message: "已同步最新反馈" });
  });

  it("keeps the legacy compact feedback table and inline action", async () => {
    render(<MemoryRouter><FeedbackPage /></MemoryRouter>);

    expect(await screen.findByRole("region", { name: "用户反馈工作区" })).toBeInTheDocument();
    expect(screen.getByText("待处理 1")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "用户反馈" })).toBeInTheDocument();
    expect(screen.getByText("需要补充下一步。")).toBeInTheDocument();
    expect(screen.getByText("Friday · Shawn · 请补充下一步")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "标记已处理" })).toBeInTheDocument();
    expect(screen.queryByText("展开详情")).not.toBeInTheDocument();
  });
});
