import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { displayValue, listFeedback, syncFeedback, type FeedbackItem } from "../api/console";
import { ResponsiveDataList } from "../components/data/ResponsiveDataList";
import { SummaryText } from "../components/data/SummaryText";
import { FilterBar } from "../components/filters/FilterBar";
import { SearchField } from "../components/filters/SearchField";
import { SelectField } from "../components/filters/SelectField";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { StatusBadge } from "../components/status/StatusBadge";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

const pageSizes = [20, 50, 100];

function localTime(value: string) {
  if (!value) return "未提供";
  const parsed = new Date(value.includes("T") ? value : `${value.replace(" ", "T")}Z`);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", { hour12: false });
}

function Pagination({ page, pageSize, total, onPageChange }: { page: number; pageSize: number; total: number; onPageChange: (page: number) => void }) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  if (pageCount <= 1) return null;
  return <nav className="pagination" aria-label="用户反馈分页"><button type="button" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>上一页</button><span>第 {page} / {pageCount} 页</span><button type="button" disabled={page >= pageCount} onClick={() => onPageChange(page + 1)}>下一页</button></nav>;
}

export function FeedbackPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [rows, setRows] = useState<FeedbackItem[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const [pendingCount, setPendingCount] = useState(0);
  const [totalCount, setTotalCount] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const query = searchParams.get("q") || "";
  const status = searchParams.get("status") || "";
  const pageValue = Number(searchParams.get("page") || 1);
  const page = Number.isFinite(pageValue) && pageValue > 0 ? pageValue : 1;
  const pageSizeValue = Number(searchParams.get("page_size") || 20);
  const pageSize = pageSizes.includes(pageSizeValue) ? pageSizeValue : 20;

  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    setError("");
    listFeedback({ q: query, status, page, page_size: pageSize }, controller.signal).then((result) => {
      setRows(result.items);
      setTotalCount(result.meta.total || 0);
      setPendingCount(result.pending_count ?? result.items.filter((row) => row.status === "pending").length);
      setSnapshot(result.meta.snapshot_at);
      setState("ready");
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : "加载失败");
      setState("error");
    });
    return () => controller.abort();
  }, [page, pageSize, query, status]);

  const update = (key: string, value: string | number) => {
    const next = new URLSearchParams(searchParams);
    if (String(value)) next.set(key, String(value)); else next.delete(key);
    if (key !== "page" && key !== "page_size") next.delete("page");
    setSearchParams(next);
  };

  const sync = async () => {
    setSyncing(true);
    setError("");
    try {
      await syncFeedback();
      setSearchParams(new URLSearchParams(searchParams));
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "同步反馈失败");
    } finally {
      setSyncing(false);
    }
  };

  const listState = state === "loading" ? "loading" : state === "error" ? "error" : rows.length ? "ready" : "empty";
  return <ConsolePageLayout title="用户反馈" actions={<><span className="feedback-pending-badge">待处理 {pendingCount}</span><SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} /></>}>
    <section className="console-card feedback-workspace-card">
      <div className="card-head"><div><p className="feedback-card-title">用户反馈</p><p className="muted">记录来自对话方的评分与处理结果。</p></div><button className="compact-button" type="button" disabled={syncing} onClick={() => void sync()}>{syncing ? "同步中…" : "同步最新反馈"}</button></div>
      <FilterBar><div className="filter-bar-main"><SearchField id="feedback-search-input" label="搜索反馈" value={query} placeholder="搜索反馈内容或上下文" onChange={(value) => update("q", value)} onClear={() => update("q", "")} /><SelectField id="feedback-status" label="状态" value={status} options={[{ value: "", label: "全部状态" }, { value: "pending", label: "待处理" }, { value: "processing", label: "处理中" }, { value: "resolved", label: "已处理" }]} onChange={(value) => update("status", value)} /></div><div className="filter-bar-side"><SelectField id="feedback-page-size" label="每页" value={String(pageSize)} options={pageSizes.map((size) => ({ value: String(size), label: `${size} 条` }))} onChange={(value) => update("page_size", value)} /><span className="table-toolbar-total">共 {listState === "loading" ? "—" : totalCount} 条</span></div></FilterBar>
      {error && <div className="page-state page-state-error" role="alert">{error}</div>}
      <ResponsiveDataList ariaLabel="用户反馈列表" columns={[{ key: "status", label: "状态" }, { key: "rating", label: "评分" }, { key: "comment", label: "评语" }, { key: "context", label: "上下文" }, { key: "created_at", label: "时间" }, { key: "id", label: "操作" }]} rows={rows} state={listState} errorMessage={error} emptyMessage="当前没有用户反馈" expandable renderCell={(row, key) => {
        if (key === "status") return <StatusBadge value={row.status} />;
        if (key === "comment") return <SummaryText value={row.comment} />;
        if (key === "context") return <SummaryText value={displayValue(row.context)} />;
        if (key === "created_at") return localTime(row.created_at);
        if (key === "id") return <span className="console-page-actions">{row.attempt_id && <Link to={`/attempts/${row.attempt_id}`}>Attempt</Link>}{row.processing_task_id && <Link to={`/?task=${encodeURIComponent(row.processing_task_id)}`}>Workbench task</Link>}{row.batch_id && <a href={`/api/console/feedback/batches/${encodeURIComponent(row.batch_id)}`}>Processing batch</a>}{!row.attempt_id && !row.processing_task_id && !row.batch_id && <span className="muted">未关联</span>}</span>;
        return displayValue(row[key]);
      }} renderExpanded={(row) => <span className="console-page-actions">{row.attempt_id ? <Link to={`/attempts/${row.attempt_id}`}>查看关联 Attempt</Link> : <span>未关联 Attempt</span>}{row.processing_task_id && <Link to={`/?task=${encodeURIComponent(row.processing_task_id)}`}>查看 Workbench task</Link>}{row.batch_id && <a href={`/api/console/feedback/batches/${encodeURIComponent(row.batch_id)}`}>查看 processing batch</a>}{row.summary && <span>{displayValue(row.summary)}</span>}</span>} />
      <Pagination page={page} pageSize={pageSize} total={totalCount} onPageChange={(nextPage) => update("page", nextPage)} />
    </section>
  </ConsolePageLayout>;
}
