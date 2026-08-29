import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { displayValue, listHistory, type HistoryChart as HistoryChartData, type HistoryItem } from "../api/console";
import { FilterBar } from "../components/filters/FilterBar";
import { FilterChip } from "../components/filters/FilterChip";
import { StackedBarChart } from "../components/charts/StackedBarChart";
import { SelectField } from "../components/filters/SelectField";
import { SearchField } from "../components/filters/SearchField";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { StatusBadge } from "../components/status/StatusBadge";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

const HistoryChart = StackedBarChart;

const statusFilters = ["sent", "reacted", "skipped", "needs_human", "blocked", "failed", "done"];
const objectFilters = ["replay", "wechat", "approval", "task", "meeting"];
const pageSizes = [20, 50, 100];

function localTime(value: string) {
  if (!value) return "未提供";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function Pagination({ page, pageSize, total, onPageChange }: { page: number; pageSize: number; total: number; onPageChange: (page: number) => void }) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  if (pageCount <= 1) return null;
  return <div className="pagination"><div className="pagination-status"><span>{total ? `${(page - 1) * pageSize + 1}-${Math.min(page * pageSize, total)}` : "0-0"}</span><span>{page} / {pageCount}</span><span>共 {total} 条</span></div><nav className="pagination-actions" aria-label="分页导航"><button type="button" className="pagination-button" disabled={page === 1} onClick={() => onPageChange(1)}>首页</button><button type="button" className="pagination-button pagination-arrow" disabled={page === 1} aria-label="上一页" onClick={() => onPageChange(page - 1)}>‹</button><button type="button" className="pagination-button pagination-arrow" disabled={page === pageCount} aria-label="下一页" onClick={() => onPageChange(page + 1)}>›</button><button type="button" className="pagination-button" disabled={page === pageCount} onClick={() => onPageChange(pageCount)}>末页</button></nav></div>;
}

export function HistoryPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [rows, setRows] = useState<HistoryItem[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [chart, setChart] = useState<HistoryChartData>();
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const query = searchParams.get("q") || "";
  const status = searchParams.get("status") || searchParams.get("type") || "";
  const objectType = searchParams.get("object_type") || "";
  const page = Math.max(1, Number(searchParams.get("page") || 1));
  const pageSizeValue = Number(searchParams.get("page_size") || 20);
  const pageSize = pageSizes.includes(pageSizeValue) ? pageSizeValue : 20;

  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    setError("");
    listHistory({ q: query, status, object_type: objectType, page, page_size: pageSize }, controller.signal).then((result) => { setRows(result.items); setTotalCount(result.meta.total || 0); setChart(result.chart); setSnapshot(result.meta.snapshot_at); setState("ready"); }).catch((reason: unknown) => { if (controller.signal.aborted) return; setError(reason instanceof Error ? reason.message : "加载失败"); setState("error"); });
    return () => controller.abort();
  }, [query, status, objectType, page, pageSize]);

  const update = (key: string, value: string | number) => { const next = new URLSearchParams(searchParams); if (String(value)) next.set(key, String(value)); else next.delete(key); if (key !== "page") next.delete("page"); setSearchParams(next); };
  const total = totalCount;

  return <ConsolePageLayout title="History" actions={<SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} />}><div className="history-page" role="region" aria-label="History workspace"><HistoryChart chart={chart} /><section className="card history-workspace-card"><FilterBar><div className="filter-bar-main"><SearchField id="history-search-input" label="搜索历史" value={query} placeholder="搜索标题、内容或来源" onChange={(value) => update("q", value)} onClear={() => update("q", "")} /><SelectField id="history-status-filter" label="状态" value={status} options={[{ value: "", label: "全部状态" }, ...statusFilters.map((value) => ({ value, label: value }))]} onChange={(value) => update("status", value)} /><SelectField id="history-object-filter" label="对象" value={objectType} options={[{ value: "", label: "全部对象" }, ...objectFilters.map((value) => ({ value, label: value }))]} onChange={(value) => update("object_type", value)} /></div><div className="filter-bar-side"><SelectField id="history-page-size" label="每页" value={String(pageSize)} options={pageSizes.map((size) => ({ value: String(size), label: `${size} 条` }))} onChange={(value) => update("page_size", value)} /><span className="table-toolbar-total">共 {total || "—"} 条</span></div></FilterBar><div className="filter-chip-list" aria-label="快速状态筛选"><FilterChip label="全部" active={!status} onClick={() => update("status", "")} /><FilterChip label="失败" active={status === "failed"} onClick={() => update("status", "failed")} /><FilterChip label="待人工" active={status === "needs_human"} onClick={() => update("status", "needs_human")} /><FilterChip label="已完成" active={status === "done" || status === "sent"} onClick={() => update("status", "done")} /></div>{state === "error" ? <div className="page-state page-state-error" role="alert">{error}</div> : state === "loading" && !rows.length ? <div className="page-state" role="status">正在加载…</div> : !rows.length ? <div className="page-state">No reply attempts recorded.</div> : <><section className="attempt-feed" aria-label="执行历史">{rows.map((row) => <article className={`attempt-item history-kind-${row.kind || row.type}`} role="article" aria-label={row.title} key={`${row.kind || row.type}-${row.id}`}><div className="attempt-head"><div className="attempt-title"><Link className="attempt-id" to={row.detail_url || `/attempts/${row.id}`}>#{row.id}</Link><span className={`history-type-badge history-type-${row.kind || row.type}`}>{row.type || row.kind || "History"}</span><StatusBadge value={row.status} /><div className="attempt-main">{row.title}</div><div className="attempt-meta">{row.actor || "未提供"}</div></div><div className="attempt-side"><time className="attempt-time">{localTime(row.occurred_at)}</time><div className="attempt-actions"><Link className="review-link" to={row.detail_url || `/attempts/${row.id}`}>查看详情</Link></div></div></div><div className="attempt-lines">{row.input && <div className="attempt-line"><span className="attempt-label">问</span><span className="attempt-copy">{displayValue(row.input)}</span></div>}{row.output && <div className="attempt-line"><span className="attempt-label">答</span><span className="attempt-copy">{displayValue(row.output)}</span></div>}{!row.output && <div className="attempt-line"><span className="attempt-label">结果</span><span className="attempt-copy">{displayValue(row.summary)}</span></div>}</div></article>)}</section><Pagination page={page} pageSize={pageSize} total={total} onPageChange={(nextPage) => update("page", nextPage)} /></>}</section></div></ConsolePageLayout>;
}
