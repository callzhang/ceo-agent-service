import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { displayValue, listHistory, type HistoryChart, type HistoryItem } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { StatusBadge } from "../components/status/StatusBadge";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

const statusFilters = ["sent", "reacted", "skipped", "needs_human", "blocked", "failed", "done"];
const objectFilters = ["replay", "wechat", "approval", "task", "meeting"];
const pageSizes = [20, 50, 100];

function localTime(value: string) {
  if (!value) return "未提供";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function HistoryChart({ chart }: { chart?: HistoryChart }) {
  const values = useMemo(() => chart?.labels.map((label, index) => ({ label, value: chart.series.reduce((total, series) => total + Number(series.data[index] || 0), 0) })) || [], [chart]);
  const max = Math.max(1, ...values.map((item) => item.value));
  return (
    <section className="card history-chart-card" aria-label="Recent 24 hour events">
      <div className="history-chart-head"><div><h2 className="history-chart-title">最近 24 小时事件</h2><div className="history-chart-subtitle">{chart?.range || "暂无快照"}</div></div><span className="pill">{chart?.total || 0} events</span></div>
      {values.length ? <div className="history-chart-bars" role="img" aria-label={`最近 24 小时共 ${chart?.total || 0} 个事件`}>{values.map((item) => <span key={item.label} className="history-chart-bar" style={{ height: `${Math.max(4, item.value / max * 100)}%` }} title={`${item.label}: ${item.value}`} />)}</div> : <div className="history-chart-empty">暂无事件</div>}
    </section>
  );
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
  const [chart, setChart] = useState<HistoryChart>();
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

  return <ConsolePageLayout title="History" actions={<SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} />}><div className="history-page" role="region" aria-label="History workspace"><HistoryChart chart={chart} /><section className="card history-workspace-card"><div className="table-toolbar history-toolbar"><div className="table-toolbar-left"><label className="table-toolbar-search"><span className="sr-only">搜索历史</span><input id="history-search-input" type="text" value={query} placeholder="搜索" onChange={(event) => update("q", event.target.value)} />{query && <button type="button" className="table-search-clear" aria-label="清除搜索" onClick={() => update("q", "")}>×</button>}</label><select className="table-type-select" aria-label="History status filter" value={status} onChange={(event) => update("status", event.target.value)}><option value="">type: all</option>{statusFilters.map((value) => <option key={value} value={value}>{value}</option>)}</select><select className="table-type-select history-object-type-select" aria-label="History object filter" value={objectType} onChange={(event) => update("object_type", event.target.value)}><option value="">对象：全部</option>{objectFilters.map((value) => <option key={value} value={value}>{value}</option>)}</select></div><div className="table-toolbar-right"><select className="table-page-size" aria-label="History page size" value={pageSize} onChange={(event) => update("page_size", event.target.value)}>{pageSizes.map((size) => <option key={size} value={size}>{size}/页</option>)}</select><span className="table-toolbar-total">共 {total || "—"} 条</span></div></div>{state === "error" ? <div className="page-state page-state-error" role="alert">{error}</div> : state === "loading" && !rows.length ? <div className="page-state" role="status">正在加载…</div> : !rows.length ? <div className="page-state">No reply attempts recorded.</div> : <><section className="attempt-feed" aria-label="执行历史">{rows.map((row) => <article className={`attempt-item history-kind-${row.kind || row.type}`} role="article" aria-label={row.title} key={`${row.kind || row.type}-${row.id}`}><div className="attempt-head"><div className="attempt-title"><Link className="attempt-id" to={row.detail_url || `/attempts/${row.id}`}>#{row.id}</Link><span className={`history-type-badge history-type-${row.kind || row.type}`}>{row.type || row.kind || "History"}</span><StatusBadge value={row.status} /><div className="attempt-main">{row.title}</div><div className="attempt-meta">{row.actor || "未提供"}</div></div><div className="attempt-side"><time className="attempt-time">{localTime(row.occurred_at)}</time><div className="attempt-actions"><Link className="review-link" to={row.detail_url || `/attempts/${row.id}`}>查看详情</Link></div></div></div><div className="attempt-lines">{row.input && <div className="attempt-line"><span className="attempt-label">问</span><span className="attempt-copy">{displayValue(row.input)}</span></div>}{row.output && <div className="attempt-line"><span className="attempt-label">答</span><span className="attempt-copy">{displayValue(row.output)}</span></div>}{!row.output && <div className="attempt-line"><span className="attempt-label">结果</span><span className="attempt-copy">{displayValue(row.summary)}</span></div>}</div></article>)}</section><Pagination page={page} pageSize={pageSize} total={total} onPageChange={(nextPage) => update("page", nextPage)} /></>}</section></div></ConsolePageLayout>;
}
