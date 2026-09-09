import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listAttention = vi.hoisted(() => vi.fn());

vi.mock("../api/console", () => ({
  listAttention,
  displayValue: (value: unknown) => typeof value === "string" ? value : JSON.stringify(value) || "未提供",
}));

import { AttentionPage } from "./AttentionPage";

describe("AttentionPage", () => {
  beforeEach(() => {
    listAttention.mockResolvedValue({
      items: [
        { id: "runtime:provider_timeout:worker", category: "Service error", root_cause: "provider_timeout", context: "worker", severity: "error", count: 3, summary: "Provider timeout", error: "retryable", updated_at: "2026-08-29 16:20:00", links: [{ label: "查看 Attempt", href: "/attempts/1" }] },
        { id: "task:owner_missing:Sales", category: "Work item", root_cause: "owner_missing", context: "Sales", severity: "warning", count: 1, summary: "需要补充负责人", error: "owner_missing", updated_at: "2026-08-29 16:18:00", links: [] },
        { id: "scheduled:runtime:42", category: "Scheduled task", root_cause: "scheduled_task_runtime_unavailable", context: "scheduled-task:42", severity: "error", count: 1, summary: "Runtime unavailable", error: "runtime unavailable", updated_at: "2026-08-29 16:17:00", links: [{ label: "查看详情", href: "/scheduled-tasks?id=42" }] },
      ],
      meta: { page: 1, page_size: 20, total: 2, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T16:20:00Z" },
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows a red unresolved-count badge and compact expandable issue cards", async () => {
    render(<MemoryRouter initialEntries={["/attention"]}><Routes><Route path="/attention" element={<AttentionPage />} /></Routes></MemoryRouter>);

    expect(await screen.findByLabelText("5 个未解决问题")).toHaveClass("attention-count-badge");
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "待处理问题" })).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "待处理问题" })).not.toBeInTheDocument();
    expect(screen.getByText("Provider timeout")).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "查看详情" })[0]);
    expect(screen.getByText("retryable")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看 Attempt" })).toHaveAttribute("href", "/attempts/1");
    const scheduledCard = screen.getByText("Runtime unavailable").closest("article");
    expect(scheduledCard).not.toBeNull();
    fireEvent.click(within(scheduledCard as HTMLElement).getByRole("button", { name: "查看详情" }));
    expect(within(scheduledCard as HTMLElement).getByRole("link", { name: "查看详情" })).toHaveAttribute("href", "/scheduled-tasks?id=42");
  });

  it("refreshes the current attention snapshot automatically", async () => {
    vi.useFakeTimers();
    listAttention
      .mockResolvedValueOnce({ items: [{ id: "first", category: "Service error", root_cause: "first", context: "worker", severity: "error", count: 1, summary: "First error", error: "first", updated_at: "2026-08-29 16:20:00", links: [] }], meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T16:20:00Z" } })
      .mockResolvedValueOnce({ items: [], meta: { page: 1, page_size: 20, total: 0, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T16:20:10Z" } });

    render(<MemoryRouter initialEntries={["/attention"]}><Routes><Route path="/attention" element={<AttentionPage />} /></Routes></MemoryRouter>);
    await vi.waitFor(() => expect(screen.getByText("First error")).toBeInTheDocument());

    await vi.advanceTimersByTimeAsync(10_000);

    await vi.waitFor(() => expect(screen.getByText("当前没有待处理问题")).toBeInTheDocument());
    expect(listAttention.mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});
