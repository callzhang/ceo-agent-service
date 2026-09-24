import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

const getBusinessProjectDetail = vi.hoisted(() => vi.fn());
vi.mock("../api/console", async (importOriginal) => ({ ...(await importOriginal<typeof import("../api/console")>()), getBusinessProjectDetail }));
import { TaskProjectDetailPage } from "./TaskProjectDetailPage";

describe("TaskProjectDetailPage", () => {
  it("shows official registry evidence and confirmed linked Tasks", async () => {
    getBusinessProjectDetail.mockResolvedValue({ item: {
      summary: { id: "2", title: "美国市场拓展", registry_source: "经营会确认美国市场拓展为正式项目", canonical_anchor_id: 3, confirmed_task_count: 1, detail_url: "/tasks/project/2" },
      anchor: { id: 3, title: "美国市场" },
      confirmed_tasks: [{ id: "42", title: "交付报价首版", stage: "formal", status: "open", commitment_status: "accepted", owner: "王明", deadline_at: "", business_relevance: "relevant", anchor_labels: ["美国市场"], updated_at: "2026-09-24", detail_url: "/tasks/item/42" }],
    }, meta: { snapshot_at: "2026-09-24" } });
    render(<MemoryRouter><TaskProjectDetailPage projectId="2" /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "美国市场拓展" })).toBeInTheDocument();
    expect(screen.getByText("经营会确认美国市场拓展为正式项目")).toBeInTheDocument();
    expect(screen.getByText("美国市场")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "交付报价首版" })).toHaveAttribute("href", "/tasks/item/42");
  });
});
