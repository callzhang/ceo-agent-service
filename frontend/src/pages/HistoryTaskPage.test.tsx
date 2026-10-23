import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getResource = vi.hoisted(() => vi.fn());
vi.mock("../api/console", () => ({
  getResource,
  displayValue: (value: unknown) => typeof value === "string" ? value : JSON.stringify(value),
}));

import { HistoryTaskPage } from "./HistoryTaskPage";

describe("HistoryTaskPage", () => {
  beforeEach(() => {
    getResource.mockReset();
    getResource.mockResolvedValue({
      item: {
        task: { id: 7, title: "采购审批", channel: "dingtalk", sender: "Avery", trigger_text: "请审批", status: "done", error: "", execution_generation: "g2", created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T01:00:00Z" },
        runs: [
          { id: 1, execution_generation: "g1", current: false, role: "consumer", proposal_revision: 0, turn_attempt: 0, status: "completed", started_at: "2026-08-29T00:00:00Z", completed_at: "" },
          { id: 2, execution_generation: "g2", current: true, role: "audit", proposal_revision: 1, turn_attempt: 0, status: "completed", started_at: "2026-08-29T01:00:00Z", completed_at: "" },
        ],
        attempts: [{ id: 11, action: "send_reply", status: "sent", updated_at: "2026-08-29T01:00:00Z", summary: "已回复", detail_url: "/attempts/11" }],
      },
      meta: { snapshot_at: "2026-08-29T01:00:00Z" },
    });
  });

  it("lists the task's attempts and runs, each attempt opening its detail page", async () => {
    render(<MemoryRouter initialEntries={["/history/tasks/7"]}><Routes><Route path="/history/tasks/:taskId" element={<HistoryTaskPage />} /></Routes></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "采购审批" })).toBeInTheDocument();
    expect(getResource).toHaveBeenCalledWith("/api/console/history/tasks/7", expect.anything());
    expect(screen.getByRole("link", { name: /send_reply/ })).toHaveAttribute("href", "/attempts/11");
    expect(screen.getByRole("region", { name: "执行过程" })).toHaveTextContent("Consumer");
    expect(screen.getByRole("region", { name: "执行过程" })).toHaveTextContent("Audit");
    expect(screen.getByRole("region", { name: "执行过程" })).toHaveTextContent("当前代");
  });
});
