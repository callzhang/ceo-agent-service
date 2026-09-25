import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

const getBusinessTaskDetail = vi.hoisted(() => vi.fn());
const sendBusinessTaskFollowUp = vi.hoisted(() => vi.fn());
vi.mock("../api/console", async (importOriginal) => ({ ...(await importOriginal<typeof import("../api/console")>()), getBusinessTaskDetail, sendBusinessTaskFollowUp }));
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

  it("sends a follow-up only when Derek clicks it, and shows a withdrawn one without a button", async () => {
    const detail = (followUps: Array<Record<string, unknown>>) => ({ item: {
      summary: { id: "42", title: "交付报价首版", stage: "formal", status: "open", commitment_status: "accepted", owner: "王明", deadline_at: "", business_relevance: "relevant", anchor_labels: [], updated_at: "2026-09-24", detail_url: "/tasks/item/42" },
      description: "", evidence: [], date_evidence: [], events: [], relations: [], clusters: [], anchors: [], official_projects: [], dingtalk_todos: [], follow_ups: followUps,
    }, meta: { snapshot_at: "2026-09-25" } });
    const pending = { id: 7, revision: 3, status: "draft", question_text: "请确认报价首版进展", target_kind: "group", owner_name: "王明", scheduled_at: "2026-09-26 09:00:00" };
    const withdrawn = { id: 6, revision: 2, status: "cancelled", question_text: "旧的催办", target_kind: "direct", owner_name: "王明", scheduled_at: "2026-09-20 09:00:00", suppressed_reason: "Task 已被新信息更新" };
    getBusinessTaskDetail.mockResolvedValue(detail([pending, withdrawn]));
    sendBusinessTaskFollowUp.mockResolvedValue({ ok: true, message: "催办已发送", meta: { updated_at: "" } });
    render(<MemoryRouter><TaskDetailPage taskId="42" /></MemoryRouter>);

    expect(await screen.findByText("请确认报价首版进展")).toBeInTheDocument();
    expect(screen.getByText(/Task 已被新信息更新/)).toBeInTheDocument();
    const buttons = screen.getAllByRole("button", { name: /发送/ });
    expect(buttons).toHaveLength(1);
    expect(sendBusinessTaskFollowUp).not.toHaveBeenCalled();

    fireEvent.click(buttons[0]);

    expect(await screen.findByText("催办已发送")).toBeInTheDocument();
    expect(sendBusinessTaskFollowUp).toHaveBeenCalledWith("42", "7", 3);
  });
});
