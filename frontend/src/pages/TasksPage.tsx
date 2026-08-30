import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { listSentTodos, listTasks, type SentTodoItem, type TaskSummary } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { FilterBar } from "../components/filters/FilterBar";
import { SearchField } from "../components/filters/SearchField";
import { SelectField } from "../components/filters/SelectField";
import { StatusBadge } from "../components/status/StatusBadge";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

const pageSizes = [20, 50, 100];
const sortOptions = [["", "默认顺序"], ["project_desc", "Project Z-A"], ["project_asc", "Project A-Z"], ["priority_desc", "Priority 高-低"], ["progress_desc", "Progress 高-低"], ["todos_desc", "ToDos 多-少"]];

function localTime(value: string) {
  if (!value) return "未提供";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function Pagination({ ariaLabel, page, pageSize, total, onPageChange }: { ariaLabel: string; page: number; pageSize: number; total: number; onPageChange: (page: number) => void }) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  if (pageCount <= 1) return null;
  return <div className="pagination"><div className="pagination-status"><span>{(page - 1) * pageSize + 1}-{Math.min(page * pageSize, total)}</span><span>{page} / {pageCount}</span><span>共 {total} 条</span></div><nav className="pagination-actions" aria-label={ariaLabel}><button type="button" className="pagination-button" disabled={page === 1} onClick={() => onPageChange(1)}>首页</button><button type="button" className="pagination-button pagination-arrow" disabled={page === 1} aria-label="上一页" onClick={() => onPageChange(page - 1)}>‹</button><button type="button" className="pagination-button pagination-arrow" disabled={page === pageCount} aria-label="下一页" onClick={() => onPageChange(page + 1)}>›</button><button type="button" className="pagination-button" disabled={page === pageCount} onClick={() => onPageChange(pageCount)}>末页</button></nav></div>;
}

export function TasksPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [rows, setRows] = useState<TaskSummary[]>([]);
  const [sentRows, setSentRows] = useState<SentTodoItem[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [sentTotalCount, setSentTotalCount] = useState(0);
  const [availableCategories, setAvailableCategories] = useState<string[]>([]);
  const [availableTaskStates, setAvailableTaskStates] = useState<string[]>([]);
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
  const sentPage = Math.max(1, Number(searchParams.get("sent_page") || 1));
  const sentPageSizeValue = Number(searchParams.get("sent_page_size") || 20);
  const sentPageSize = pageSizes.includes(sentPageSizeValue) ? sentPageSizeValue : 20;

  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    setError("");
    listTasks({ q: query, category, task_state: taskState, sort, page, page_size: pageSize }, controller.signal).then((result) => { setRows(result.items); setTotalCount(result.meta.total || result.items.length); setAvailableCategories(result.filters?.categories || []); setAvailableTaskStates(result.filters?.task_states || []); setSnapshot(result.meta.snapshot_at); setState("ready"); }).catch((reason: unknown) => { if (controller.signal.aborted) return; setError(reason instanceof Error ? reason.message : "加载失败"); setState("error"); });
    return () => controller.abort();
  }, [query, category, taskState, sort, page, pageSize]);

  useEffect(() => {
    const controller = new AbortController();
    setSentState("loading");
    listSentTodos({ page: sentPage, page_size: sentPageSize }, controller.signal).then((result) => { setSentRows(result.items); setSentTotalCount(result.meta.total || result.items.length); setSentState("ready"); }).catch((reason: unknown) => { if (!controller.signal.aborted) setSentState("error"); });
    return () => controller.abort();
  }, [sentPage, sentPageSize]);

  const categories = useMemo(() => [...new Set([...availableCategories, category, ...rows.map((row) => row.category)].filter(Boolean))].sort(), [availableCategories, category, rows]);
  const taskStates = useMemo(() => [...new Set([...availableTaskStates, taskState, ...rows.map((row) => row.status)].filter(Boolean))].sort(), [availableTaskStates, taskState, rows]);
  const visibleRows = rows;
  const update = (key: string, value: string | number) => { const next = new URLSearchParams(searchParams); if (String(value)) next.set(key, String(value)); else next.delete(key); if (key !== "page") next.delete("page"); setSearchParams(next); };
  const updateSent = (key: string, value: string | number) => { const next = new URLSearchParams(searchParams); if (String(value)) next.set(key, String(value)); else next.delete(key); if (key !== "sent_page") next.delete("sent_page"); setSearchParams(next); };

  return <ConsolePageLayout title="Tasks" actions={<SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} />}><div className="tasks-page" role="region" aria-label="Tasks workspace"><section className="card tasks-workspace-card"><FilterBar><div className="filter-bar-main"><SearchField id="task-search-input" label="搜索任务" value={query} placeholder="搜索项目或任务" onChange={(value) => update("q", value)} onClear={() => update("q", "")} /><SelectField id="task-type-filter" label="类型" value={category} options={[{ value: "", label: "全部类型" }, ...categories.map((value) => ({ value, label: value }))]} onChange={(value) => update("category", value)} /><SelectField id="task-state-filter" label="状态" value={taskState} options={[{ value: "", label: "全部状态" }, ...taskStates.map((value) => ({ value, label: value }))]} onChange={(value) => update("task_state", value)} /><SelectField id="task-sort-filter" label="排序" value={sort} options={sortOptions.map(([value, label]) => ({ value, label }))} onChange={(value) => update("sort", value)} /></div><div className="filter-bar-side"><SelectField id="task-page-size" label="每页" value={String(pageSize)} options={pageSizes.map((size) => ({ value: String(size), label: `${size} 条` }))} onChange={(value) => update("page_size", value)} /><span className="table-toolbar-total">共 {totalCount} 条</span></div></FilterBar>{state === "error" ? <div className="page-state page-state-error" role="alert">{error}</div> : state === "loading" && !rows.length ? <div className="page-state" role="status">正在加载…</div> : !visibleRows.length ? <div className="page-state">暂无任务。</div> : <><div className="responsive-table-wrap tasks-table-wrap"><table className="tasks-table" aria-label="Tasks"><thead><tr><th>Project</th><th>Status</th><th>Priority</th><th>Owner</th><th>Progress</th><th>ToDos</th></tr></thead><tbody>{visibleRows.map((row) => <tr key={row.id}><td><Link to={`/tasks/${row.id}`} aria-label={`查看详情 ${row.title}`}>{row.title}</Link></td><td><StatusBadge value={row.status} /></td><td><span className="pill">{row.priority || "未提供"}</span></td><td>{row.owner || "未提供"}</td><td>{row.progress || "未提供"}</td><td>{row.todo_count} 个 TODO</td></tr>)}</tbody></table></div><Pagination ariaLabel="Task pages" page={page} pageSize={pageSize} total={totalCount} onPageChange={(nextPage) => update("page", nextPage)} /></>}</section><section className="card sent-todos-section"><div className="section-head sent-todos-head"><div><h2>Sent TODOs</h2><p className="muted">DingTalk Todo and follow-up messages sent by task maintenance.</p></div><div className="sent-todos-controls"><SelectField id="sent-todo-page-size" label="每页" value={String(sentPageSize)} options={pageSizes.map((size) => ({ value: String(size), label: `${size} 条` }))} onChange={(value) => updateSent("sent_page_size", value)} /><span className="table-toolbar-total">共 {sentTotalCount} 条</span></div></div>{sentState === "loading" && !sentRows.length ? <div className="page-state" role="status">正在加载…</div> : sentState === "error" ? <div className="page-state page-state-error" role="alert">Sent TODOs 加载失败</div> : !sentRows.length ? <div className="page-state">No sent TODOs.</div> : <><div className="responsive-table-wrap sent-todos-table-wrap"><table className="sent-todos-table" aria-label="Sent TODOs"><thead><tr><th>Sent</th><th>Type</th><th>Owner</th><th>Project</th><th>TODO</th><th>DDL</th><th>Status</th><th>Original Text</th><th>Target</th></tr></thead><tbody>{sentRows.map((row) => <tr key={row.id}><td data-label="Sent">{localTime(row.sent_at)}</td><td data-label="Type"><span className="pill">{row.kind_label}</span></td><td data-label="Owner">{row.owner || "未提供"}</td><td data-label="Project">{row.detail_url ? <Link to={row.detail_url}>{row.project_title || "未提供"}</Link> : row.project_title || "未提供"}</td><td data-label="TODO">{row.detail_url ? <Link to={row.detail_url}>{row.todo_title || "未提供"}</Link> : row.todo_title || "未提供"}</td><td data-label="DDL">{localTime(row.deadline)}</td><td data-label="Status"><StatusBadge value={row.status} /></td><td data-label="Original Text">{row.original_text || row.description || "未提供"}</td><td data-label="Target">{row.target || "未提供"}</td></tr>)}</tbody></table></div><Pagination ariaLabel="Sent TODO pages" page={sentPage} pageSize={sentPageSize} total={sentTotalCount} onPageChange={(nextPage) => updateSent("sent_page", nextPage)} /></>}</section></div></ConsolePageLayout>;
}
