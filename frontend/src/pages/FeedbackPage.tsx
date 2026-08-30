import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  displayValue,
  getFeedbackDetail,
  listFeedback,
  reopenFeedback,
  syncFeedback,
  type FeedbackItem,
  type FeedbackProcessingRound,
} from "../api/console";
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

function feedbackKey(row: FeedbackItem) {
  return row.feedback_key || row.id;
}

function evidenceValue(evidence: Record<string, unknown> | undefined, key: string) {
  return evidence?.[key];
}

function testEvidenceSummary(round: FeedbackProcessingRound) {
  const tests = Object.entries(round.test_evidence || {}).slice(0, 4);
  if (!tests.length) return "未提供";
  return tests.map(([name, result]) => {
    const exitCode = result && typeof result === "object" && !Array.isArray(result) ? (result as Record<string, unknown>).exit_code : undefined;
    return `${name} exit ${typeof exitCode === "number" ? exitCode : "未提供"}`;
  }).join("；");
}

function restartEvidenceSummary(round: FeedbackProcessingRound) {
  const label = evidenceValue(round.restart_evidence, "launchd_label");
  const beforePid = evidenceValue(round.restart_evidence, "before_pid");
  const afterPid = evidenceValue(round.restart_evidence, "after_pid");
  if ((typeof beforePid === "number" || typeof beforePid === "string") && (typeof afterPid === "number" || typeof afterPid === "string")) {
    return `${typeof label === "string" && label ? `${label} · ` : ""}PID ${beforePid} → ${afterPid}`;
  }
  return Object.keys(round.restart_evidence || {}).length ? "已记录" : "未提供";
}

function healthEvidenceSummary(round: FeedbackProcessingRound) {
  const statusCode = evidenceValue(round.health_evidence, "status_code");
  const health = `${typeof statusCode === "number" ? `HTTP ${statusCode} · ` : ""}${evidenceValue(round.health_evidence, "ok") === true ? "通过" : Object.keys(round.health_evidence || {}).length ? "已记录" : "未提供"}`;
  const backlog = round.backlog_evidence || {};
  const processing = evidenceValue(backlog, "processing");
  const failed = evidenceValue(backlog, "failed");
  const retryable = evidenceValue(backlog, "retryable");
  if ([processing, failed, retryable].every((value) => typeof value === "number")) {
    return `${health}；积压 processing ${processing} / failed ${failed} / retryable ${retryable}`;
  }
  return health;
}

function ProcessingHistory({ history }: { history: readonly FeedbackProcessingRound[] }) {
  const newestFirst = [...history].sort((left, right) => right.round_number - left.round_number || right.id - left.id);
  if (!newestFirst.length) return <p className="feedback-history-empty">尚无处理历史</p>;
  return <ol className="feedback-history-list" aria-label="处理历史">
    {newestFirst.map((round) => <li className="feedback-history-item" key={round.id}>
      <div className="feedback-history-head">
        <strong>第 {round.round_number} 轮</strong>
        <StatusBadge value={round.status} />
        {round.batch_id && <a href={`/api/console/feedback/batches/${encodeURIComponent(round.batch_id)}`}>{round.batch_id}</a>}
      </div>
      <dl className="feedback-history-evidence">
        <div><dt>开始：</dt><dd>{localTime(round.started_at || round.created_at)}</dd></div>
        <div><dt>完成：</dt><dd>{localTime(round.resolved_at)}</dd></div>
        <div><dt>提交：</dt><dd>{round.commit_sha ? <code>{round.commit_sha.slice(0, 12)}</code> : "未提供"}</dd></div>
        <div><dt>测试：</dt><dd>{testEvidenceSummary(round)}</dd></div>
        <div><dt>重启：</dt><dd>{restartEvidenceSummary(round)}</dd></div>
        <div><dt>健康：</dt><dd>{healthEvidenceSummary(round)}</dd></div>
      </dl>
      <div className="feedback-history-links">
        {round.attempt_id > 0 && <Link to={`/attempts/${round.attempt_id}`}>attempt#{round.attempt_id}</Link>}
        {round.workbench_task_id && <Link to={`/?task=${encodeURIComponent(round.workbench_task_id)}`}>查看 Workbench task</Link>}
        {round.workbench_turn_id && <span>turn#{round.workbench_turn_id}</span>}
        {round.agent_run_id > 0 && <span>run#{round.agent_run_id}</span>}
      </div>
      {round.reopen_reason && <p className="feedback-history-reason"><strong>重新打开原因：</strong>{round.reopen_reason}</p>}
    </li>)}
  </ol>;
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
  const [success, setSuccess] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const [pendingCount, setPendingCount] = useState(0);
  const [totalCount, setTotalCount] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const [historyLoadingKey, setHistoryLoadingKey] = useState("");
  const [reopenTarget, setReopenTarget] = useState<FeedbackItem | null>(null);
  const [reopenReason, setReopenReason] = useState("");
  const [reopenError, setReopenError] = useState("");
  const [reopening, setReopening] = useState(false);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const successRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const restoreTriggerRef = useRef(true);
  const reopeningRef = useRef(false);
  const listGenerationRef = useRef(0);
  const listAbortRef = useRef<AbortController | null>(null);
  const historyGenerationRef = useRef(new Map<string, number>());
  reopeningRef.current = reopening;
  const query = searchParams.get("q") || "";
  const status = searchParams.get("status") || "";
  const pageValue = Number(searchParams.get("page") || 1);
  const page = Number.isFinite(pageValue) && pageValue > 0 ? pageValue : 1;
  const pageSizeValue = Number(searchParams.get("page_size") || 20);
  const pageSize = pageSizes.includes(pageSizeValue) ? pageSizeValue : 20;
  const listViewKey = `${query}\u0000${status}\u0000${page}\u0000${pageSize}`;
  const listViewKeyRef = useRef(listViewKey);
  listViewKeyRef.current = listViewKey;

  const invalidateListLoad = useCallback(() => {
    listGenerationRef.current += 1;
    listAbortRef.current?.abort();
    listAbortRef.current = null;
  }, []);

  const loadRows = useCallback(async (showLoading = true) => {
    const generation = listGenerationRef.current + 1;
    listGenerationRef.current = generation;
    listAbortRef.current?.abort();
    const controller = new AbortController();
    listAbortRef.current = controller;
    if (showLoading) setState("loading");
    setError("");
    try {
      const result = await listFeedback({ q: query, status, page, page_size: pageSize }, controller.signal);
      if (controller.signal.aborted || generation !== listGenerationRef.current) return false;
      historyGenerationRef.current.forEach((value, key) => historyGenerationRef.current.set(key, value + 1));
      setHistoryLoadingKey("");
      setRows(result.items);
      setTotalCount(result.meta.total || 0);
      setPendingCount(result.pending_count ?? result.items.filter((row) => row.status === "pending").length);
      setSnapshot(result.meta.snapshot_at);
      setState("ready");
      return true;
    } catch (reason: unknown) {
      if (controller.signal.aborted || generation !== listGenerationRef.current) return false;
      throw reason;
    } finally {
      if (generation === listGenerationRef.current) listAbortRef.current = null;
    }
  }, [page, pageSize, query, status]);
  const loadRowsRef = useRef(loadRows);
  loadRowsRef.current = loadRows;

  useEffect(() => {
    void loadRows().catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "加载失败");
      setState("error");
    });
    return invalidateListLoad;
  }, [invalidateListLoad, loadRows]);

  useEffect(() => {
    if (success) successRef.current?.focus();
  }, [success]);

  useEffect(() => {
    if (!reopenTarget) return;
    reasonRef.current?.focus();
    const containFocus = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !reopeningRef.current) {
        event.preventDefault();
        setReopenTarget(null);
        setReopenReason("");
        setReopenError("");
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>("button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])") || []);
      if (!focusable.length) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!dialogRef.current?.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", containFocus);
    return () => {
      document.removeEventListener("keydown", containFocus);
      if (restoreTriggerRef.current) returnFocusRef.current?.focus();
    };
  }, [reopenTarget]);

  const update = (key: string, value: string | number) => {
    const next = new URLSearchParams(searchParams);
    if (String(value)) next.set(key, String(value)); else next.delete(key);
    if (key !== "page" && key !== "page_size") next.delete("page");
    setSearchParams(next);
  };

  const sync = async () => {
    const requestedViewKey = listViewKeyRef.current;
    setSyncing(true);
    setError("");
    setSuccess("");
    try {
      await syncFeedback();
      if (requestedViewKey !== listViewKeyRef.current) return;
      await loadRows(false);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "同步反馈失败");
    } finally {
      setSyncing(false);
    }
  };

  const openReopen = (row: FeedbackItem) => {
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    restoreTriggerRef.current = true;
    setSuccess("");
    setReopenError("");
    setReopenReason("");
    setReopenTarget(row);
  };

  const closeReopen = () => {
    if (reopening) return;
    setReopenTarget(null);
    setReopenReason("");
    setReopenError("");
  };

  const submitReopen = async () => {
    if (!reopenTarget || reopening || !reopenReason.trim()) return;
    const key = feedbackKey(reopenTarget);
    const requestedViewKey = listViewKeyRef.current;
    setReopening(true);
    setReopenError("");
    try {
      const response = await reopenFeedback(key, reopenReason);
      const reopened = response.item && typeof response.item === "object" ? response.item as Partial<FeedbackItem> : undefined;
      historyGenerationRef.current.set(key, (historyGenerationRef.current.get(key) || 0) + 1);
      setHistoryLoadingKey((current) => current === key ? "" : current);
      setPendingCount((current) => current + 1);
      restoreTriggerRef.current = false;
      setReopenTarget(null);
      setReopenReason("");
      setSuccess("反馈已重新打开，已回到待处理列表。");
      if (requestedViewKey !== listViewKeyRef.current) {
        try {
          await loadRowsRef.current(false);
        } catch (reason: unknown) {
          setError(`反馈已重新打开，但列表刷新失败：${reason instanceof Error ? reason.message : "请手动刷新"}`);
        }
        return;
      }
      invalidateListLoad();
      const projected: Partial<FeedbackItem> = {
        status: "pending",
        processing_status: "pending",
        batch_id: "",
        processing_task_id: "",
        current_processing: reopened?.current_processing ?? null,
        processing_history: Array.isArray(reopened?.processing_history) ? reopened.processing_history : reopenTarget.processing_history,
      };
      const excludesPending = Boolean(status && status !== "pending");
      const nextTotal = excludesPending ? Math.max(0, totalCount - 1) : totalCount;
      setRows((current) => excludesPending
        ? current.filter((item) => feedbackKey(item) !== key)
        : current.map((item) => feedbackKey(item) === key ? { ...item, ...projected } : item));
      setTotalCount(nextTotal);
      setState("ready");
      const maxPage = Math.max(1, Math.ceil(nextTotal / pageSize));
      if (page > maxPage) {
        const next = new URLSearchParams(searchParams);
        if (maxPage === 1) next.delete("page"); else next.set("page", String(maxPage));
        setSearchParams(next, { replace: true });
      } else {
        try {
          await loadRows(false);
        } catch (reason: unknown) {
          setError(`反馈已重新打开，但列表刷新失败：${reason instanceof Error ? reason.message : "请手动刷新"}`);
        }
      }
    } catch (reason: unknown) {
      setReopenError(reason instanceof Error ? reason.message : "重新打开失败，请重试");
    } finally {
      setReopening(false);
    }
  };

  const loadHistory = async (row: FeedbackItem) => {
    const key = feedbackKey(row);
    const generation = (historyGenerationRef.current.get(key) || 0) + 1;
    historyGenerationRef.current.set(key, generation);
    setHistoryLoadingKey(key);
    setError("");
    try {
      const detail = await getFeedbackDetail(key);
      if (historyGenerationRef.current.get(key) !== generation) return;
      setRows((current) => current.map((item) => {
        if (feedbackKey(item) !== key) return item;
        return {
          ...item,
          ...(Object.prototype.hasOwnProperty.call(detail.item, "current_processing") ? { current_processing: detail.item.current_processing } : {}),
          ...(Object.prototype.hasOwnProperty.call(detail.item, "processing_history") ? { processing_history: detail.item.processing_history } : {}),
        };
      }));
    } catch (reason: unknown) {
      if (historyGenerationRef.current.get(key) === generation) setError(reason instanceof Error ? reason.message : "加载处理历史失败");
    } finally {
      if (historyGenerationRef.current.get(key) === generation) setHistoryLoadingKey("");
    }
  };

  const listState = state === "loading" ? "loading" : state === "error" ? "error" : rows.length ? "ready" : "empty";
  return <ConsolePageLayout title="用户反馈" actions={<><span className="feedback-pending-badge">待处理 {pendingCount}</span><SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} /></>}>
    <section className="console-card feedback-workspace-card">
      <div className="card-head"><div><p className="feedback-card-title">用户反馈</p><p className="muted">记录来自对话方的评分与处理结果。</p></div><button className="compact-button" type="button" disabled={syncing} onClick={() => void sync()}>{syncing ? "同步中…" : "同步最新反馈"}</button></div>
      <FilterBar><div className="filter-bar-main"><SearchField id="feedback-search-input" label="搜索反馈" value={query} placeholder="搜索反馈内容或上下文" onChange={(value) => update("q", value)} onClear={() => update("q", "")} /><SelectField id="feedback-status" label="状态" value={status} options={[{ value: "", label: "全部状态" }, { value: "pending", label: "待处理" }, { value: "processing", label: "处理中" }, { value: "resolved", label: "已处理" }]} onChange={(value) => update("status", value)} /></div><div className="filter-bar-side"><SelectField id="feedback-page-size" label="每页" value={String(pageSize)} options={pageSizes.map((size) => ({ value: String(size), label: `${size} 条` }))} onChange={(value) => update("page_size", value)} /><span className="table-toolbar-total">共 {listState === "loading" ? "—" : totalCount} 条</span></div></FilterBar>
      {success && <div ref={successRef} className="feedback-success" role="status" aria-label="操作成功" tabIndex={-1}>{success}</div>}
      {error && <div className="page-state page-state-error" role="alert">{error}</div>}
      <ResponsiveDataList ariaLabel="用户反馈列表" columns={[{ key: "status", label: "状态" }, { key: "rating", label: "评分" }, { key: "comment", label: "评语" }, { key: "context", label: "上下文" }, { key: "created_at", label: "时间" }, { key: "id", label: "操作" }]} rows={rows} state={listState} errorMessage={error} emptyMessage="当前没有用户反馈" expandable renderCell={(row, key) => {
        if (key === "status") return <StatusBadge value={row.status} />;
        if (key === "comment") return <SummaryText value={row.comment} />;
        if (key === "context") return <SummaryText value={displayValue(row.context)} />;
        if (key === "created_at") return localTime(row.created_at);
        if (key === "id") return <span className="console-page-actions">{row.attempt_id && <Link to={`/attempts/${row.attempt_id}`}>Attempt</Link>}{row.processing_task_id && <Link to={`/?task=${encodeURIComponent(row.processing_task_id)}`}>Workbench task</Link>}{row.batch_id && <a href={`/api/console/feedback/batches/${encodeURIComponent(row.batch_id)}`}>Processing batch</a>}{row.status === "resolved" && <button className="feedback-reopen-button" type="button" onClick={() => openReopen(row)}>重新打开反馈</button>}{!row.attempt_id && !row.processing_task_id && !row.batch_id && row.status !== "resolved" && <span className="muted">未关联</span>}</span>;
        return displayValue(row[key]);
      }} renderExpanded={(row) => <div className="feedback-expanded">
        <div className="console-page-actions">{row.references.map((reference) => reference.route ? <a href={reference.route} key={`${reference.label}:${reference.route}`}>{reference.label}</a> : <span key={reference.label}>{reference.label}</span>)}{row.summary && <span>{displayValue(row.summary)}</span>}</div>
        <h3>处理历史</h3>
        {row.processing_history ? <ProcessingHistory history={row.processing_history} /> : <button className="secondary-button feedback-history-load" type="button" disabled={historyLoadingKey === feedbackKey(row)} onClick={() => void loadHistory(row)}>{historyLoadingKey === feedbackKey(row) ? "正在加载处理历史…" : "加载处理历史"}</button>}
      </div>} />
      <Pagination page={page} pageSize={pageSize} total={totalCount} onPageChange={(nextPage) => update("page", nextPage)} />
    </section>
    {reopenTarget && <>
      <div className="feedback-reopen-scrim" aria-hidden="true" onMouseDown={closeReopen} />
      <section ref={dialogRef} className="feedback-reopen-dialog" role="dialog" aria-modal="true" aria-labelledby="feedback-reopen-title" aria-describedby="feedback-reopen-help">
        <h2 id="feedback-reopen-title">重新打开反馈</h2>
        <p id="feedback-reopen-help">请写明此前为何过早完成，以及还缺少哪项可核验结果。</p>
        {reopenError && <p className="feedback-reopen-error" role="alert">{reopenError}</p>}
        <label htmlFor="feedback-reopen-reason">重新打开原因</label>
        <textarea ref={reasonRef} id="feedback-reopen-reason" rows={5} value={reopenReason} disabled={reopening} onChange={(event) => setReopenReason(event.target.value)} />
        <div className="feedback-reopen-actions"><button className="secondary-button" type="button" disabled={reopening} onClick={closeReopen}>取消</button><button className="primary-button" type="button" disabled={reopening || !reopenReason.trim()} onClick={() => void submitReopen()}>{reopening ? "正在重新打开…" : "确认重新打开"}</button></div>
      </section>
    </>}
  </ConsolePageLayout>;
}
