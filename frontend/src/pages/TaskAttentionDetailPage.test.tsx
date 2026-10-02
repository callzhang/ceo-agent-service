import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getBusinessAttentionDetail = vi.hoisted(() => vi.fn());
vi.mock("../api/console", async (importOriginal) => ({ ...(await importOriginal<typeof import("../api/console")>()), getBusinessAttentionDetail }));
import { TaskAttentionDetailPage } from "./TaskAttentionDetailPage";

describe("TaskAttentionDetailPage", () => {
  beforeEach(() => vi.clearAllMocks());
  it("shows lifecycle and linked Tasks, retaining a no-action decision", async () => {
    getBusinessAttentionDetail.mockResolvedValue({ item: {
      summary: { id: "7", category: "watch", business_area: "海外业务", title: "美国客户报价", why_attention: "客户在等", current_state: "首版制作中", ceo_action: "当前无需处理", anchor_label: "美国市场", linked_task_count: 1, updated_at: "2026-09-24", detail_url: "/tasks/attention/7" },
      linked_tasks: [{ id: "42", title: "交付报价首版", stage: "formal", status: "open", commitment_status: "accepted", owner: "王明", deadline_at: "", business_relevance: "relevant", anchor_labels: ["美国市场"], updated_at: "2026-09-24", detail_url: "/tasks/item/42" }],
      assessment: {}, events: [{ id: 3, event_type: "opened", created_at: "2026-09-24" }], evidence_signals: [],
    }, meta: { snapshot_at: "2026-09-24" } });
    render(<MemoryRouter><TaskAttentionDetailPage attentionId="7" /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "美国客户报价" })).toBeInTheDocument();
    expect(screen.getByText("当前无需处理")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "交付报价首版" })).toHaveAttribute("href", "/tasks/item/42");
    expect(screen.getByText("开始关注")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "关注点" })).toHaveTextContent("当前无需处理");
    expect(screen.queryByRole("region", { name: "你的动作" })).not.toBeInTheDocument();
    expect(screen.getByText("暂无结构化来源事实。历史记录未保存引文依据。")).toBeInTheDocument();
    expect(screen.getByText("客户在等")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "当前位置" })).toHaveTextContent("Tasks需关注关注事项");
  });

  it.each(["watch", "decision", "push"])("renders saved quotes separately from inference for %s", async (category) => {
    getBusinessAttentionDetail.mockResolvedValue({ item: {
      summary: { id: "8", category, business_area: "Product", title: "Delivery risk", why_attention: "Old judgment", current_state: "Waiting", ceo_action: "当前无需你处理", anchor_label: "Project", updated_at: "2026-09-24", detail_url: "/tasks/attention/8" },
      assessment: { material_trigger: "risk_escalation", inference: "Agent inferred delivery impact", evidence: [
        { signal_id: 31, source_ref: "minutes-31", source_excerpt: "First exact quote。", source_time: "2026-09-23T10:00:00Z", source_link: "https://example.com/minutes/31" },
        { signal_id: 32, source_ref: "chat-32", source_excerpt: "Second exact quote！", source_time: "2026-09-24T11:00:00Z", source_link: "" },
      ] }, linked_tasks: [], evidence_signals: [], events: [],
    }, meta: { snapshot_at: "2026-09-24" } });
    render(<MemoryRouter><TaskAttentionDetailPage attentionId="8" /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "来源事实" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Agent 判断" })).toBeInTheDocument();
    for (const quote of ["First exact quote。", "Second exact quote！", "minutes-31", "chat-32", "Agent inferred delivery impact"]) expect(screen.getByText(quote)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "打开来源" })).toHaveAttribute("href", "https://example.com/minutes/31");
    expect(document.querySelector('time[datetime="2026-09-23T10:00:00Z"]')).toBeInTheDocument();
    expect(document.querySelector('time[datetime="2026-09-24T11:00:00Z"]')).toBeInTheDocument();
    expect(screen.queryByText("Old judgment")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: category === "watch" ? "关注点" : "你的动作" })).toBeInTheDocument();
    expect(screen.getByText("暂无关联任务。")).toBeInTheDocument();
    expect(screen.queryByText("已完成")).not.toBeInTheDocument();
  });

  it("shows loading while the detail request is pending", () => {
    getBusinessAttentionDetail.mockReturnValue(new Promise(() => {}));
    render(<MemoryRouter><TaskAttentionDetailPage attentionId="8" /></MemoryRouter>);
    expect(screen.getByRole("status", { name: "正在加载" })).toBeInTheDocument();
  });

  it("shows the detail request error", async () => {
    getBusinessAttentionDetail.mockRejectedValue(new Error("读取失败"));
    render(<MemoryRouter><TaskAttentionDetailPage attentionId="8" /></MemoryRouter>);
    expect(await screen.findByRole("alert")).toHaveTextContent("读取失败");
  });
});
