import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

const getBusinessTaskDetail = vi.hoisted(() => vi.fn());
vi.mock("../api/console", async (importOriginal) => ({ ...(await importOriginal<typeof import("../api/console")>()), getBusinessTaskDetail }));
import { TaskDetailPage } from "./TaskDetailPage";

describe("TaskDetailPage", () => {
  it("shows Task stage, commitment and source evidence without pretending it is a Project", async () => {
    getBusinessTaskDetail.mockResolvedValue({ item: {
      summary: { id: "42", title: "交付报价首版", stage: "formal", status: "open", commitment_status: "assigned_unaccepted", owner: "王明", deadline_at: "2026-09-28", business_relevance: "relevant", anchor_labels: ["美国市场"], updated_at: "2026-09-24", detail_url: "/tasks/item/42" },
      description: "为美国客户准备", evidence: [{ role: "assignment", signal: { id: 3, source_type: "meeting", evidence_text: "王明周一交付报价首版" } }], date_evidence: [{ id: 6, date_type: "committed_deadline_at", value_at: "2026-09-28", raw_phrase: "下周一交付" }], events: [{ id: 5, event_type: "assignment", created_at: "2026-09-24" }], relations: [], clusters: [], anchors: [], official_projects: [], follow_ups: [], dingtalk_todos: [],
    }, meta: { snapshot_at: "2026-09-24" } });
    render(<MemoryRouter><TaskDetailPage taskId="42" /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "交付报价首版" })).toBeInTheDocument();
    expect(screen.getByText("已指派，未接受")).toBeInTheDocument();
    expect(screen.getByText("王明周一交付报价首版")).toBeInTheDocument();
    expect(screen.getByText("下周一交付")).toBeInTheDocument();
    expect(screen.getByText("美国市场")).toBeInTheDocument();
    expect(screen.queryByText("Project details")).not.toBeInTheDocument();
  });
});
