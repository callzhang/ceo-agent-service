import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

const getBusinessAttentionDetail = vi.hoisted(() => vi.fn());
vi.mock("../api/console", async (importOriginal) => ({ ...(await importOriginal<typeof import("../api/console")>()), getBusinessAttentionDetail }));
import { TaskAttentionDetailPage } from "./TaskAttentionDetailPage";

describe("TaskAttentionDetailPage", () => {
  it("shows lifecycle and linked Tasks, retaining a no-action decision", async () => {
    getBusinessAttentionDetail.mockResolvedValue({ item: {
      summary: { id: "7", category: "watch", business_area: "海外业务", title: "美国客户报价", why_attention: "客户在等", current_state: "首版制作中", ceo_action: "当前无需处理", anchor_label: "美国市场", linked_task_count: 1, updated_at: "2026-09-24", detail_url: "/tasks/attention/7" },
      linked_tasks: [{ id: "42", title: "交付报价首版", stage: "formal", status: "open", commitment_status: "accepted", owner: "王明", deadline_at: "", business_relevance: "relevant", anchor_labels: ["美国市场"], updated_at: "2026-09-24", detail_url: "/tasks/item/42" }],
      events: [{ id: 3, event_type: "opened", created_at: "2026-09-24" }], evidence_signals: [],
    }, meta: { snapshot_at: "2026-09-24" } });
    render(<MemoryRouter><TaskAttentionDetailPage attentionId="7" /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "美国客户报价" })).toBeInTheDocument();
    expect(screen.getByText("当前无需处理")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "交付报价首版" })).toHaveAttribute("href", "/tasks/item/42");
    expect(screen.getByText("opened")).toBeInTheDocument();
  });
});
