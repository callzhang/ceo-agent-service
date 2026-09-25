import { useEffect, useState } from "react";
import { getEmailProcessingProgress, type EmailProcessingProgress as Progress } from "../../api/console";
import { errorMessage, localTime } from "./shared";

const ACTIVE_POLL_MS = 5_000;
const IDLE_POLL_MS = 30_000;

export function backlogOf(data: Progress) {
  return data.provider_actions.pending + data.provider_actions.processing + data.classification_queue.pending + data.classification_queue.processing + data.unsubscribe_queue.pending + data.unsubscribe_queue.processing;
}

function speedNote(
  speed: Progress["throughput"],
  queued: number,
) {
  if (!speed.finished) {
    return queued > 0 ? `端到端速度：最近 ${speed.window_minutes} 分钟没有完成的动作` : "";
  }
  const each = speed.median_seconds != null ? ` · 每项约 ${speed.median_seconds} 秒` : "";
  const wait = queued > 0 && speed.per_minute > 0 ? ` · 预计还要 ${waitLabel(queued / speed.per_minute)}` : "";
  return `端到端 ${speed.per_minute} 项/分钟${each}${wait}`;
}

function waitLabel(minutes: number) {
  if (minutes < 1) return "不到 1 分钟";
  if (minutes < 60) return `${Math.ceil(minutes)} 分钟`;
  const hours = minutes / 60;
  return hours < 24 ? `${hours.toFixed(1)} 小时` : `${Math.ceil(hours / 24)} 天`;
}

export function EmailProcessingProgress() {
  const [data, setData] = useState<Progress | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let disposed = false;
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      controller = new AbortController();
      let delay = IDLE_POLL_MS;
      try {
        const next = await getEmailProcessingProgress(controller.signal);
        if (disposed) return;
        setData(next); setError("");
        delay = backlogOf(next) > 0 ? ACTIVE_POLL_MS : IDLE_POLL_MS;
      } catch (reason) {
        if (disposed || controller.signal.aborted) return;
        setError(errorMessage(reason));
      } finally {
        if (!disposed) timer = setTimeout(() => { void poll(); }, delay);
      }
    };
    void poll();
    return () => { disposed = true; controller?.abort(); if (timer !== null) clearTimeout(timer); };
  }, []);

  if (!data) {
    return <section className="email-progress" aria-label="邮件处理进度">{error ? <p role="alert" className="email-progress-warning">邮件处理进度读取失败：{error}</p> : <p role="status">正在读取邮件处理进度…</p>}</section>;
  }
  const actions = data.provider_actions;
  const planned = actions.done + actions.pending + actions.processing + actions.failed + actions.skipped;
  const percent = planned ? Math.round(((actions.done + actions.skipped) / planned) * 100) : 100;
  const backlog = backlogOf(data);
  const scanErrors = data.scans.filter(scan => scan.last_error);
  const heading = backlog > 0 ? `邮件处理中 · 还有 ${backlog} 项在排队` : "邮件已处理完，队列为空";
  return <section className="email-progress" aria-label="邮件处理进度">
    <div className="email-progress-heading">
      <strong>{heading}</strong>
      <span>近 {data.window_hours} 小时邮箱动作 {actions.done + actions.skipped}/{planned}</span>
      <span>{speedNote(data.throughput, actions.pending + actions.processing)}</span>
    </div>
    <div className="email-progress-track" role="progressbar" aria-label="邮箱动作完成比例" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><span style={{width: `${percent}%`}}/></div>
    <div className="email-progress-counts">
      {actions.pending + actions.processing > 0 && <span>邮箱动作待执行 {actions.pending + actions.processing}</span>}
      {actions.failed > 0 && <span className="email-progress-warning">邮箱动作失败 {actions.failed}</span>}
      {data.classification_queue.pending + data.classification_queue.processing > 0 && <span>Agent 分类排队 {data.classification_queue.pending + data.classification_queue.processing}</span>}
      {data.unsubscribe_queue.pending + data.unsubscribe_queue.processing > 0 && <span>退订任务排队 {data.unsubscribe_queue.pending + data.unsubscribe_queue.processing}</span>}
      <span>待你确认 {data.waiting_for_owner}</span>
      {data.scans.map(scan => <span key={scan.account + scan.folder} className={scan.last_error ? "email-progress-warning" : ""} title={`${scan.account} · ${scan.folder} 已扫描到 UID ${scan.last_seen_uid}`}>{scan.account} {scan.folder} 最近扫描 {scan.last_success_at ? localTime(scan.last_success_at) : "从未成功"}</span>)}
    </div>
    {scanErrors.map(scan => <p key={scan.account + scan.folder} role="alert" className="email-progress-inline-error">{scan.account} {scan.folder} 扫描出错：{scan.last_error}</p>)}
    {error && <p role="alert" className="email-progress-inline-error">进度暂时无法刷新：{error}</p>}
  </section>;
}
