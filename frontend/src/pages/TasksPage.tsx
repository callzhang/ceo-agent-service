import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { listSentTodos, listTasks, type SentTodoItem, type TaskSummary } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { StatusBadge } from "../components/status/StatusBadge";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

const pageSizes = [20, 50, 100];
const sortOptions = [["", "默认顺序"], ["project_desc", "Project Z-A"], ["project_asc", "Project A-Z"], ["priority_desc", "Priority 高-低"], ["progress_desc", "Progress 高-低"], ["todos_desc", "ToDos 多-少"]];

function localTime(value: string) {
  if (!value) return "未提供";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function Pagination({ page, pageSize, total, onPageChange }: { page: number; pageSize: number; total: number; onPageChange: (page: number) => void }) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  if (pageCount <= 1) return null;
  return <div className="pagination"><div className="pagination-status"><span>{(page - 1) * pageSize + 1}-{Math.min(page * pageSize, total)}</span><span>{page} / {pageCount}</span><span>共 {total} 条</span></div><nav className="pagination-actions" aria-label="Task pages"><button type="button" className="pagination-button" disabled={page === 1} onClick={() => onPageChange(1)}>首页</button><button type="button" className="pagination-button pagination-arrow" disabled={page === 1} aria-label="上一页" onClick={() => onPageChange(page - 1)}>‹</button><button type="button" className="pagination-button pagination-arrow" disabled={page === pageCount} aria-label="下一页" onClick={() => onPageChange(page + 1)}>›</button><button type="button" className="pagination-button" disabled={page === pageCount} onClick={() => onPageChange(pageCount)}>末页</button></nav></div>;
}

export function TasksPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [rows, setRows] = useState<TaskSummary[]>([]);
  const [sentRows, setSentRows] = useState<SentTodoItem[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [sentState, setSentState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const query = searchParams.get("q") || "";
  const category = searchParams.get("category") || "";
  const taskState = searchParams.get("task_state") || "";
  const sort = searchParams.get("sort") || "";
  const page = Math.max(1, Number(searchParams.get("page") || 1));
  const pageSizeValue = Number(searchParams.get("page_size") || 20);
  const pageSize = pageSizes.includes(pageSizeValue) ? pageSizeValue : 20;

  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    setError("");
    listTasks({ q: query, category, task_state: taskState, page: 1, page_size: 100 }, controller.signal).then((result) => { setRows(result.items); setTotalCount(result.meta.total || result.items.length); setSnapshot(result.meta.snapshot_at); setState("ready"); }).catch((reason: unknown) => { if (controller.signal.aborted) return; setError(reason instanceof Error ? reason.message : "加载失败"); setState("error"); });
    return () => controller.abort();
  }, [query, category, taskState]);

  useEffect(() => {
    const controller = new AbortController();
    setSentState("loading");
    listSentTodos({}, controller.signal).then((result) => setSentRows(result.items)).then(() => setSentState("ready")).catch((reason: unknown) => { if (!controller.signal.aborted) setSentState("error"); });
    return () => controller.abort();
  }, []);

  const categories = useMemo(() => [...new Set(rows.map((row) => row.category).filter(Boolean))].sort(), [rows]);
  const taskStates = useMemo(() => [...new Set(rows.map((row) => row.status).filter(Boolean))].sort(), [rows]);
  const sortedRows = useMemo(() => [...rows].sort((left, right) => { if (sort === "project_asc") return left.title.localeCompare(right.title); if (sort === "project_desc") return right.title.localeCompare(left.title); if (sort === "priority_desc") return left.priority.localeCompare(right.priority); if (sort === "progress_desc") return right.progress.localeCompare(left.progress); if (sort === "todos_desc") return right.todo_count - left.todo_count; return 0; }), [rows, sort]);
  const visibleRows = sortedRows.slice((page - 1) * pageSize, page * pageSize);
  const update = (key: string, value: string | number) => { const next = new URLSearchParams(searchParams); if (String(value)) next.set(key, String(value)); else next.delete(key); if (key !== "page") next.delete("page"); setSearchParams(next); };

  return <ConsolePageLayout title="Tasks" actions={<SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} />}><div className="tasks-page" role="region" aria-label="Tasks workspace"><section className="card tasks-workspace-card"><div className="table-toolbar tasks-toolbar"><div className="table-toolbar-left"><label className="table-toolbar-search"><span className="sr-only">Search tasks</span><input id="task-search-input" type="text" value={query} placeholder="搜索" onChange={(event) => update("q", event.target.value)} />{query && <button id="task-search-clear" type="button" className="table-search-clear" aria-label="清除搜索" onClick={() => update("q", "")}>×</button>}</label><select id="task-type-filter" className="table-type-select" aria-label="Task type filter" value={category} onChange={(event) => update("category", event.target.value)}><option value="">type: all</option>{categories.map((value) => <option value={value} key={value}>{value}</option>)}</select><select id="task-state-filter" className="table-type-select" aria-label="Task state filter" value={taskState} onChange={(event) => update("task_state", event.target.value)}><option value="">state: all</option>{taskStates.map((value) => <option value={value} key={value}>{value}</option>)}</select><select className="table-type-select task-sort-select" aria-label="Task sort" value={sort} onChange={(event) => update("sort", event.target.value)}>{sortOptions.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></div><div className="table-toolbar-right"><select className="table-page-size" aria-label="Tasks per page" value={pageSize} onChange={(event) => update("page_size", event.target.value)}>{pageSizes.map((size) => <option key={size} value={size}>{size}/页</option>)}</select><span className="table-toolbar-total">共 {totalCount} 条</span></div></div>{state === "error" ? <div className="page-state page-state-error" role="alert">{error}</div> : state === "loading" && !rows.length ? <div className="page-state" role="status">正在加载…</div> : !visibleRows.length ? <div className="page-state">暂无任务。</div> : <><div className="responsive-table-wrap tasks-table-wrap"><table className="tasks-table" aria-label="Tasks"><thead><tr><th>Project</th><th>Status</th><th>Priority</th><th>Owner</th><th>Progress</th><th>ToDos</th></tr></thead><tbody>{visibleRows.map((row) => <tr key={row.id}><td><Link to={`/tasks/${row.id}`} aria-label={`查看详情 ${row.title}`}>{row.title}</Link></td><td><StatusBadge value={row.status} /></td><td><span className="pill">{row.priority || "未提供"}</span></td><td>{row.owner || "未提供"}</td><td>{row.progress || "未提供"}</td><td>{row.todo_count} 个 TODO</td></tr>)}</tbody></table></div><Pagination page={page} pageSize={pageSize} total={totalCount} onPageChange={(nextPage) => update("page", nextPage)} /></>}</section><section className="card sent-todos-section"><div className="section-head"><h2>Sent TODOs</h2><p className="muted">DingTalk Todo and follow-up messages sent by task maintenance.</p></div>{sentState === "loading" ? <div className="page-state" role="status">正在加载…</div> : sentState === "error" ? <div className="page-state page-state-error" role="alert">Sent TODOs 加载失败</div> : !sentRows.length ? <div className="page-state">No sent TODOs.</div> : <div className="responsive-table-wrap sent-todos-table-wrap"><table className="sent-todos-table" aria-label="Sent TODOs"><thead><tr><th>Sent</th><th>Type</th><th>Owner</th><th>Project</th><th>TODO</th><th>DDL</th><th>Status</th><th>Original Text</th><th>Target</th></tr></thead><tbody>{sentRows.map((row) => <tr key={row.id}><td>{localTime(row.sent_at)}</td><td><span className="pill">{row.kind_label}</span></td><td>{row.owner || "未提供"}</td><td>{row.detail_url ? <Link to={row.detail_url}>{row.project_title || "未提供"}</Link> : row.project_title || "未提供"}</td><td>{row.detail_url ? <Link to={row.detail_url}>{row.todo_title || "未提供"}</Link> : row.todo_title || "未提供"}</td><td>{localTime(row.deadline)}</td><td><StatusBadge value={row.status} /></td><td>{row.original_text || row.description || "未提供"}</td><td>{row.target || "未提供"}</td></tr>)}</tbody></table></div>}</section></div></ConsolePageLayout>;
}
