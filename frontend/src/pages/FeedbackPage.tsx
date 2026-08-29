import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { listFeedback, resolveFeedback, syncFeedback, type FeedbackItem } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { StatusBadge } from "../components/status/StatusBadge";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

const pageSizes = [20, 50, 100];

function localTime(value: string) {
  if (!value) return "未提供";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function Pagination({ page, pageSize, total, onPageChange }: { page: number; pageSize: number; total: number; onPageChange: (page: number) => void }) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  if (pageCount <= 1) return null;
  return <div className="pagination"><div className="pagination-status"><span>{(page - 1) * pageSize + 1}-{Math.min(page * pageSize, total)}</span><span>{page} / {pageCount}</span><span>共 {total} 条</span></div><nav className="pagination-actions" aria-label="Feedback pages"><button type="button" className="pagination-button" disabled={page === 1} onClick={() => onPageChange(1)}>首页</button><button type="button" className="pagination-button pagination-arrow" disabled={page === 1} aria-label="上一页" onClick={() => onPageChange(page - 1)}>‹</button><button type="button" className="pagination-button pagination-arrow" disabled={page === pageCount} aria-label="下一页" onClick={() => onPageChange(page + 1)}>›</button><button type="button" className="pagination-button" disabled={page === pageCount} onClick={() => onPageChange(pageCount)}>末页</button></nav></div>;
}

export function FeedbackPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [rows, setRows] = useState<FeedbackItem[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [pendingCount, setPendingCount] = useState(0);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [resolving, setResolving] = useState("");
  const query = searchParams.get("q") || "";
  const status = searchParams.get("status") || "";
  const page = Math.max(1, Number(searchParams.get("page") || 1));
  const pageSizeValue = Number(searchParams.get("page_size") || 50);
  const pageSize = pageSizes.includes(pageSizeValue) ? pageSizeValue : 50;

  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    setError("");
    listFeedback({ q: query, status, page, page_size: pageSize }, controller.signal).then((result) => { setRows(result.items); setTotalCount(result.meta.total || 0); setPendingCount(result.pending_count ?? result.items.filter((row) => row.status === "pending").length); setSnapshot(result.meta.snapshot_at); setState("ready"); }).catch((reason: unknown) => { if (controller.signal.aborted) return; setError(reason instanceof Error ? reason.message : "加载失败"); setState("error"); });
    return () => controller.abort();
  }, [query, status, page, pageSize]);

  const update = (key: string, value: string | number) => { const next = new URLSearchParams(searchParams); if (String(value)) next.set(key, String(value)); else next.delete(key); if (key !== "page") next.delete("page"); setSearchParams(next); };
  const resolve = async (id: string) => { setResolving(id); setError(""); try { await resolveFeedback(id); setRows((current) => current.map((row) => row.id === id ? { ...row, status: "resolved" } : row)); setPendingCount((count) => Math.max(0, count - 1)); } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "保存失败"); } finally { setResolving(""); } };
  const sync = async () => { setSyncing(true); setError(""); try { await syncFeedback(); window.location.reload(); } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "同步反馈失败"); } finally { setSyncing(false); } };

  return <ConsolePageLayout title="用户反馈" actions={<><span className="feedback-pending-badge">待处理 {pendingCount}</span><SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} /></>}><div className="feedback-page" role="region" aria-label="用户反馈工作区"><section className="card feedback-workspace-card"><div className="card-head"><div><p className="feedback-card-title">用户反馈</p><p className="muted">记录来自对话方的评分与处理结果。</p></div><button className="compact-button" type="button" disabled={syncing} onClick={() => void sync()}>{syncing ? "同步中…" : "同步最新反馈"}</button></div><div className="table-toolbar feedback-toolbar"><div className="table-toolbar-left"><label className="table-toolbar-search"><span className="sr-only">搜索反馈</span><input id="feedback-search-input" type="text" value={query} placeholder="搜索" onChange={(event) => update("q", event.target.value)} />{query && <button type="button" className="table-search-clear" aria-label="清除搜索" onClick={() => update("q", "")}>×</button>}</label><select id="feedback-status" className="table-type-select" aria-label="反馈状态筛选" value={status} onChange={(event) => update("status", event.target.value)}><option value="">status: all</option><option value="pending">pending</option><option value="resolved">resolved</option></select></div><div className="table-toolbar-right"><select className="table-page-size" aria-label="Feedback page size" value={pageSize} onChange={(event) => update("page_size", event.target.value)}>{pageSizes.map((size) => <option key={size} value={size}>{size}/页</option>)}</select><span className="table-toolbar-total">共 {totalCount} 条</span></div></div>{error && <div className="page-state page-state-error" role="alert">{error}</div>}{state === "loading" && !rows.length ? <div className="page-state" role="status">正在加载…</div> : state === "error" && !rows.length ? null : !rows.length ? <div className="page-state">暂无用户反馈。</div> : <><div className="responsive-table-wrap feedback-table-wrap"><table className="user-feedback-table" aria-label="用户反馈"><thead><tr><th>状态</th><th>评分</th><th>用户反馈</th><th>时间</th><th>操作</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td><StatusBadge value={row.status} /></td><td>{row.rating || "未提供"}</td><td><div className="user-feedback-comment">{row.comment || "未填写评语"}</div><div className="user-feedback-context">{row.context || "未关联上下文"}</div></td><td>{localTime(row.created_at)}</td><td><div className="user-feedback-actions">{row.attempt_id ? <Link className="review-link" to={`/attempts/${row.attempt_id}`}>处理</Link> : <span className="muted">未关联</span>}{row.status === "pending" ? <button type="button" disabled={resolving === row.id} onClick={() => void resolve(row.id)}>{resolving === row.id ? "保存中…" : "标记已处理"}</button> : <span className="muted">已处理</span>}</div></td></tr>)}</tbody></table></div><Pagination page={page} pageSize={pageSize} total={totalCount} onPageChange={(nextPage) => update("page", nextPage)} /></>}</section></div></ConsolePageLayout>;
}
