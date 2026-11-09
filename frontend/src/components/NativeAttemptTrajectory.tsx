import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { getResource } from "../api/console";
import { formatWorkbenchDateTime } from "../presentation";
import { StatusBadge } from "./status/StatusBadge";
import { SessionTimeline, type CodexSessionEvent } from "../pages/CodexPages";

interface NativeAttempt { id: number; runtime: string; route: string; model: string; status: string; url: string; created_at: string; finished_at: string }
interface TrajectoryPage {
  available: boolean; message: string; events: CodexSessionEvent[];
  has_more: boolean; next_cursor: number | null;
  range_start: number; range_end: number; read_through: number;
  attempt: NativeAttempt; runtime_attempts: NativeAttempt[];
}

export function NativeAttemptTrajectory({ id, compact = false }: { id: string; compact?: boolean }) {
  const [page, setPage] = useState<TrajectoryPage | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const endpoint = `/api/console/runtime-attempts/${encodeURIComponent(id)}/trajectory`;
  useEffect(() => {
    const request = new AbortController();
    controller.current = request;
    getResource(endpoint, request.signal).then((result) => {
      if (!request.signal.aborted) setPage(result.item as unknown as TrajectoryPage);
    }).catch((reason: unknown) => { if (!request.signal.aborted) setError(reason instanceof Error ? reason.message : "加载失败"); });
    return () => { controller.current?.abort(); };
  }, [endpoint]);
  async function loadRemaining() {
    if (!page?.has_more || loading) return;
    const request = new AbortController();
    controller.current = request;
    setLoading(true); setError("");
    let cursor = page.next_cursor;
    try {
      while (cursor !== null && !request.signal.aborted) {
        const response = await getResource(`${endpoint}?after=${cursor}`, request.signal);
        const next = response.item as unknown as TrajectoryPage;
        if (request.signal.aborted) return;
        if (!next.available) throw new Error(next.message);
        setPage((prior) => prior ? { ...next, events: [...prior.events, ...next.events] } : next);
        cursor = next.has_more ? next.next_cursor : null;
      }
    } catch (reason: unknown) {
      if (!request.signal.aborted) setError(reason instanceof Error ? reason.message : "加载失败");
    } finally { if (!request.signal.aborted) setLoading(false); }
  }
  return <div className={compact ? "native-attempt-compact" : "native-attempt"}>
    {error && <div className="console-card page-state-error" role="alert">{error}</div>}
    {!page && !error && <p className="page-state" role="status">正在读取原生轨迹…</p>}
    {page && <>
      <section className="console-card">
        <div className="task-overview-meta"><strong>{page.attempt.runtime}</strong><StatusBadge value={page.attempt.status} /><span>{page.attempt.route}</span><span>{page.attempt.model}</span></div>
        <p>原生调用 #{page.attempt.id} · {formatWorkbenchDateTime(page.attempt.created_at)?.label || page.attempt.created_at} {page.attempt.finished_at && `→ ${formatWorkbenchDateTime(page.attempt.finished_at)?.label || page.attempt.finished_at}`}</p>
        {!compact && <nav className="trajectory-attempt-links" aria-label="本次运行的原生调用">{page.runtime_attempts.map((item) => <Link key={item.id} to={item.url} aria-current={item.id === page.attempt.id ? "page" : undefined}>#{item.id} · {item.runtime} · {item.status}</Link>)}</nav>}
      </section>
      {!page.available ? <section className="console-card"><h2>原生轨迹不可用</h2><p>{page.message}</p></section> : <>
        <div className="console-card trajectory-loading">
          <span role="status">{page.has_more ? `已读取 ${page.read_through - page.range_start} / ${page.range_end - page.range_start} 条原生记录` : `已完整读取本次调用范围 · ${page.range_end - page.range_start} 条原生记录`}</span>
          {page.has_more && <button className="secondary-button" type="button" disabled={loading} onClick={() => void loadRemaining()}>{loading ? "正在加载…" : "加载剩余轨迹"}</button>}
        </div>
        <SessionTimeline compact={compact} events={page.events} terminal={!page.has_more && ["failed", "completed", "superseded"].includes(page.attempt.status)} />
      </>}
    </>}
  </div>;
}
