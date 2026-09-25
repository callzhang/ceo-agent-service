import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { displayValue, getHistoryChart, listHistory, listHistoryTypes, type HistoryChart as HistoryChartData, type HistoryItem, type HistoryTypeOption } from "../api/console";
import { FilterBar } from "../components/filters/FilterBar";
import { StackedBarChart } from "../components/charts/StackedBarChart";
import { MultiSelectField } from "../components/filters/MultiSelectField";
import { SelectField } from "../components/filters/SelectField";
import { SearchField } from "../components/filters/SearchField";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { StatusBadge, statusLabel } from "../components/status/StatusBadge";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

const MARKDOWN_LINK = /\[([^\]]+)\]\((?:https?:\/\/|dingtalk:\/\/)[^)]*\)/gi;
const BARE_LINK = /(?:https?:\/\/|dingtalk:\/\/)\S+/gi;

/** One readable line: a message's own words, with its links named rather than spelled out. */
export function previewText(value: unknown) {
  return displayValue(value)
    .replace(MARKDOWN_LINK, "$1")
    .replace(BARE_LINK, "[链接]")
    .replace(/\s+/g, " ")
    .trim();
}

/** Whether a line is the service's own status code rather than something a person wrote. */
export function isStatusCode(text: string) {
  return text.length <= 160 && text.includes("_") && !/[\u4e00-\u9fff]/.test(text);
}

const TRAILING_CODE = /(?:^|\s)([a-z][\w.:-]*_[\w.:-]*)$/;

/** Split a line into what a person wrote and the status code appended to it. */
export function splitStatusCode(text: string): { copy: string; code: string } {
  if (isStatusCode(text)) return { copy: "", code: text };
  const match = TRAILING_CODE.exec(text);
  if (!match) return { copy: text, code: "" };
  return { copy: text.slice(0, match.index).trim(), code: match[1] };
}

function HistoryChart({ chart, loading }: { chart?: HistoryChartData; loading?: boolean }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const range = searchParams.get("chart_range") || "24h";
  return <StackedBarChart chart={chart} loading={loading} range={range} onRangeChange={(nextRange) => { const next = new URLSearchParams(searchParams); next.set("chart_range", nextRange); next.delete("page"); setSearchParams(next); }} />;
}

// `done` also covers sent replies on the service side, so there is no separate 已发送.
const statusFilters = ["pending", "processing", "done", "failed", "recovered", "needs_human", "skipped", "blocked", "reacted"];
const statusOptions = statusFilters.map((value) => ({ value, label: statusLabel(value) }));
const listParam = (value: string) => value.split(",").map((item) => item.trim()).filter(Boolean);
const pageSizes = [20, 50, 100];
const HISTORY_REFRESH_INTERVAL_MS = 10_000;

/** Month, day and minute: what a one-line row has room for; the full time is the tooltip. */
function shortTime(value: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

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
  const [chartState, setChartState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const [refreshEpoch, setRefreshEpoch] = useState(0);
  // The service owns the type list (Derek 2026-09-25); if it cannot be read,
  // the filter offers only 全部 rather than a stale copy.
  const [typeOptions, setTypeOptions] = useState<HistoryTypeOption[]>([]);
  const requestGeneration = useRef(0);
  const query = searchParams.get("q") || "";
  const status = searchParams.get("status") || searchParams.get("type") || "";
  const objectType = searchParams.get("object_type") || "";
  const chartRange = searchParams.get("chart_range") || "24h";
  const page = Math.max(1, Number(searchParams.get("page") || 1));
  const pageSizeValue = Number(searchParams.get("page_size") || 20);
  const pageSize = pageSizes.includes(pageSizeValue) ? pageSizeValue : 20;

  useEffect(() => {
    const controller = new AbortController();
    const generation = ++requestGeneration.current;
    setState("loading");
    setError("");
    listHistory({ q: query, status, object_type: objectType, page, page_size: pageSize, include_chart: 0 }, controller.signal).then((result) => { if (generation !== requestGeneration.current) return; setRows(result.items); setTotalCount(result.meta.total || 0); setSnapshot(result.meta.snapshot_at); setState("ready"); }).catch((reason: unknown) => { if (controller.signal.aborted || generation !== requestGeneration.current) return; setError(reason instanceof Error ? reason.message : "加载失败"); setState("error"); });
    return () => controller.abort();
  }, [query, status, objectType, page, pageSize, refreshEpoch]);

  useEffect(() => {
    const controller = new AbortController();
    listHistoryTypes(controller.signal).then((items) => { if (!controller.signal.aborted) setTypeOptions(items); }).catch(() => { if (!controller.signal.aborted) setTypeOptions([]); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const refreshTimer = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        setRefreshEpoch((value) => value + 1);
      }
    }, HISTORY_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(refreshTimer);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setChartState("loading");
    getHistoryChart(chartRange, controller.signal).then((value) => { if (controller.signal.aborted) return; setChart(value); setChartState("ready"); }).catch((reason: unknown) => { if (controller.signal.aborted) return; setChartState("error"); setError(reason instanceof Error ? reason.message : "图表加载失败"); });
    return () => controller.abort();
  }, [chartRange]);

  const update = (key: string, value: string | number) => { const next = new URLSearchParams(searchParams); if (String(value)) next.set(key, String(value)); else next.delete(key); if (key !== "page") next.delete("page"); setSearchParams(next); };
  const selectedStatuses = listParam(status).filter((value) => statusFilters.includes(value));
  const total = totalCount;
  const typeLabels = new Map(typeOptions.map((option) => [option.value, option.label]));
  // A value the service no longer lists (the retired `replay`) filters nothing there, so the menu says 全部 too.
  const selectedTypes = listParam(objectType).filter((value) => typeLabels.has(value));

  return <ConsolePageLayout showHeader={false} title="History" actions={<SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} />}><div className="history-page" role="region" aria-label="History workspace"><HistoryChart chart={chart} loading={chartState === "loading"} /><section className="card history-workspace-card"><FilterBar><div className="filter-bar-main"><SearchField id="history-search-input" label="搜索历史" value={query} placeholder="搜索标题、内容或来源" onChange={(value) => update("q", value)} onClear={() => update("q", "")} /><MultiSelectField id="history-status-filter" label="状态" allLabel="全部状态" options={statusOptions} values={selectedStatuses} onChange={(values) => update("status", values.join(","))} /><MultiSelectField id="history-type-filter" label="任务类型" allLabel="全部类型" options={typeOptions} values={selectedTypes} onChange={(values) => update("object_type", values.join(","))} /></div><div className="filter-bar-side"><SelectField id="history-page-size" label="每页" value={String(pageSize)} options={pageSizes.map((size) => ({ value: String(size), label: `${size} 条` }))} onChange={(value) => update("page_size", value)} /><span className="table-toolbar-total">共 {total} 条</span></div></FilterBar>{state === "error" ? <div className="page-state page-state-error" role="alert">{error}</div> : state === "loading" && !rows.length ? <div className="page-state" role="status">正在加载…</div> : !rows.length ? <div className="page-state">No reply attempts recorded.</div> : <><section className="history-row-list" aria-label="执行历史">{rows.map((row) => {
  const typeName = typeLabels.get(row.type) || row.type || row.kind || "History";
  const { copy, code } = splitStatusCode(previewText(row.output || row.summary || row.input));
  const when = localTime(row.occurred_at);
  return <Link className={`history-dense-row history-kind-${row.kind || row.type}`} key={`${row.kind || row.type}-${row.id}`} to={row.detail_url || `/attempts/${row.id}`} aria-label={`${typeName} ${row.title}`} title={`#${row.id} · ${row.title}`}>
    <span className={`history-type-badge history-type-${row.kind || row.type}`}>{typeName}</span>
    <span className="history-row-status"><StatusBadge value={row.status} /></span>
    <span className="history-row-actor" title={row.actor}>{row.actor || "未提供"}</span>
    <span className="history-row-content"><strong>{row.title}</strong>{copy && <span className="history-row-preview">{copy}</span>}{code && <code className="attempt-copy-code" title={code}>{code}</code>}</span>
    <time dateTime={row.occurred_at} title={when}>{shortTime(row.occurred_at)}</time>
  </Link>;
})}</section><Pagination page={page} pageSize={pageSize} total={total} onPageChange={(nextPage) => update("page", nextPage)} /></>}</section></div></ConsolePageLayout>;
}
