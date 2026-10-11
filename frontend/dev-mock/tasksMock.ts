// Synthetic data for looking at the Tasks pages without touching the service
// database. `npm run dev:mock` serves these under /api/console/tasks/*; every
// other /api call is proxied to the running console. Names and content are made
// up. Add `?mock=empty`, `?mock=error` or `?mock=slow` to a page URL to see its
// empty, failed and loading states.
import type { IncomingMessage, ServerResponse } from "node:http";
import type { Plugin } from "vite";
import type { BusinessAttentionDetail, BusinessAttentionSummary, BusinessProjectDetail, BusinessProjectSummary, BusinessTaskDetail, BusinessTaskSignal, BusinessTaskSummary, ConsoleListMeta, ProjectContext, SourceCitation } from "../src/api/console";

type Row = Record<string, unknown>;

const minute = 60_000;
const ago = (minutes: number) => new Date(Date.now() - minutes * minute).toISOString();
const backendStamp = (minutes: number) => ago(minutes).slice(0, 19).replace("T", " ");
const snapshot = () => new Date().toISOString();

const anchors = ["美国市场", "AI 会议助手", "财务设备接入"];

function task(id: number, title: string, extra: Partial<BusinessTaskSummary> = {}): BusinessTaskSummary {
  return {
    id: String(id), title, origin: "source", suggested_owner: "", suggestion_reason: "", stage: "candidate", status: "open", commitment_status: "none", owner: "", deadline_at: "", deadline_type: "",
    business_relevance: "unknown", anchor_labels: [], updated_at: ago(id * 37), detail_url: `/tasks/item/${id}`, ...extra,
  };
}

const formal: BusinessTaskSummary[] = [
  task(1, "交付美国客户报价首版", { stage: "formal", commitment_status: "accepted", owner: "王明", deadline_at: "2026-09-28", deadline_type: "committed_deadline_at", business_relevance: "relevant", anchor_labels: ["美国市场"], updated_at: ago(3) }),
  task(2, "确认 Projects 模块上线时间与评审流程，并把结论同步给所有相关负责人和外部合作方，避免再次出现口径不一致的情况", { stage: "formal", status: "waiting", commitment_status: "accepted", owner: "陈思睿", business_relevance: "relevant", anchor_labels: ["AI 会议助手", "美国市场"], updated_at: ago(95) }),
  task(3, "财务设备接入需求评审", { stage: "formal", commitment_status: "assigned_unaccepted", owner: "静宇", business_relevance: "relevant", anchor_labels: ["财务设备接入"], updated_at: ago(60 * 26) }),
  task(4, "整理国庆前客户走访清单", { stage: "formal", status: "done", commitment_status: "completed", owner: "Avery", deadline_at: "2026-09-20", deadline_type: "requested_deadline_at", business_relevance: "relevant", updated_at: ago(60 * 24 * 4) }),
  task(5, "确认海外渠道合同条款", { stage: "formal", commitment_status: "disputed", owner: "Bartholomew Featherstonehaugh-Montgomery", business_relevance: "relevant", anchor_labels: ["美国市场"], updated_at: ago(60 * 24 * 9) }),
  task(6, "去年遗留的合规自查", { stage: "formal", status: "cancelled", commitment_status: "cancelled", owner: "王明", updated_at: new Date(Date.now() - 400 * 86_400_000).toISOString() }),
  task(7, "整理验收材料", { stage: "formal", commitment_status: "assigned_unaccepted", owner: "李四", deadline_at: "2026-10-08", deadline_type: "requested_deadline_at", business_relevance: "relevant", anchor_labels: ["美国市场"], updated_at: ago(15) }),
  task(8, "与客户确认发票接收信息", { origin: "agent_suggestion", stage: "formal", commitment_status: "accepted", owner: "赵六", deadline_at: "2026-10-12", deadline_type: "committed_deadline_at", business_relevance: "relevant", anchor_labels: ["美国市场"], updated_at: ago(6) }),
];

const candidateTitles = [
  "结构化撰写内容产出计划，明确分类、数量及分工", "整理访谈问题清单并发给磊哥确认", "使用真实 Case 测试事项抽取效果并提供 Evil Case 给工程师调优", "国庆前完成事项抽取 Prompt 初版编写并在商务电脑验证",
  "跟进 Recipe 生成不稳定的 Bug 修复进度", "定义 AI 会议辅助及多 Agent 功能的产品目标与客户痛点", "将财务设备接入需求转化为标准产品需求文档", "梳理需求清单，区分产品需求与算法需求",
  "绘制团队协作功能具体界面示例", "发送最新设计文档给磊哥审阅", "收集更多用户诉求并整理优先级表", "下次预约会议时改用其他会议工具",
];
const candidates: BusinessTaskSummary[] = Array.from({ length: 41 }, (_, index) => task(100 + index, `${candidateTitles[index % candidateTitles.length]}${index >= candidateTitles.length ? `（${Math.floor(index / candidateTitles.length) + 1}）` : ""}`, {
  owner: index % 5 === 2 ? "静宇" : index % 7 === 3 ? "发言人" : "",
  deadline_at: index % 9 === 4 ? "2026-10-03" : "",
  deadline_type: index % 9 === 4 ? "requested_deadline_at" : "",
  anchor_labels: index % 6 === 0 ? [anchors[index % 3]] : [],
  updated_at: ago(20 + index * 53),
}));
const suggestions = [
  task(300, "确认客户回款时间", { origin: "agent_suggestion", suggested_owner: "王五", suggestion_reason: "王五负责商务与回款；最新消息仍未明确客户付款计划，建议确认付款时间。", anchor_labels: ["美国市场"], business_relevance: "relevant", updated_at: ago(2) }),
  task(301, "梳理评审阻塞事项与待决策范围", { origin: "agent_suggestion", suggestion_reason: "评审尚无结论，建议先明确影响范围；当前没有足够证据指定负责人。", anchor_labels: ["AI 会议助手"], business_relevance: "relevant", updated_at: ago(4) }),
];
const allTasks = [...formal, ...candidates, ...suggestions].sort((left, right) => right.updated_at.localeCompare(left.updated_at));

const attention: BusinessAttentionSummary[] = [
  { id: "1", category: "decision", business_area: "海外业务", title: "美国客户要求下周前给出正式报价", why_attention: "客户已两次催问，报价首版仍在制作，超过约定日期会影响后续合同", current_state: "王明已接单，首版预计周五完成", ceo_action: "决定是否接受客户提出的 15% 折扣区间", anchor_label: "美国市场", linked_task_count: 3, updated_at: ago(12), detail_url: "/tasks/attention/1" },
  { id: "2", category: "decision", business_area: "财务", title: "财务设备接入方案需要选型", why_attention: "两套方案成本相差一倍，团队意见不一致", current_state: "评审会已开，结论待定", ceo_action: "选定方案并明确预算上限", anchor_label: "财务设备接入", linked_task_count: 2, updated_at: ago(60 * 5), detail_url: "/tasks/attention/2" },
  { id: "3", category: "push", business_area: "产品", title: "Projects 模块上线时间未定", why_attention: "评审流程还没有走完，负责人之间没有对齐", current_state: "等待评审结论", ceo_action: "拉通会议，逼近一个明确的上线日期", anchor_label: "AI 会议助手", linked_task_count: 1, updated_at: ago(60 * 27), detail_url: "/tasks/attention/3" },
  { id: "4", category: "watch", business_area: "研发", title: "Recipe 生成不稳定", why_attention: "偶发失败，已影响两个演示", current_state: "工程师在复现", ceo_action: "当前无需你处理，观察故障复现与演示恢复结果", anchor_label: "", linked_task_count: 1, updated_at: ago(60 * 30), detail_url: "/tasks/attention/4" },
  { id: "5", category: "watch", business_area: "市场", title: "国庆前客户走访进度", why_attention: "走访清单已完成，实际预约较少", current_state: "已联系 4 家，2 家确认", ceo_action: "当前无需处理", anchor_label: "美国市场", linked_task_count: 4, updated_at: ago(60 * 24 * 3), detail_url: "/tasks/attention/5" },
  { id: "6", category: "fyi", business_area: "人力", title: "两名新同事下周入职", why_attention: "入职材料齐备", current_state: "工位与账号已准备", ceo_action: "当前无需处理", anchor_label: "", linked_task_count: 0, updated_at: ago(60 * 24 * 5), detail_url: "/tasks/attention/6" },
  { id: "7", category: "fyi", business_area: "法务", title: "海外渠道合同已通过法审并且法务给出了一长串需要留意的条款，其中涉及数据出境、违约金上限、争议解决地和知识产权归属", why_attention: "合同已法审", current_state: "等待对方签署", ceo_action: "当前无需处理", anchor_label: "美国市场", linked_task_count: 1, updated_at: ago(60 * 24 * 8), detail_url: "/tasks/attention/7" },
  { id: "8", category: "watch", business_area: "客户交付", title: "客户回款节奏存在不确定性", why_attention: "客户尚未确认付款时间，先观察业务风险，无需创造催款任务。", current_state: "客户仍在内部确认付款安排", ceo_action: "当前无需你处理", anchor_label: "客户回款观察", linked_task_count: 0, updated_at: ago(7), detail_url: "/tasks/attention/8" },
];

const cite = (signal_id: number, source_ref: string, source_excerpt: string): SourceCitation => ({ signal_id, source_ref, source_excerpt });
const accountability = cite(501, "project-meeting-501", "张三总负责交付与验收，李四负责材料，王五负责商务与回款。");
const payment = cite(502, "customer-chat-502", "客户付款时间仍在内部确认，暂时不能承诺日期。");
const projectContexts: Record<string, ProjectContext | null> = {
  "1": { goal: "完成首期交付、客户验收与回款", scope: "首期报价、交付材料及验收，不含二期开发", overall_owner: { person_user_id: "zhang", person_name: "张三", responsibility: "交付与验收", evidence: [accountability] }, responsibilities: [
    { person_user_id: "li", person_name: "李四", responsibility: "整理验收材料", evidence: [accountability] },
    { person_user_id: "wang", person_name: "王五", responsibility: "商务与回款", evidence: [accountability] },
  ], facts: [
    { key: "payment", text: "客户回款时间存在不确定性", evidence: [payment], date_type: "", date_value: "" },
    { key: "materials", text: "验收材料要求十月八日提交，尚未收到负责人承诺", evidence: [cite(503, "meeting-503", "要求李四在十月八日前提交验收材料。")], date_type: "requested_deadline_at", date_value: "2026-10-08" },
  ] },
  "2": { goal: "完成会议助手评审并验证客户价值", scope: "会议纪要与行动项体验", overall_owner: null, responsibilities: [], facts: [{ key: "review", text: "评审结论尚未明确", evidence: [cite(504, "review-504", "评审还没有形成结论。")], date_type: "", date_value: "" }] },
  "3": null,
  "4": { goal: "掌握客户回款节奏", scope: "付款安排的最新事实", overall_owner: null, responsibilities: [], facts: [{ key: "payment", text: "客户仍在内部确认付款安排", evidence: [payment], date_type: "", date_value: "" }] },
};
const projectTaskIds: Record<string, number[]> = { "1": [1, 2, 5, 7, 8], "2": [2], "3": [3], "4": [] };
const projects: BusinessProjectSummary[] = [
  { id: "1", title: "美国市场拓展", registry_source: "经营会确认美国市场拓展为正式项目", attention_reason: "回款时间尚未明确，材料任务完成不代表风险已解除。" },
  { id: "2", title: "AI 会议助手", registry_source: "产品周会决议", attention_reason: "评审未形成结论，可能影响上线安排。" },
  { id: "3", title: "财务设备接入", registry_source: "财务负责人提出并经确认", attention_reason: "" },
  { id: "4", title: "客户回款观察", registry_source: "客户沟通中登记", attention_reason: "当前无需 CEO 动作，持续观察付款安排变化。" },
].map((project) => {
  const context = projectContexts[project.id];
  const tasks = formal.filter((task) => projectTaskIds[project.id].includes(Number(task.id)));
  return { ...project, canonical_anchor_id: Number(project.id), confirmed_task_count: tasks.length, detail_url: `/tasks/project/${project.id}`, overall_owner: context?.overall_owner?.person_name || "", overall_responsibility: context?.overall_owner?.responsibility || "", responsible_content: context?.overall_owner?.responsibility || "", goal: context?.goal || "", current_status: context?.facts.map((fact) => fact.text).join("\n") || "", deadline: "", source_title: "", reporting_period: "", source_url: "", source_excerpt: "", updated_at: ago(10), open_task_count: tasks.filter((task) => !["done", "cancelled"].includes(task.status)).length, done_task_count: tasks.filter((task) => task.status === "done").length };
});
const projectCandidates: Row[] = Array.from({ length: 25 }, (_, index) => ({
  id: String(200 + index), title: `${["海外渠道拓展", "客户成功体系", "数据出境合规", "招聘体系升级", "内部工具整合"][index % 5]}${index >= 5 ? `（线索 ${index + 1}）` : ""}`,
  reason: `${2 + (index % 4)} 项关联任务指向同一件事，尚未有人明确确认`, status: "proposed", cluster_id: index + 1, provisional: true, confirmed_project_id: null,
}));

const signal = (id: number, sourceType: string, evidence: string, context: Row = {}): BusinessTaskSignal => ({
  id, source_type: sourceType, source_ref: `ref-${id}`, source_time: "2026-09-24T11:29:21+08:00", conversation_id: "", conversation_title: "", author_user_id: "", author_name: "", author_kind: "unknown",
  evidence_text: evidence, context_json: JSON.stringify(context), dedupe_key: `k-${id}`, created_at: "2026-09-24T23:48:47+00:00",
});

const followUps: Row[] = [
  { id: 7, revision: 3, status: "draft", question_text: "报价首版目前进展到哪一步？周五能否交付？", target_kind: "group", owner_name: "王明", scheduled_at: "2026-09-26 09:00:00" },
  { id: 6, revision: 2, status: "sent", question_text: "请确认客户要求的折扣区间是否已经沟通", target_kind: "direct", owner_name: "王明", scheduled_at: "2026-09-22 09:00:00", sent_at: "2026-09-22 09:02:11" },
  { id: 5, revision: 2, status: "failed", question_text: "上周约定的报价模板是否已经更新？", target_kind: "direct", owner_name: "王明", scheduled_at: "2026-09-20 09:00:00", send_result_json: JSON.stringify({ error: "timeout" }) },
  { id: 4, revision: 2, status: "cancelled", question_text: "旧的催办", target_kind: "direct", owner_name: "王明", scheduled_at: "2026-09-18 09:00:00", suppressed_reason: "Task 已被新信息更新" },
];

function taskDetail(id: number): BusinessTaskDetail | null {
  const summary = allTasks.find((row) => row.id === String(id));
  if (!summary) return null;
  const base = { summary, suggestion: null, description: summary.stage === "formal" ? "来源中记录的工作事项；指派与承诺以各自证据为准。" : "来源线索尚未形成正式任务。", formal_basis: "", owner_user_id: "", missing_evidence: [] as string[], date_evidence: [] as Row[], relations: [] as Row[], clusters: [] as Row[], anchors: [] as Row[], official_projects: [] as BusinessProjectSummary[], follow_ups: [] as Row[], dingtalk_todos: [] as Row[] };
  const meeting = { meeting: { title: "每周产品进展同步", durationMicros: 1972792000, startTimeISO: "2026-09-24T11:29:21+08:00", todos: { result: { actions: ["整理访谈问题清单"] } } }, uuid: "0d3f-mock" };
  const created = { id: 1, task_id: String(id), event_type: "created", reason: "Candidate task recorded from source evidence.", created_at: backendStamp(60 * 30) };
  if (id === 8) return { ...base, suggestion: { reason: "王五负责商务与回款，建议确认发票接收信息。", suggested_owner_user_id: "wang", suggested_owner_name: "王五", responsibility_evidence: [accountability], basis_evidence: [payment] }, evidence: [{ role: "acceptance", signal: signal(508, "dingtalk", "赵六：这件事我来负责，十月十二日前完成。") }], date_evidence: [{ id: 508, date_type: "committed_deadline_at", value_at: "2026-10-12", raw_phrase: "十月十二日前完成" }], events: [created, { id: 508, event_type: "promoted", reason: "人类正式承接", created_at: ago(6) }], official_projects: [projects[0]] };
  if (summary.origin === "agent_suggestion") return { ...base, description: "基于已保存项目职责与最新来源提出的建议，尚未指派。", suggestion: { reason: summary.suggestion_reason, suggested_owner_user_id: id === 300 ? "wang" : "", suggested_owner_name: summary.suggested_owner, responsibility_evidence: id === 300 ? [accountability] : [], basis_evidence: id === 300 ? [payment] : [cite(504, "review-504", "评审还没有形成结论。")] }, evidence: [{ role: "discovery", signal: { ...signal(id === 300 ? 502 : 504, id === 300 ? "dingtalk" : "meeting", id === 300 ? payment.source_excerpt : "评审还没有形成结论。"), source_ref: id === 300 ? payment.source_ref : "review-504" } }], events: [created], official_projects: [projects[id === 300 ? 0 : 1]] };
  if (id === 1) return { ...base,
    formal_basis: "explicit_assignment", missing_evidence: ["负责人接受的原话"],
    evidence: [
      { role: "assignment", signal: signal(11, "meeting", "磊哥：这个报价首版，王明周五前给到客户。", { work_item_title: "交付美国客户报价首版" }) },
      { role: "acceptance", signal: signal(12, "dingtalk", "好的，我周五前交付。") },
      { role: "discovery", signal: signal(13, "ai_minutes", JSON.stringify(meeting), { work_item_title: "每周产品进展同步行动项" }) },
    ],
    date_evidence: [{ id: 6, date_type: "committed_deadline_at", value_at: "2026-09-28", raw_phrase: "周五前交付", created_at: backendStamp(60 * 25) }, { id: 7, date_type: "requested_deadline_at", value_at: "2026-09-27", raw_phrase: "客户要求下周一前", created_at: backendStamp(60 * 26) }],
    events: [{ ...created, reason: "Assignment recorded" }, { id: 2, task_id: "1", event_type: "promoted", reason: "有明确指派", created_at: backendStamp(60 * 25) }, { id: 3, task_id: "1", event_type: "commitment_changed", reason: "王明接受", created_at: backendStamp(60 * 3) }],
    relations: [{ id: 1, relation_type: "depends_on", status: "confirmed", title: "财务设备接入需求评审" }, { id: 2, relation_type: "related_to", status: "proposed", title: "确认海外渠道合同条款" }],
    clusters: [{ id: 1, title: "美国客户报价相关工作", reason: "同一客户、同一交付物" }],
    anchors: [{ id: 1, title: "美国市场" }], official_projects: [projects[0]], follow_ups: followUps,
    dingtalk_todos: [{ id: 1, title: "交付美国客户报价首版", status: "open", created_at: backendStamp(60 * 24) }],
  };
  if (id === 2) return { ...base, evidence: [{ role: "commitment", signal: signal(21, "meeting", "陈思睿：评审后再定上线时间。") }], events: [{ ...created, id: 9 }], follow_ups: [followUps[0]] };
  if (id === 7) return { ...base, evidence: [{ role: "assignment", signal: { ...signal(503, "meeting", "要求李四在十月八日前提交验收材料。"), source_ref: "meeting-503" } }], date_evidence: [{ id: 503, date_type: "requested_deadline_at", value_at: "2026-10-08", raw_phrase: "十月八日前提交" }], events: [created], official_projects: [projects[0]] };
  return { ...base, evidence: [{ role: "discovery", signal: signal(id, "ai_minutes", JSON.stringify(meeting), { work_item_title: `${summary.title}行动项` }) }], events: [created] };
}

const attentionDetail = (id: string): BusinessAttentionDetail | null => {
  const summary = attention.find((row) => row.id === id);
  if (!summary) return null;
  const sourceSignals = id === "1" ? [
    signal(31, "meeting", "客户希望下周前看到正式报价，并提出折扣区间。", { source_link: "https://example.com/synthetic/minutes-31" }),
    { ...signal(32, "dingtalk", "目前报价首版还在制作，需要等成本核算结果。"), source_time: "2026-09-25T09:15:00+08:00" },
  ] : id === "4" ? [
    signal(41, "meeting", "Recipe 生成偶发失败，已经影响两个演示。", { source_link: "https://example.com/synthetic/recipe-review-41" }),
    { ...signal(42, "dingtalk", "工程师仍在复现故障，需要观察修复后演示能否恢复。"), source_time: "2026-09-25T09:15:00+08:00" },
  ] : id === "8" ? [{ ...signal(502, "dingtalk", payment.source_excerpt, { source_link: "https://example.com/synthetic/customer-payment" }), source_ref: payment.source_ref }] : [];
  return {
    summary, anchor: { id: 1, title: summary.anchor_label },
    assessment: sourceSignals.length ? {
      material_trigger: "risk_escalation", inference: summary.why_attention,
      evidence: sourceSignals.map((source) => ({ signal_id: source.id, source_ref: source.source_ref, source_excerpt: source.evidence_text, source_time: source.source_time, source_link: JSON.parse(source.context_json).source_link || "" })),
    } : {},
    linked_tasks: id === "4" ? [candidates[4]] : formal.slice(0, summary.linked_task_count),
    evidence_signals: sourceSignals,
    events: [{ id: 1, event_type: "opened", created_at: backendStamp(60 * 30) }, { id: 2, event_type: "category_changed", reason: "客户再次催问", created_at: backendStamp(60 * 3) }],
  };
};

const projectDetail = (id: string): BusinessProjectDetail | null => {
  const summary = projects.find((row) => row.id === id);
  if (!summary) return null;
  const context = projectContexts[id];
  const evidence = id === "1" ? [
    { ...signal(501, "meeting", accountability.source_excerpt, { source_link: "https://example.com/synthetic/project-meeting" }), source_ref: accountability.source_ref },
    { ...signal(502, "dingtalk", payment.source_excerpt, { source_link: "https://example.com/synthetic/customer-payment" }), source_ref: payment.source_ref },
    { ...signal(503, "meeting", "要求李四在十月八日前提交验收材料。"), source_ref: "meeting-503" },
  ] : id === "2" ? [{ ...signal(504, "meeting", "评审还没有形成结论。"), source_ref: "review-504" }] : id === "4" ? [{ ...signal(502, "dingtalk", payment.source_excerpt), source_ref: payment.source_ref }] : [];
  const revisions = context ? [{ id: Number(id) * 10, project_id: Number(id), context, evidence_signal_ids: evidence.map((source) => source.id), created_at: summary.updated_at }] : [];
  const meta = (total: number): ConsoleListMeta => ({ page: 1, page_size: 20, total, next_cursor: "", has_more: false, snapshot_at: snapshot() });
  return { summary, anchor: { id: Number(id), title: summary.title }, context, responsibilities: context?.responsibilities || [], confirmed_tasks: formal.filter((task) => projectTaskIds[id].includes(Number(task.id))), suggestions: suggestions.filter((task) => task.id === (id === "1" ? "300" : id === "2" ? "301" : "")), evidence_signals: evidence, context_revisions: revisions, evidence_meta: meta(evidence.length), context_revision_meta: meta(revisions.length) };
};

function page<T>(rows: T[], url: URL, extra: Row = {}) {
  const size = Number(url.searchParams.get("page_size") || 20);
  const current = Math.max(1, Number(url.searchParams.get("page") || 1));
  const start = (current - 1) * size;
  return { items: rows.slice(start, start + size), meta: { snapshot_at: snapshot(), page: current, page_size: size, total: rows.length, next_cursor: start + size < rows.length ? String(current + 1) : "", has_more: start + size < rows.length }, ...extra };
}

function route(pathname: string, url: URL, scenario: string, method: string, body: Row): { status: number; body: unknown } | null {
  const send = (body: unknown, status = 200) => ({ status, body });
  const meta = { snapshot_at: snapshot() };
  const q = (url.searchParams.get("q") || "").toLowerCase();
  let match: RegExpMatchArray | null;
  if (pathname === "/api/console/tasks/attention") {
    const category = url.searchParams.get("category");
    return send(page(scenario === "empty" ? [] : attention.filter((row) => !category || row.category === category), url));
  }
  if (pathname === "/api/console/tasks/all") {
    const stage = url.searchParams.get("stage");
    const status = url.searchParams.get("status");
    const owner = url.searchParams.get("owner");
    const created = url.searchParams.get("sort") === "created";
    const rows = scenario === "empty" ? [] : allTasks.filter((row) => (!stage || row.stage === stage) && (!status || row.status === status) && (!owner || Boolean(row.owner) === (owner === "assigned")) && (!q || String(row.title).toLowerCase().includes(q)));
    return send(page(created ? [...rows].sort((left, right) => Number(right.id) - Number(left.id)) : rows, url));
  }
  if (pathname === "/api/console/tasks/projects") {
    const rows = scenario === "empty" ? [] : projects.filter((row) => !q || String(row.title).toLowerCase().includes(q));
    const candidateRows = scenario === "empty" ? [] : projectCandidates.filter((row) => !q || String(row.title).toLowerCase().includes(q));
    const candidateUrl = new URL(url);
    candidateUrl.searchParams.set("page", url.searchParams.get("candidate_page") || "1");
    candidateUrl.searchParams.set("page_size", url.searchParams.get("candidate_page_size") || "20");
    const candidates = page(candidateRows, candidateUrl);
    return send({ ...page(rows, url), candidates: candidates.items, candidate_meta: candidates.meta });
  }
  if ((match = pathname.match(/^\/api\/console\/tasks\/items\/(\d+)$/))) {
    const item = taskDetail(Number(match[1]));
    return item ? send({ item, meta }) : send({ detail: "Business task not found" }, 404);
  }
  if ((match = pathname.match(/^\/api\/console\/tasks\/attention\/(\d+)$/))) {
    const item = attentionDetail(match[1]);
    return item ? send({ item, meta }) : send({ detail: "Attention item not found" }, 404);
  }
  if ((match = pathname.match(/^\/api\/console\/tasks\/projects\/(\d+)$/))) {
    const item = projectDetail(match[1]);
    return item ? send({ item, meta }) : send({ detail: "Project not found" }, 404);
  }
  if (pathname.startsWith("/api/console/tasks/legacy-projects/")) {
    return send({ item: { project: { id: 9, title: "旧版海外渠道项目", status: "active", goal: "早期用旧流程登记的项目，未经确认。", facts: [{ id: 1, description: "2025 年底曾与两家渠道商接触", source: "旧记录", created_at: "2025-12-20 10:00:00" }] }, todos: [], updates: [] }, meta });
  }
  if ((match = pathname.match(/^\/api\/console\/tasks\/items\/(\d+)\/candidate-decision$/)) && method === "POST") {
    const row = allTasks.find((candidate) => candidate.id === match![1]);
    if (!row) return send({ ok: false, code: "not_found", message: "这个任务不存在", details: {} }, 404);
    if (row.stage !== "candidate") return send({ ok: false, code: "not_applicable", message: "只有候选任务可以忽略或恢复", details: {} }, 409);
    row.status = body.action === "restore" ? "open" : "cancelled";
    row.updated_at = ago(0);
    return send({ ok: true, item: { status: row.status }, message: body.action === "restore" ? "已恢复这个候选任务" : "已忽略这个候选任务", meta: { updated_at: snapshot() } });
  }
  if (/^\/api\/console\/tasks\/items\/\d+\/follow-ups\/\d+\/send$/.test(pathname)) return send({ ok: true, message: "催办已发送（演示数据，未真正发送）", meta: { updated_at: snapshot() } });
  return null;
}

export function tasksMock(): Plugin {
  return {
    name: "tasks-mock-data",
    configureServer(server) {
      server.middlewares.use((request: IncomingMessage, response: ServerResponse, next: () => void) => {
        const url = new URL(request.url || "/", "http://localhost");
        if (!url.pathname.startsWith("/api/console/tasks")) return next();
        const scenario = new URL(String(request.headers.referer || "http://localhost/"), "http://localhost").searchParams.get("mock") || "";
        const respond = (body: Row) => {
          const result = scenario === "error" ? { status: 500, body: { detail: "演示：服务暂时不可用" } } : route(url.pathname, url, scenario, request.method || "GET", body);
          if (!result) return next();
          response.statusCode = result.status;
          response.setHeader("content-type", "application/json");
          response.end(JSON.stringify(result.body));
        };
        const chunks: Buffer[] = [];
        request.on("data", (chunk: Buffer) => chunks.push(chunk));
        request.on("end", () => {
          const raw = Buffer.concat(chunks).toString();
          const body = raw ? JSON.parse(raw) as Row : {};
          if (scenario === "slow") setTimeout(() => respond(body), 4000); else respond(body);
        });
      });
    },
  };
}
