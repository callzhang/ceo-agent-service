import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { listBusinessAttention, listBusinessProjects, listBusinessTasks, type AttentionCategory, type BusinessAttentionSummary, type BusinessProjectCandidateSummary, type BusinessProjectSummary, type BusinessTaskSummary, type TaskView } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";
import { TaskSkeleton } from "./TaskParts";
import { TaskTime } from "./TaskTime";
import { categoryLabels, categoryOrder, commitmentText, labelOf, taskStatusLabels } from "./taskLabels";

const stageOptions = [{ value: "candidate", label: "候选任务" }, { value: "formal", label: "正式任务" }];
const statusOptions = ["open", "waiting", "done", "cancelled"];

function AttentionCard({ item }: { item: BusinessAttentionSummary }) {
  return <article className={`business-attention-card category-${item.category}`} aria-label={item.title}>
    <header><span className="business-attention-category">{categoryLabels[item.category]}</span><span>{item.business_area}</span></header>
    <h2><Link to={item.detail_url}>{item.title}</Link></h2>
    <dl>
      <div><dt>为什么关注</dt><dd>{item.why_attention}</dd></div>
      <div><dt>当前状态</dt><dd>{item.current_state}</dd></div>
      <div className="business-attention-action"><dt>你的动作</dt><dd>{item.ceo_action}</dd></div>
    </dl>
    <footer><span>{item.anchor_label || "暂未关联业务主线"}</span><span>{item.linked_task_count} 个关联任务</span><TaskTime value={item.updated_at} /></footer>
  </article>;
}

/** Only what is worth reading: a candidate with no owner, the default status and no anchor shows nothing here. */
function taskFacts(item: BusinessTaskSummary) {
  return [
    item.owner ? `负责人 ${item.owner}` : "",
    item.status !== "open" ? labelOf(taskStatusLabels, item.status) : "",
    commitmentText(item.status, item.commitment_status),
    item.deadline_at ? `截止 ${item.deadline_at}` : "",
    ...item.anchor_labels,
  ].filter(Boolean);
}

function TaskRow({ item }: { item: BusinessTaskSummary }) {
  const facts = taskFacts(item);
  return <li className="business-task-row">
    <div className="business-task-row-main"><Link to={item.detail_url}>{item.title}</Link><span className={`business-stage ${item.stage}`}>{item.stage === "candidate" ? "候选任务" : "正式任务"}</span></div>
    <span className="business-task-time"><TaskTime value={item.updated_at} /></span>
    {facts.length > 0 && <p className="business-task-meta">{facts.map((fact) => <span key={fact}>{fact}</span>)}</p>}
  </li>;
}

function ProjectRow({ item }: { item: BusinessProjectSummary }) {
  return <li className="business-project-row"><div className="business-task-row-main"><Link to={item.detail_url}>{item.title}</Link><span className="business-stage formal">正式项目</span></div><span className="business-task-time">{item.confirmed_task_count} 个确认关联任务</span><p className="business-task-meta"><span>登记来源：{item.registry_source}</span></p></li>;
}

function ProjectCandidateRow({ item }: { item: BusinessProjectCandidateSummary }) {
  return <li className="business-project-row is-provisional"><div className="business-task-row-main"><strong>{item.title}</strong><span className="business-stage">候选项目 · 尚未确认</span></div><p className="business-task-meta"><span>{item.reason}</span></p></li>;
}

function Pagination({ label, page, pages, previous, next, previousLabel = "上一页", nextLabel = "下一页" }: { label: string; page: number; pages: number; previous: () => void; next: () => void; previousLabel?: string; nextLabel?: string }) {
  if (pages <= 1) return null;
  return <nav className="business-task-pagination" aria-label={label}><button type="button" disabled={page <= 1} onClick={previous}>{previousLabel}</button><span>{page} / {pages}</span><button type="button" disabled={page >= pages} onClick={next}>{nextLabel}</button></nav>;
}

interface Loaded {
  view: TaskView;
  items: BusinessAttentionSummary[] | BusinessTaskSummary[] | BusinessProjectSummary[];
  candidates: BusinessProjectCandidateSummary[];
  candidateMeta: { page: number; page_size: number; total: number };
}

export function TasksPage() {
  const [params, setParams] = useSearchParams();
  const requestedView = params.get("view");
  const view: TaskView = requestedView === "all" || requestedView === "projects" ? requestedView : "attention";
  const category = params.get("category") || "";
  const q = params.get("q") || "";
  const stage = params.get("stage") || "";
  const status = params.get("status") || "";
  const page = Math.max(1, Number(params.get("page") || 1));
  const candidatePage = Math.max(1, Number(params.get("candidate_page") || 1));
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [total, setTotal] = useState(0);
  const [snapshot, setSnapshot] = useState("");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const workspace = useRef<HTMLDivElement>(null);
  const firstRender = useRef(true);

  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    setError("");
    const request = view === "attention"
      ? listBusinessAttention({ category, page, page_size: 20 }, controller.signal)
      : view === "all"
        ? listBusinessTasks({ q, stage, status, page, page_size: 20 }, controller.signal)
        : listBusinessProjects({ q, page, page_size: 20, candidate_page: candidatePage, candidate_page_size: 20 }, controller.signal);
    request.then((result) => {
      if (controller.signal.aborted) return;
      setLoaded({
        view,
        items: result.items,
        candidates: "candidates" in result ? result.candidates.filter((candidate) => candidate.provisional) : [],
        candidateMeta: "candidate_meta" in result ? result.candidate_meta : { page: 1, page_size: 20, total: 0 },
      });
      setTotal(result.meta.total);
      setSnapshot(result.meta.snapshot_at);
      setState("ready");
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : "加载失败");
      setState("error");
    });
    return () => controller.abort();
  }, [view, category, q, stage, status, page, candidatePage]);

  // Turning a page should land on the top of the list, not stay at the bottom where the button was.
  useEffect(() => {
    if (firstRender.current) { firstRender.current = false; return; }
    workspace.current?.scrollIntoView({ block: "start" });
  }, [page, candidatePage]);

  const update = (key: string, value: string | number) => {
    const next = new URLSearchParams(params);
    if (String(value)) next.set(key, String(value)); else next.delete(key);
    if (key !== "page" && key !== "candidate_page") next.delete("page");
    if (key !== "candidate_page") next.delete("candidate_page");
    setParams(next);
  };
  const pages = Math.max(1, Math.ceil(total / 20));
  const candidateMeta = loaded?.candidateMeta ?? { page: 1, page_size: 20, total: 0 };
  const candidatePages = Math.max(1, Math.ceil(candidateMeta.total / candidateMeta.page_size));
  // Rows of another tab must never be drawn by this tab's renderer; within one tab, the previous page stays (dimmed) while the next loads.
  const data = loaded?.view === view ? loaded : null;
  const filtered = view === "all" ? Boolean(q || stage || status) : view === "projects" ? Boolean(q) : Boolean(category);
  const clearFilters = () => setParams(new URLSearchParams(view === "attention" ? { } : { view }));

  const empty = (message: string) => <div className="business-empty"><p>{message}</p>{filtered ? <button type="button" className="secondary-button" onClick={clearFilters}>清除筛选</button> : view === "attention" ? <Link className="secondary-button" to="/tasks?view=all">查看全部任务</Link> : null}</div>;

  let body;
  if (state === "error") body = <div className="page-state page-state-error" role="alert">{error}</div>;
  else if (!data) body = <TaskSkeleton />;
  else if (view === "attention") body = data.items.length ? <div className="business-attention-list">{(data.items as BusinessAttentionSummary[]).map((item) => <AttentionCard key={item.id} item={item} />)}</div> : empty(filtered ? "该类别下没有事项。" : "当前没有需要关注的事项。");
  else if (view === "all") body = data.items.length ? <ul className="business-task-list">{(data.items as BusinessTaskSummary[]).map((item) => <TaskRow key={item.id} item={item} />)}</ul> : empty(filtered ? "没有符合条件的任务。" : "暂无任务。");
  else body = <>
    {data.items.length ? <ul className="business-task-list">{(data.items as BusinessProjectSummary[]).map((item) => <ProjectRow key={item.id} item={item} />)}</ul> : empty(filtered ? "没有符合条件的项目。" : "暂无正式项目。")}
    {candidateMeta.total > 0 && <details className="business-project-candidates" open={data.items.length === 0}>
      <summary><h2>待确认的项目线索 <span>{candidateMeta.total}</span></h2></summary>
      <ul className="business-task-list">{data.candidates.map((item) => <ProjectCandidateRow key={item.id} item={item} />)}</ul>
      <Pagination label="项目线索分页" page={candidatePage} pages={candidatePages} previous={() => update("candidate_page", candidatePage - 1)} next={() => update("candidate_page", candidatePage + 1)} previousLabel="项目线索上一页" nextLabel="项目线索下一页" />
    </details>}
  </>;

  return <ConsolePageLayout title="Tasks" description="从真实上下文发现任务，聚焦需要你关注的业务进展。" actions={<SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} />}>
    <div className="tasks-page task-domain-page" role="region" aria-label="Tasks workspace" ref={workspace}>
      <nav className="business-task-tabs" aria-label="Tasks 视图">
        <Link to="/tasks" aria-current={view === "attention" ? "page" : undefined}>需关注</Link>
        <Link to="/tasks?view=all" aria-current={view === "all" ? "page" : undefined}>全部任务</Link>
        <Link to="/tasks?view=projects" aria-current={view === "projects" ? "page" : undefined}>正式项目</Link>
      </nav>
      {view === "attention" ? <div className="business-task-toolbar" role="group" aria-label="关注类别">
        <button type="button" aria-pressed={!category} onClick={() => update("category", "")}>全部关注</button>
        {categoryOrder.map((value: AttentionCategory) => <button key={value} type="button" aria-pressed={category === value} onClick={() => update("category", value)}>{categoryLabels[value]}</button>)}
      </div> : <div className="business-task-filters">
        <label className="business-task-search"><span className="sr-only">搜索{view === "all" ? "任务" : "项目"}</span><input type="search" aria-label={`搜索${view === "all" ? "任务" : "项目"}`} value={q} onChange={(event) => update("q", event.target.value)} placeholder={`按名称搜索${view === "all" ? "任务" : "项目"}`} /></label>
        {view === "all" && <>
          <select aria-label="任务阶段" value={stage} onChange={(event) => update("stage", event.target.value)}><option value="">全部阶段</option>{stageOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
          <select aria-label="任务状态" value={status} onChange={(event) => update("status", event.target.value)}><option value="">全部状态</option>{statusOptions.map((value) => <option key={value} value={value}>{taskStatusLabels[value]}</option>)}</select>
        </>}
        {state !== "error" && data && <span className="business-task-count">{view === "all" ? "共" : "正式项目"} {total} {view === "all" ? "个任务" : "个"}</span>}
      </div>}
      <div className="business-list-body" aria-busy={state === "loading"} data-refreshing={state === "loading" && data ? "true" : undefined}>{body}</div>
      {state !== "error" && data && <Pagination label="Tasks 分页" page={page} pages={pages} previous={() => update("page", page - 1)} next={() => update("page", page + 1)} />}
    </div>
  </ConsolePageLayout>;
}
