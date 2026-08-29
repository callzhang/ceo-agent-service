import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { displayValue, listFeedback, syncFeedback, type FeedbackItem } from "../api/console";
import { ResponsiveDataList } from "../components/data/ResponsiveDataList";
import { SummaryText } from "../components/data/SummaryText";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { StatusBadge } from "../components/status/StatusBadge";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

export function FeedbackPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [rows, setRows] = useState<FeedbackItem[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const query = searchParams.get("q") || "";
  const status = searchParams.get("status") || "";
  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    listFeedback({ q: query, status }, controller.signal).then((page) => {
      setRows(page.items);
      setSnapshot(page.meta.snapshot_at);
      setState("ready");
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : "加载失败");
      setState("error");
    });
    return () => controller.abort();
  }, [query, status]);
  const changeStatus = (value: string) => { const next = new URLSearchParams(searchParams); if (value) next.set("status", value); else next.delete("status"); setSearchParams(next); };
  return (
    <ConsolePageLayout title="用户反馈" actions={<SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} />}>
      <section className="console-card">
        <form className="console-filter-bar" onSubmit={(event) => event.preventDefault()}>
          <label htmlFor="feedback-search">搜索反馈</label>
          <input id="feedback-search" value={query} placeholder="搜索评语、会话或状态" onChange={(event) => {
            const next = new URLSearchParams(searchParams);
            if (event.target.value) next.set("q", event.target.value); else next.delete("q");
            setSearchParams(next);
          }} />
          <label htmlFor="feedback-status">状态</label><select id="feedback-status" value={status} onChange={(event) => changeStatus(event.target.value)}><option value="">全部</option><option value="pending">未处理</option><option value="processing">处理中</option><option value="resolved">已处理</option></select>
          <button type="button" className="secondary-button" onClick={() => void syncFeedback()}>同步最新反馈</button>
        </form>
        <ResponsiveDataList
          ariaLabel="用户反馈列表"
          columns={[{ key: "status", label: "状态" }, { key: "rating", label: "评分" }, { key: "comment", label: "评语" }, { key: "context", label: "上下文" }, { key: "created_at", label: "时间" }, { key: "id", label: "操作" }]}
          rows={rows}
          state={state === "loading" ? "loading" : state === "error" ? "error" : rows.length ? "ready" : "empty"}
          errorMessage={error}
          emptyMessage="当前没有用户反馈"
          renderCell={(row, key) => {
            if (key === "status") return <StatusBadge value={row.status} />;
            if (key === "comment") return <SummaryText value={row.comment} />;
            if (key === "context") return <SummaryText value={displayValue(row.context)} />;
            if (key === "id") return <span className="console-page-actions">
              {row.attempt_id && <Link to={`/attempts/${row.attempt_id}`}>Attempt</Link>}
              {row.processing_task_id && <Link to={`/?task=${encodeURIComponent(row.processing_task_id)}`}>Workbench task</Link>}
              {row.batch_id && <Link to={`/api/console/feedback/batches/${encodeURIComponent(row.batch_id)}`}>Processing batch</Link>}
              {!row.attempt_id && !row.processing_task_id && !row.batch_id && <span className="muted">未关联</span>}
            </span>;
            return displayValue(row[key]);
          }}
          expandable
          renderExpanded={(row) => <span className="console-page-actions">
            {row.attempt_id ? <Link to={`/attempts/${row.attempt_id}`}>查看关联 Attempt</Link> : <span>未关联 Attempt</span>}
            {row.processing_task_id && <Link to={`/?task=${encodeURIComponent(row.processing_task_id)}`}>查看 Workbench task</Link>}
            {row.batch_id && <Link to={`/api/console/feedback/batches/${encodeURIComponent(row.batch_id)}`}>查看 processing batch</Link>}
          </span>}
        />
      </section>
    </ConsolePageLayout>
  );
}
