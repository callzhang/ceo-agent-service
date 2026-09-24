import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { listBusinessAttention, listBusinessProjects, listBusinessTasks, type AttentionCategory, type BusinessAttentionSummary, type BusinessProjectCandidateSummary, type BusinessProjectSummary, type BusinessTaskSummary, type TaskView } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

const categoryLabels: Record<AttentionCategory, string> = { fyi: "仅需知晓", watch: "持续观察", decision: "需要决策", push: "需要推动" };
const categoryOrder: AttentionCategory[] = ["fyi", "watch", "decision", "push"];

function localTime(value: string) {
  if (!value) return "未提供";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function AttentionCard({ item }: { item: BusinessAttentionSummary }) {
  return <article className={`business-attention-card category-${item.category}`} aria-label={item.title}>
    <header><span className="business-attention-category">{categoryLabels[item.category]}</span><span>{item.business_area}</span></header>
    <h2><Link to={item.detail_url}>{item.title}</Link></h2>
    <dl>
      <div><dt>为什么关注</dt><dd>{item.why_attention}</dd></div>
      <div><dt>当前状态</dt><dd>{item.current_state}</dd></div>
      <div className="business-attention-action"><dt>你的动作</dt><dd>{item.ceo_action}</dd></div>
    </dl>
    <footer><span>{item.anchor_label || "暂未关联业务主线"}</span><span>{item.linked_task_count} 个关联任务</span><time dateTime={item.updated_at}>{localTime(item.updated_at)}</time></footer>
  </article>;
}

function TaskRow({ item }: { item: BusinessTaskSummary }) {
  return <li className="business-task-row">
    <div className="business-task-row-main"><Link to={item.detail_url}>{item.title}</Link><span className={`business-stage ${item.stage}`}>{item.stage === "candidate" ? "候选任务" : "正式任务"}</span></div>
    <p>{item.owner || "负责人未明确"} · {item.status} · {item.commitment_status}</p>
    <small>{item.anchor_labels.join(" · ") || "暂无业务主线关联"} · 更新于 {localTime(item.updated_at)}</small>
  </li>;
}

function ProjectRow({ item }: { item: BusinessProjectSummary }) {
  return <li className="business-project-row"><div className="business-task-row-main"><Link to={item.detail_url}>{item.title}</Link><span className="business-stage">正式项目</span></div><p>{item.confirmed_task_count} 个确认关联任务</p><small>登记来源：{item.registry_source}</small></li>;
}

function ProjectCandidateRow({ item }: { item: BusinessProjectCandidateSummary }) {
  return <li className="business-project-row is-provisional"><div className="business-task-row-main"><strong>{item.title}</strong><span className="business-stage">候选项目 · 尚未确认</span></div><p>{item.reason}</p></li>;
}

export function TasksPage() {
  const [params, setParams] = useSearchParams();
  const requestedView = params.get("view");
  const view: TaskView = requestedView === "all" || requestedView === "projects" ? requestedView : "attention";
  const category = params.get("category") || "";
  const q = params.get("q") || "";
  const page = Math.max(1, Number(params.get("page") || 1));
  const candidatePage = Math.max(1, Number(params.get("candidate_page") || 1));
  const [items, setItems] = useState<BusinessAttentionSummary[] | BusinessTaskSummary[] | BusinessProjectSummary[]>([]);
  const [candidates, setCandidates] = useState<BusinessProjectCandidateSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [candidateMeta, setCandidateMeta] = useState({ page: 1, page_size: 20, total: 0, next_cursor: "", has_more: false, snapshot_at: "" });
  const [snapshot, setSnapshot] = useState("");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    setError("");
    const request = view === "attention"
      ? listBusinessAttention({ category, page, page_size: 20 }, controller.signal)
      : view === "all"
        ? listBusinessTasks({ q, page, page_size: 20 }, controller.signal)
        : listBusinessProjects({ q, page, page_size: 20, candidate_page: candidatePage, candidate_page_size: 20 }, controller.signal);
    request.then((result) => {
      if (controller.signal.aborted) return;
      setItems(result.items);
      setCandidates("candidates" in result ? result.candidates.filter((candidate) => candidate.provisional) : []);
      if ("candidate_meta" in result) setCandidateMeta(result.candidate_meta);
      setTotal(result.meta.total);
      setSnapshot(result.meta.snapshot_at);
      setState("ready");
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : "加载失败");
      setState("error");
    });
    return () => controller.abort();
  }, [view, category, q, page, candidatePage]);

  const update = (key: string, value: string | number) => {
    const next = new URLSearchParams(params);
    if (String(value)) next.set(key, String(value)); else next.delete(key);
    if (key !== "page" && key !== "candidate_page") next.delete("page");
    if (key !== "candidate_page") next.delete("candidate_page");
    setParams(next);
  };
  const pages = Math.max(1, Math.ceil(total / 20));
  const candidatePages = Math.max(1, Math.ceil(candidateMeta.total / candidateMeta.page_size));

  return <ConsolePageLayout title="Tasks" description="从真实上下文发现任务，聚焦需要你关注的业务进展。" actions={<SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} />}>
    <div className="tasks-page task-domain-page" role="region" aria-label="Tasks workspace">
      <nav className="business-task-tabs" aria-label="Tasks 视图">
        <Link to="/tasks" aria-current={view === "attention" ? "page" : undefined}>需关注</Link>
        <Link to="/tasks?view=all" aria-current={view === "all" ? "page" : undefined}>全部任务</Link>
        <Link to="/tasks?view=projects" aria-current={view === "projects" ? "page" : undefined}>正式项目</Link>
      </nav>
      {view === "attention" ? <div className="business-task-toolbar" role="group" aria-label="关注类别">
        <button type="button" aria-pressed={!category} onClick={() => update("category", "")}>全部关注</button>
        {categoryOrder.map((value) => <button key={value} type="button" aria-pressed={category === value} onClick={() => update("category", value)}>{categoryLabels[value]}</button>)}
      </div> : <label className="business-task-search">搜索{view === "all" ? "任务" : "项目"}<input value={q} onChange={(event) => update("q", event.target.value)} placeholder="按名称搜索" /></label>}
      {state === "error" ? <div className="page-state page-state-error" role="alert">{error}</div> : state === "loading" ? <div className="page-state" role="status">正在加载…</div> : view === "attention" ? (
        items.length ? <div className="business-attention-list">{(items as BusinessAttentionSummary[]).map((item) => <AttentionCard key={item.id} item={item} />)}</div> : <p className="page-state">当前没有需要关注的事项。</p>
      ) : view === "all" ? (
        items.length ? <ul className="business-task-list">{(items as BusinessTaskSummary[]).map((item) => <TaskRow key={item.id} item={item} />)}</ul> : <p className="page-state">暂无任务。</p>
      ) : <><p className="business-project-count">正式项目 {total} 个</p>{items.length ? <ul className="business-task-list">{(items as BusinessProjectSummary[]).map((item) => <ProjectRow key={item.id} item={item} />)}</ul> : <p className="page-state">暂无正式项目。</p>}{candidateMeta.total > 0 && <section className="business-project-candidates"><h2>待确认的项目线索</h2><ul className="business-task-list">{candidates.map((item) => <ProjectCandidateRow key={item.id} item={item} />)}</ul>{candidatePages > 1 && <nav className="business-task-pagination" aria-label="项目线索分页"><button type="button" disabled={candidatePage <= 1} onClick={() => update("candidate_page", candidatePage - 1)}>项目线索上一页</button><span>{candidateMeta.page} / {candidatePages}</span><button type="button" disabled={candidatePage >= candidatePages} onClick={() => update("candidate_page", candidatePage + 1)}>项目线索下一页</button></nav>}</section>}</>}
      {state === "ready" && pages > 1 && <nav className="business-task-pagination" aria-label="Tasks 分页"><button type="button" disabled={page <= 1} onClick={() => update("page", page - 1)}>上一页</button><span>{page} / {pages}</span><button type="button" disabled={page >= pages} onClick={() => update("page", page + 1)}>下一页</button></nav>}
    </div>
  </ConsolePageLayout>;
}
