import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
      ],
      meta: { page: 1, page_size: 20, total: 2, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T16:20:00Z" },
    });
  });

  it("shows a red unresolved-count badge and compact expandable issue cards", async () => {
    render(<MemoryRouter initialEntries={["/attention"]}><Routes><Route path="/attention" element={<AttentionPage />} /></Routes></MemoryRouter>);

    expect(await screen.findByLabelText("4 个未解决问题")).toHaveClass("attention-count-badge");
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "待处理问题" })).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "待处理问题" })).not.toBeInTheDocument();
    expect(screen.getByText("Provider timeout")).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "查看详情" })[0]);
    expect(screen.getByText("retryable")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看 Attempt" })).toHaveAttribute("href", "/attempts/1");
  });
});
