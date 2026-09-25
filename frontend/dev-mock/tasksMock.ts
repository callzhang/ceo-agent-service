// Synthetic data for looking at the Tasks pages without touching the service
// database. `npm run dev:mock` serves these under /api/console/tasks/*; every
// other /api call is proxied to the running console. Names and content are made
// up. Add `?mock=empty`, `?mock=error` or `?mock=slow` to a page URL to see its
// empty, failed and loading states.
import type { IncomingMessage, ServerResponse } from "node:http";
import type { Plugin } from "vite";

type Row = Record<string, unknown>;

const minute = 60_000;
const ago = (minutes: number) => new Date(Date.now() - minutes * minute).toISOString();
const backendStamp = (minutes: number) => ago(minutes).slice(0, 19).replace("T", " ");
const snapshot = () => new Date().toISOString();

const anchors = ["美国市场", "AI 会议助手", "财务设备接入"];

function task(id: number, title: string, extra: Partial<Row> = {}): Row {
  return {
    id: String(id), title, stage: "candidate", status: "open", commitment_status: "none", owner: "", deadline_at: "",
    business_relevance: "unknown", anchor_labels: [], updated_at: ago(id * 37), detail_url: `/tasks/item/${id}`, ...extra,
  };
}

const formal: Row[] = [
  task(1, "交付美国客户报价首版", { stage: "formal", commitment_status: "accepted", owner: "王明", deadline_at: "2026-09-28", business_relevance: "relevant", anchor_labels: ["美国市场"], updated_at: ago(3) }),
  task(2, "确认 Projects 模块上线时间与评审流程，并把结论同步给所有相关负责人和外部合作方，避免再次出现口径不一致的情况", { stage: "formal", status: "waiting", commitment_status: "accepted", owner: "陈思睿", deadline_at: "2026-10-08", business_relevance: "relevant", anchor_labels: ["AI 会议助手", "美国市场"], updated_at: ago(95) }),
  task(3, "财务设备接入需求评审", { stage: "formal", commitment_status: "assigned_unaccepted", owner: "静宇", business_relevance: "relevant", anchor_labels: ["财务设备接入"], updated_at: ago(60 * 26) }),
  task(4, "整理国庆前客户走访清单", { stage: "formal", status: "done", commitment_status: "completed", owner: "Avery", deadline_at: "2026-09-20", business_relevance: "relevant", updated_at: ago(60 * 24 * 4) }),
  task(5, "确认海外渠道合同条款", { stage: "formal", commitment_status: "disputed", owner: "Bartholomew Featherstonehaugh-Montgomery", business_relevance: "relevant", anchor_labels: ["美国市场"], updated_at: ago(60 * 24 * 9) }),
  task(6, "去年遗留的合规自查", { stage: "formal", status: "cancelled", commitment_status: "cancelled", owner: "王明", updated_at: new Date(Date.now() - 400 * 86_400_000).toISOString() }),
];

const candidateTitles = [
  "结构化撰写内容产出计划，明确分类、数量及分工", "整理访谈问题清单并发给磊哥确认", "使用真实 Case 测试事项抽取效果并提供 Evil Case 给工程师调优", "国庆前完成事项抽取 Prompt 初版编写并在商务电脑验证",
  "跟进 Recipe 生成不稳定的 Bug 修复进度", "定义 AI 会议辅助及多 Agent 功能的产品目标与客户痛点", "将财务设备接入需求转化为标准产品需求文档", "梳理需求清单，区分产品需求与算法需求",
  "绘制团队协作功能具体界面示例", "发送最新设计文档给磊哥审阅", "收集更多用户诉求并整理优先级表", "下次预约会议时改用其他会议工具",
];
const candidates: Row[] = Array.from({ length: 41 }, (_, index) => task(100 + index, `${candidateTitles[index % candidateTitles.length]}${index >= candidateTitles.length ? `（${Math.floor(index / candidateTitles.length) + 1}）` : ""}`, {
  owner: index % 5 === 2 ? "静宇" : index % 7 === 3 ? "发言人" : "",
  deadline_at: index % 9 === 4 ? "2026-10-03" : "",
  anchor_labels: index % 6 === 0 ? [anchors[index % 3]] : [],
  updated_at: ago(20 + index * 53),
}));
const allTasks = [...formal, ...candidates].sort((left, right) => String(right.updated_at).localeCompare(String(left.updated_at)));

const attention: Row[] = [
  { id: "1", category: "decision", business_area: "海外业务", title: "美国客户要求下周前给出正式报价", why_attention: "客户已两次催问，报价首版仍在制作，超过约定日期会影响后续合同", current_state: "王明已接单，首版预计周五完成", ceo_action: "决定是否接受客户提出的 15% 折扣区间", anchor_label: "美国市场", linked_task_count: 3, updated_at: ago(12), detail_url: "/tasks/attention/1" },
  { id: "2", category: "decision", business_area: "财务", title: "财务设备接入方案需要选型", why_attention: "两套方案成本相差一倍，团队意见不一致", current_state: "评审会已开，结论待定", ceo_action: "选定方案并明确预算上限", anchor_label: "财务设备接入", linked_task_count: 2, updated_at: ago(60 * 5), detail_url: "/tasks/attention/2" },
  { id: "3", category: "push", business_area: "产品", title: "Projects 模块上线时间未定", why_attention: "评审流程还没有走完，负责人之间没有对齐", current_state: "等待评审结论", ceo_action: "拉通会议，逼近一个明确的上线日期", anchor_label: "AI 会议助手", linked_task_count: 1, updated_at: ago(60 * 27), detail_url: "/tasks/attention/3" },
  { id: "4", category: "watch", business_area: "研发", title: "Recipe 生成不稳定", why_attention: "偶发失败，已影响两个演示", current_state: "工程师在复现", ceo_action: "当前无需处理", anchor_label: "", linked_task_count: 1, updated_at: ago(60 * 30), detail_url: "/tasks/attention/4" },
  { id: "5", category: "watch", business_area: "市场", title: "国庆前客户走访进度", why_attention: "走访清单已完成，实际预约较少", current_state: "已联系 4 家，2 家确认", ceo_action: "当前无需处理", anchor_label: "美国市场", linked_task_count: 4, updated_at: ago(60 * 24 * 3), detail_url: "/tasks/attention/5" },
  { id: "6", category: "fyi", business_area: "人力", title: "两名新同事下周入职", why_attention: "入职材料齐备", current_state: "工位与账号已准备", ceo_action: "当前无需处理", anchor_label: "", linked_task_count: 0, updated_at: ago(60 * 24 * 5), detail_url: "/tasks/attention/6" },
  { id: "7", category: "fyi", business_area: "法务", title: "海外渠道合同已通过法审并且法务给出了一长串需要留意的条款，其中涉及数据出境、违约金上限、争议解决地和知识产权归属", why_attention: "合同已法审", current_state: "等待对方签署", ceo_action: "当前无需处理", anchor_label: "美国市场", linked_task_count: 1, updated_at: ago(60 * 24 * 8), detail_url: "/tasks/attention/7" },
];

const projects: Row[] = [
  { id: "1", title: "美国市场拓展", registry_source: "经营会确认美国市场拓展为正式项目", canonical_anchor_id: 1, confirmed_task_count: 4, detail_url: "/tasks/project/1" },
  { id: "2", title: "AI 会议助手", registry_source: "产品周会决议", canonical_anchor_id: 2, confirmed_task_count: 1, detail_url: "/tasks/project/2" },
  { id: "3", title: "财务设备接入", registry_source: "财务负责人提出并经确认", canonical_anchor_id: 3, confirmed_task_count: 0, detail_url: "/tasks/project/3" },
];
const projectCandidates: Row[] = Array.from({ length: 25 }, (_, index) => ({
  id: String(200 + index), title: `${["海外渠道拓展", "客户成功体系", "数据出境合规", "招聘体系升级", "内部工具整合"][index % 5]}${index >= 5 ? `（线索 ${index + 1}）` : ""}`,
  reason: `${2 + (index % 4)} 项关联任务指向同一件事，尚未有人明确确认`, status: "proposed", cluster_id: index + 1, provisional: true, confirmed_project_id: null,
}));

const signal = (id: number, sourceType: string, evidence: string, context: Row = {}) => ({
  id, source_type: sourceType, source_ref: `ref-${id}`, source_time: "2026-09-24T11:29:21+08:00", conversation_id: "", conversation_title: "", author_user_id: "", author_name: "", author_kind: "unknown",
  evidence_text: evidence, context_json: JSON.stringify(context), dedupe_key: `k-${id}`, created_at: "2026-09-24T23:48:47+00:00",
});

const followUps: Row[] = [
  { id: 7, revision: 3, status: "draft", question_text: "报价首版目前进展到哪一步？周五能否交付？", target_kind: "group", owner_name: "王明", scheduled_at: "2026-09-26 09:00:00" },
  { id: 6, revision: 2, status: "sent", question_text: "请确认客户要求的折扣区间是否已经沟通", target_kind: "direct", owner_name: "王明", scheduled_at: "2026-09-22 09:00:00", sent_at: "2026-09-22 09:02:11" },
  { id: 5, revision: 2, status: "failed", question_text: "上周约定的报价模板是否已经更新？", target_kind: "direct", owner_name: "王明", scheduled_at: "2026-09-20 09:00:00", send_result_json: JSON.stringify({ error: "timeout" }) },
  { id: 4, revision: 2, status: "cancelled", question_text: "旧的催办", target_kind: "direct", owner_name: "王明", scheduled_at: "2026-09-18 09:00:00", suppressed_reason: "Task 已被新信息更新" },
];

function taskDetail(id: number): Row | null {
  const summary = allTasks.find((row) => row.id === String(id));
  if (!summary) return null;
  const base = { summary, description: summary.stage === "formal" ? "为美国客户准备正式报价，包含产品清单、交付周期和折扣条款。" : "来源为 AI 听记中的钉钉待办行动项。任务负责人、分派授权和负责人接受或承诺证据均未提供，保留为候选任务。", formal_basis: "", owner_user_id: "", missing_evidence: [] as string[], date_evidence: [] as Row[], relations: [] as Row[], clusters: [] as Row[], anchors: [] as Row[], official_projects: [] as Row[], follow_ups: [] as Row[], dingtalk_todos: [] as Row[] };
  const meeting = { meeting: { title: "每周产品进展同步", durationMicros: 1972792000, startTimeISO: "2026-09-24T11:29:21+08:00", todos: { result: { actions: ["整理访谈问题清单"] } } }, uuid: "0d3f-mock" };
  const created = { id: 1, task_id: String(id), event_type: "created", reason: "Candidate task recorded from source evidence.", created_at: backendStamp(60 * 30) };
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
  return { ...base, evidence: [{ role: "discovery", signal: signal(id, "ai_minutes", JSON.stringify(meeting), { work_item_title: `${summary.title}行动项` }) }], events: [created] };
}

const attentionDetail = (id: string): Row | null => {
  const summary = attention.find((row) => row.id === id);
  if (!summary) return null;
  return {
    summary, anchor: { id: 1, title: summary.anchor_label },
    linked_tasks: allTasks.slice(0, Number(summary.linked_task_count)),
    evidence_signals: [signal(31, "meeting", "客户希望下周前看到正式报价，并提出折扣区间。"), signal(32, "ai_minutes", JSON.stringify({ meeting: { title: "客户同步会" } }), { work_item_title: "客户同步会纪要" })].map((value) => ({ signal: value })),
    events: [{ id: 1, event_type: "opened", created_at: backendStamp(60 * 30) }, { id: 2, event_type: "category_changed", reason: "客户再次催问", created_at: backendStamp(60 * 3) }],
  };
};

const projectDetail = (id: string): Row | null => {
  const summary = projects.find((row) => row.id === id);
  if (!summary) return null;
  return { summary, anchor: { id: 1, title: anchors[Number(id) - 1] }, confirmed_tasks: allTasks.slice(0, Number(summary.confirmed_task_count)) };
};

function page(rows: Row[], url: URL, extra: Row = {}) {
  const size = Number(url.searchParams.get("page_size") || 20);
  const current = Math.max(1, Number(url.searchParams.get("page") || 1));
  const start = (current - 1) * size;
  return { items: rows.slice(start, start + size), meta: { snapshot_at: snapshot(), page: current, page_size: size, total: rows.length, next_cursor: start + size < rows.length ? String(current + 1) : "", has_more: start + size < rows.length }, ...extra };
}

function route(pathname: string, url: URL, scenario: string): { status: number; body: unknown } | null {
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
    const rows = scenario === "empty" ? [] : allTasks.filter((row) => (!stage || row.stage === stage) && (!status || row.status === status) && (!q || String(row.title).toLowerCase().includes(q)));
    return send(page(rows, url));
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
        const respond = () => {
          const result = scenario === "error" ? { status: 500, body: { detail: "演示：服务暂时不可用" } } : route(url.pathname, url, scenario);
          if (!result) return next();
          response.statusCode = result.status;
          response.setHeader("content-type", "application/json");
          response.end(JSON.stringify(result.body));
        };
        if (scenario === "slow") setTimeout(respond, 4000); else respond();
      });
    },
  };
}
