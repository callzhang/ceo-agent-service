import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { displayValue, getResource } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";
import { StatusBadge } from "../components/status/StatusBadge";
import { previewText } from "./HistoryPage";

interface TaskRun { id: number; execution_generation: string; current: boolean; role: string; proposal_revision: number; turn_attempt: number; status: string; started_at: string; completed_at: string }
interface TaskAttempt { id: number; action: string; status: string; updated_at: string; summary: string; detail_url: string }
interface TaskHistory {
  task: { id: number; title: string; channel: string; sender: string; trigger_text: string; status: string; error: string; execution_generation: string; created_at: string; updated_at: string };
  runs: TaskRun[];
  attempts: TaskAttempt[];
}

const roleLabels: Record<string, string> = { consumer: "Consumer", audit: "Audit" };

function shortTime(value: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** One History record is a task; its attempts and runs are what happened to it. */
export function HistoryTaskPage() {
  const { taskId = "" } = useParams();
  return <HistoryTaskContent key={taskId} taskId={taskId} />;
}

function HistoryTaskContent({ taskId }: { taskId: string }) {
  const [history, setHistory] = useState<TaskHistory | null>(null);
  const [snapshot, setSnapshot] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setHistory(null);
    setError("");
    getResource(`/api/console/history/tasks/${encodeURIComponent(taskId)}`, controller.signal)
      .then((response) => { if (controller.signal.aborted) return; setHistory(response.item as unknown as TaskHistory); setSnapshot(response.meta.snapshot_at); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "加载失败"); });
    return () => controller.abort();
  }, [taskId]);

  const task = history?.task;
  return (
    <ConsolePageLayout title="Task 历史" actions={<><SnapshotBadge timestamp={snapshot} /><Link className="secondary-button" to="/history">返回 History</Link></>}>
      {error && <div className="page-state page-state-error" role="alert">{error}</div>}
      {!history && !error && <div className="page-state" role="status">正在加载…</div>}
      {history && task && <>
        <section className="console-card">
          <div className="task-overview-meta"><StatusBadge value={task.status} /><span>{task.channel}</span><span>{task.sender || "未提供"}</span></div>
          <h2>{task.title || `Task #${task.id}`}</h2>
          <dl className="detail-definition-list">
            <div><dt>内容</dt><dd>{previewText(task.trigger_text) || "未提供"}</dd></div>
            {task.error && <div><dt>错误</dt><dd>{displayValue(task.error)}</dd></div>}
            <div><dt>创建</dt><dd>{shortTime(task.created_at)}</dd></div>
            <div><dt>最近更新</dt><dd>{shortTime(task.updated_at)}</dd></div>
          </dl>
        </section>
        <section className="console-card" aria-label="处理结果">
          <h2>处理结果 · {history.attempts.length}</h2>
          <div className="history-row-list">
            {history.attempts.map((attempt) => <Link key={attempt.id} className="history-dense-row" to={attempt.detail_url} title={`Attempt #${attempt.id}`}>
              <span className="history-type-badge">Attempt</span>
              <span className="history-row-status"><StatusBadge value={attempt.status} /></span>
              <span className="history-row-actor">#{attempt.id}</span>
              <span className="history-row-content"><strong>{attempt.action}</strong>{attempt.summary && <span className="history-row-preview">{previewText(attempt.summary)}</span>}</span>
              <time dateTime={attempt.updated_at}>{shortTime(attempt.updated_at)}</time>
            </Link>)}
            {!history.attempts.length && <div className="page-state">还没有产生 Attempt。</div>}
          </div>
        </section>
        <section className="console-card" aria-label="执行过程">
          <h2>执行过程 · {history.runs.length}</h2>
          <div className="history-row-list">
            {history.runs.map((run) => <div key={run.id} className="history-dense-row" title={`Run #${run.id}`}>
              <span className="history-type-badge">{roleLabels[run.role] || run.role}</span>
              <span className="history-row-status"><StatusBadge value={run.status} /></span>
              <span className="history-row-actor">{run.current ? "当前代" : "历史代"}</span>
              <span className="history-row-content"><strong>修订 {run.proposal_revision} · 第 {run.turn_attempt + 1} 轮</strong><span className="history-row-preview">{run.execution_generation}</span></span>
              <time dateTime={run.started_at}>{shortTime(run.started_at)}</time>
            </div>)}
          </div>
        </section>
      </>}
    </ConsolePageLayout>
  );
}
