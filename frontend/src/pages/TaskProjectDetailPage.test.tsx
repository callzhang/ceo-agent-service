import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BusinessProjectDetail, BusinessTaskSummary, ProjectContext } from "../api/console";

const getBusinessProjectDetail = vi.hoisted(() => vi.fn<typeof import("../api/console").getBusinessProjectDetail>());
vi.mock("../api/console", async (importOriginal) => ({ ...(await importOriginal<typeof import("../api/console")>()), getBusinessProjectDetail }));
import { TaskProjectDetailPage } from "./TaskProjectDetailPage";

const citation = (signal_id: number, source_ref: string, source_excerpt: string) => ({ signal_id, source_ref, source_excerpt });
const owner = { person_user_id: "zhang", person_name: "张三", responsibility: "交付与验收", evidence: [citation(31, "meeting-31", "张三总负责交付与验收。") ] };
const role = { person_user_id: "wang", person_name: "王五", responsibility: "商务与回款", evidence: [citation(32, "report-32", "王五负责商务与回款。") ] };
const paymentCitation = citation(33, "chat-33", "客户还没有确认回款日期。");
const materialsCitation = citation(34, "meeting-34", "要求李四在十月八日提交材料。");
const context: ProjectContext = {
  goal: "完成项目验收并回款", scope: "首期交付及验收，不含二期开发", overall_owner: owner, responsibilities: [role],
  facts: [
    { key: "payment", text: "回款时间存在不确定性", evidence: [paymentCitation], date_type: "", date_value: "" },
    { key: "materials", text: "材料提交要求", evidence: [materialsCitation], date_type: "requested_deadline_at", date_value: "2026-10-08" },
  ],
};
const sourceTask: BusinessTaskSummary = { id: "42", title: "整理验收材料", origin: "source", suggested_owner: "", suggestion_reason: "", stage: "formal", status: "done", commitment_status: "completed", owner: "李四", deadline_at: "2026-10-08", deadline_type: "requested_deadline_at", business_relevance: "relevant", anchor_labels: ["客户项目"], updated_at: "2026-10-04", detail_url: "/tasks/item/42" };
const suggestion: BusinessTaskSummary = { ...sourceTask, id: "43", title: "确认客户回款时间", origin: "agent_suggestion", suggested_owner: "王五", suggestion_reason: "客户回款日期尚未确认，建议由商务负责人确认。", stage: "candidate", status: "open", commitment_status: "none", owner: "", deadline_at: "", deadline_type: "", detail_url: "/tasks/item/43" };
const pageMeta = { page: 1, page_size: 20, total: 4, next_cursor: "", has_more: false, snapshot_at: "2026-10-04" };
function projectDetail(): BusinessProjectDetail {
  return {
    summary: { id: "2", title: "客户交付项目", registry_source: "经营会确认", canonical_anchor_id: 3, confirmed_task_count: 1, detail_url: "/tasks/project/2", overall_owner: "张三", overall_responsibility: "交付与验收", attention_reason: "回款风险仍需观察，材料完成不代表风险已解除。", responsible_content: "交付与验收", goal: context.goal, deadline: "", current_status: context.facts.map((fact) => fact.text).join("\n"), source_title: "", reporting_period: "", source_url: "", open_task_count: 0, done_task_count: 1, updated_at: "2026-10-04T08:00:00Z" },
    anchor: { id: 3, title: "客户项目" }, context, responsibilities: [role], confirmed_tasks: [sourceTask], suggestions: [suggestion],
    evidence_signals: [owner.evidence[0], role.evidence[0], paymentCitation, materialsCitation].map((source) => ({ id: source.signal_id, source_type: source.signal_id === 33 ? "dingtalk" : "meeting", source_ref: source.source_ref, source_time: "2026-10-03T09:00:00Z", evidence_text: `保存的完整原文 ${source.signal_id}\n${source.source_excerpt}`, context_json: JSON.stringify({ source_link: `https://example.com/source/${source.signal_id}` }) })),
    context_revisions: [{ id: 9, project_id: 2, context, evidence_signal_ids: [31, 32, 33, 34], created_at: "2026-10-04T08:00:00Z" }],
    evidence_meta: pageMeta, context_revision_meta: { ...pageMeta, total: 1 },
  };
}

describe("TaskProjectDetailPage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("puts persisted project accountability before source Tasks, suggestions and history", async () => {
    getBusinessProjectDetail.mockResolvedValue({ item: projectDetail(), meta: { snapshot_at: "2026-10-04" } });
    render(<MemoryRouter><TaskProjectDetailPage projectId="2" /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "客户交付项目" })).toBeInTheDocument();
    for (const text of ["总负责人", "张三", "交付与验收", "人员分工", "王五", "商务与回款", context.goal, context.scope, "回款时间存在不确定性"]) expect(screen.getAllByText(text).length).toBeGreaterThan(0);
    const headings = screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent);
    expect(headings).toEqual(["项目整体情况", "总负责人及分工", "为什么关注", "来源任务1", "Agent 建议1", "来源与历史"]);
    const sourceSection = screen.getByRole("heading", { name: "来源任务1" }).closest("details")!;
    expect(within(sourceSection).getByRole("link", { name: "整理验收材料" })).toHaveAttribute("href", "/tasks/item/42");
    expect(within(sourceSection).getByText(/任务执行统计：1 个/)).toHaveTextContent("已完成 1");
    expect(within(sourceSection).queryByText("确认客户回款时间")).not.toBeInTheDocument();
    const suggestedSection = screen.getByRole("heading", { name: "Agent 建议1" }).closest("details")!;
    expect(within(suggestedSection).getByText("建议负责人：王五")).toBeInTheDocument();
    expect(within(suggestedSection).getByText(suggestion.suggestion_reason)).toBeInTheDocument();
    expect(within(suggestedSection).queryByText(/^负责人：王五$/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "自动派发" })).not.toBeInTheDocument();
    expect(screen.getByText(projectDetail().summary.attention_reason)).toBeInTheDocument();
    expect(screen.queryByText("截止/周期")).not.toBeInTheDocument();
    expect(screen.queryByText("权威周报")).not.toBeInTheDocument();
    expect(screen.getAllByText("张三总负责交付与验收。").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "来源：meeting-31" })).toHaveAttribute("href", "https://example.com/source/31");
  });

  it("keeps a zero-Task project readable without inferring missing context", async () => {
    const item = projectDetail();
    getBusinessProjectDetail.mockResolvedValue({ item: { ...item, summary: { ...item.summary, overall_owner: "", overall_responsibility: "", confirmed_task_count: 0, open_task_count: 0, done_task_count: 0 }, context: null, responsibilities: [], confirmed_tasks: [], suggestions: [], context_revisions: [], context_revision_meta: { ...pageMeta, total: 0 } }, meta: { snapshot_at: "2026-10-04" } });
    render(<MemoryRouter><TaskProjectDetailPage projectId="2" /></MemoryRouter>);
    await screen.findByRole("heading", { name: "客户交付项目" });
    expect(screen.getByText("总负责人").nextElementSibling).toHaveTextContent("待明确");
    expect(screen.getByText("暂无关联任务。")).toBeInTheDocument();
    expect(screen.getByText("暂无 Agent 建议。")).toBeInTheDocument();
    expect(screen.getByText(/^保存的完整原文 31/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("李四")).not.toBeInTheDocument();
  });

  it("retains distinct conflicting facts and exact citations, with bounded history metadata", async () => {
    const item = projectDetail();
    const facts = [
      { key: "payment-a", text: "商务预计本月回款", evidence: [citation(33, "source-a", "商务说本月可以回款。")], date_type: "", date_value: "" },
      { key: "payment-b", text: "客户尚未给出付款时间", evidence: [citation(34, "source-b", "客户说付款时间还未确认。")], date_type: "", date_value: "" },
    ];
    getBusinessProjectDetail.mockResolvedValue({ item: { ...item, context: { ...context, facts }, context_revisions: [], evidence_signals: item.evidence_signals.map((signal) => { const fact = facts.find((fact) => fact.evidence[0].signal_id === signal.id); return fact ? { ...signal, source_ref: fact.evidence[0].source_ref, evidence_text: fact.evidence[0].source_excerpt } : signal; }), evidence_meta: { ...pageMeta, total: 30, has_more: true, next_cursor: "" }, context_revision_meta: { ...pageMeta, total: 0 } }, meta: { snapshot_at: "2026-10-04" } });
    render(<MemoryRouter><TaskProjectDetailPage projectId="2" /></MemoryRouter>);
    await screen.findByRole("heading", { name: "客户交付项目" });
    for (const fact of facts) {
      const row = screen.getByText(fact.text).closest("li")!;
      expect(within(row).getByText(fact.evidence[0].source_excerpt)).toBeInTheDocument();
      expect(within(row).getByRole("link", { name: `来源：${fact.evidence[0].source_ref}` })).toBeInTheDocument();
    }
    expect(screen.getByText("已显示 4 条，共 30 条来源证据。")).toBeInTheDocument();
    expect(screen.queryByText("张三 / 王五")).not.toBeInTheDocument();
  });

  it("labels confirmed project source candidates without implying assignment", async () => {
    const item = projectDetail();
    getBusinessProjectDetail.mockResolvedValue({ item: { ...item, summary: { ...item.summary, open_task_count: 1, done_task_count: 0 }, confirmed_tasks: [{ ...sourceTask, stage: "candidate", status: "open", commitment_status: "none" }] }, meta: { snapshot_at: "2026-10-04" } });
    render(<MemoryRouter><TaskProjectDetailPage projectId="2" /></MemoryRouter>);
    const section = (await screen.findByRole("heading", { name: "来源任务1" })).closest("details")!;
    expect(within(section).getByRole("link", { name: "整理验收材料" })).toBeInTheDocument();
    expect(within(section).getByText("候选任务")).toBeInTheDocument();
    expect(within(section).getByText("来源线索")).toBeInTheDocument();
    expect(within(section).getByText(/任务执行统计：1 个/)).toHaveTextContent("进行中 1");
    expect(within(section).getByText("实际安排以任务阶段和承诺为准。")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /已安排任务/ })).not.toBeInTheDocument();
  });

  it("keeps a promoted suggestion in actual task execution statistics and shows the actual owner", async () => {
    const item = projectDetail();
    getBusinessProjectDetail.mockResolvedValue({ item: { ...item, confirmed_tasks: [{ ...sourceTask, origin: "agent_suggestion", owner: "赵六" }], suggestions: [] }, meta: { snapshot_at: "2026-10-04" } });
    render(<MemoryRouter><TaskProjectDetailPage projectId="2" /></MemoryRouter>);
    const section = (await screen.findByRole("heading", { name: "来源任务1" })).closest("details")!;
    expect(within(section).getByText("正式任务")).toBeInTheDocument();
    expect(within(section).getByText(/任务执行统计：1 个/)).toBeInTheDocument();
    expect(within(section).getByText("负责人：赵六")).toBeInTheDocument();
    expect(within(section).getByText("源于 Agent 建议")).toBeInTheDocument();
    expect(within(section).queryByText(/^建议负责人/)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Agent 建议0" })).toBeInTheDocument();
  });

  it("shows a pending state", () => {
    getBusinessProjectDetail.mockReturnValue(new Promise(() => {}));
    render(<MemoryRouter><TaskProjectDetailPage projectId="2" /></MemoryRouter>);
    expect(screen.getByRole("status", { name: "正在加载" })).toBeInTheDocument();
  });

  it("shows a request failure without stale project information", async () => {
    getBusinessProjectDetail.mockRejectedValue(new Error("项目资料读取失败"));
    render(<MemoryRouter><TaskProjectDetailPage projectId="2" /></MemoryRouter>);
    expect(await screen.findByRole("alert")).toHaveTextContent("项目资料读取失败");
  });
});
