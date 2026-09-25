import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { getEmailUnsubscribeEntryUrl, type EmailClassificationItem, type EmailObservabilityEvent } from "../../api/console";
import { localTime } from "./shared";
function actionPlanEvidence(row: EmailClassificationItem) {
  const plan = row.action_plan;
  const planId = typeof plan.action_plan_id === "string" ? plan.action_plan_id : row.current_action_plan_id;
  const planVersion = typeof plan.action_plan_version === "number" ? plan.action_plan_version : null;
  const planActions = Array.isArray(plan.actions)
    ? plan.actions.filter((action): action is string => typeof action === "string")
    : [];
  return { planId, planVersion, planActions };
}

export function ProcessedClassificationEvidence({ row }: { row: EmailClassificationItem }) {
  const plan = actionPlanEvidence(row);
  return <section aria-label="分类与 ActionPlan 证据">
    <h3>分类与 ActionPlan</h3>
    <dl className="detail-definition-list">
      <div><dt>完整模型 ID</dt><dd>{row.model_version || "未提供"}</dd></div>
      <div><dt>配置版本</dt><dd>{row.config_version || "未提供"}</dd></div>
      <div><dt>ActionPlan ID</dt><dd>{plan.planId || "未提供"}</dd></div>
      <div><dt>ActionPlan 版本</dt><dd>{plan.planVersion === null ? "未提供" : `版本 ${plan.planVersion}`}</dd></div>
      <div><dt>授权动作</dt><dd>{plan.planActions.length ? plan.planActions.join("、") : "无固定动作"}</dd></div>
    </dl>
  </section>;
}



export function actionResultLabel(event: EmailObservabilityEvent): string {
  if (event.kind === "unsubscribe") {
    if (event.outcome === "done") return "退订成功";
    if (event.outcome === "already_unsubscribed") return "该来源已退订";
    if (event.outcome?.startsWith("skipped")) return "本次未完成退订";
    if (event.status === "failed" || event.outcome?.startsWith("failed")) return "退订执行失败";
    if (event.status === "done") return "退订任务已结束，结果未知";
    const states: Record<string, string> = {
      processing: "正在退订", pending: "退订待执行",
      needs_human: "退订需要人工处理", needs_feedback: "退订等待反馈",
      skipped: "退订已跳过",
    };
    return states[event.status] || "退订状态未知";
  }
  const operations: Record<string, string> = {trash: "移入已删除邮件", move: "移动邮件", flag_important: "标记重要", mark_read: "标为已读", label: "打标签", archive: "归档"};
  const action = operations[event.operation] || event.operation || "邮箱动作";
  return event.status === "done" || event.status === "succeeded" ? `已完成：${action}` : event.status === "failed" ? `${action}失败` : `${action} · ${event.status === "processing" ? "处理中" : "待执行"}`;
}

export function unsubscribeReason(outcome: string | null | undefined): string | null {
  if (outcome === "skipped_no_reliable_entry") return "未找到可操作的退订入口。";
  if (outcome === "skipped_login_required") return "退订需要登录验证。";
  return null;
}

export function unsubscribeStateLabel(state: {status: string; outcome?: string | null} | null | undefined): {text: string; tone: "success" | "failure" | "pending"; reason: string | null} {
  if (!state) return {text: "退订状态未知", tone: "pending", reason: null};
  const event = {kind: "unsubscribe", operation: "unsubscribe", status: state.status, outcome: state.outcome ?? undefined};
  const success = ["done", "already_unsubscribed"].includes(event.outcome || "");
  const failed = event.status === "failed" || !!event.outcome?.startsWith("failed");
  return {text: actionResultLabel(event), tone: success ? "success" : failed ? "failure" : "pending", reason: unsubscribeReason(event.outcome)};
}

function UnsubscribeEvidence({ event, classificationId, entry }: { event: EmailObservabilityEvent; classificationId: string; entry?: {available: boolean; reason: string | null} }) {
  const [entryUrl, setEntryUrl] = useState("");
  const [loading, setLoading] = useState(entry?.available !== false);
  const [error, setError] = useState("");
  const [copyState, setCopyState] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (entry?.available === false) { setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true); setError("");
    getEmailUnsubscribeEntryUrl(classificationId, controller.signal)
      .then(url => { if (!controller.signal.aborted) { setEntryUrl(url); setLoading(false); } })
      .catch(() => { if (!controller.signal.aborted) { setError("退订地址暂不可用。"); setLoading(false); } });
    return () => controller.abort();
  }, [classificationId, entry?.available, attempt]);
  async function copy() {
    try {
      if (!navigator.clipboard) throw new Error("unavailable");
      await navigator.clipboard.writeText(entryUrl);setCopyState("已复制");
    } catch {setCopyState("复制失败，请选择地址手动复制");}
  }
  return <>
    {!!event.attempt_ids?.length && <div className="email-unsubscribe-attempts">{event.attempt_ids.map(id => <Link key={id} to={`/attempts/${id}`}>查看处理过程 · Attempt #{id} ↗</Link>)}</div>}
    <div className="email-unsubscribe-entry"><span>退订链接</span>
      {entry?.available === false ? <p>没有保存退订地址：当前记录没有可验证的退订地址。</p>
        : loading ? <p role="status">正在读取退订链接…</p>
        : error ? <p role="alert">{error} <button type="button" onClick={() => setAttempt(value => value + 1)}>重试</button></p>
        : <><code className="email-unsubscribe-url" aria-label="退订入口地址">{entryUrl}</code><button type="button" onClick={()=>void copy()}>复制地址</button></>}
      {copyState && <p role="status">{copyState}</p>}
    </div>
  </>;
}

function pageText(value: string) {
  return value.replace(/\u00a0/g, " ").replace(/[ \t]+\n/g, "\n").replace(/\n{2,}/g, "\n").trim();
}

function eventTime(event: EmailObservabilityEvent) {
  return event.completed_at || event.finished_at || event.created_at || "";
}

/** One "label  value" line of technical evidence; long identifiers keep their full text in the tooltip. */
function Fact({ name, children }: { name: string; children: string | number | undefined | null }) {
  if (children === undefined || children === null || children === "") return null;
  return <><dt>{name}</dt><dd title={String(children)}>{children}</dd></>;
}

export function ObservabilityDetails({ events, classificationId, entry }: { events: EmailObservabilityEvent[]; classificationId: string; entry?: {available: boolean; reason: string | null} }) {
  if (!events.length) return <p className="email-reading-empty">暂无外部处理记录。</p>;
  const newestFirst = [...events].sort((left, right) => eventTime(right).localeCompare(eventTime(left)));
  return <div className="email-observability-list">{newestFirst.map((event,index) => {
    const recordedAt=eventTime(event);
    const success=event.kind === "unsubscribe" ? ["done", "already_unsubscribed"].includes(event.outcome || "") : ["done","succeeded"].includes(event.status);
    const failed=event.status === "failed" || event.outcome?.startsWith("failed");
    const reason = event.kind === "unsubscribe" ? (unsubscribeReason(event.outcome) ?? (event.outcome === "done" ? "已记录退订成功结果。" : event.status === "done" && !event.outcome ? "历史记录未提供明确的退订结果。" : null)) : null;
    const text = event.result_text ? pageText(event.result_text) : "";
    return <article className="email-observability-item" key={event.action_identity || event.action_id || index}>
      <div className="email-event-heading"><span className={success ? "email-event-icon success" : failed ? "email-event-icon failure" : "email-event-icon"}>{success ? "✓" : failed ? "!" : "—"}</span><h3>{actionResultLabel(event)}</h3>{recordedAt && <time>{localTime(recordedAt)}</time>}</div>
      <div className="email-event-body">
        {reason && <p className="email-event-reason">{reason}</p>}
        {event.kind === "unsubscribe" && <UnsubscribeEvidence event={event} classificationId={classificationId} entry={entry}/>}
        {event.error && <p role="alert" className="email-event-error">{event.error}</p>}
        {event.summary && <p className="email-event-reason">{event.summary}</p>}
        {text && <pre className="email-page-text" aria-label="页面原文">{text}</pre>}
        <dl className="email-kv">
          <Fact name="生命周期">{event.lifecycle_version}</Fact>
          <Fact name="退订结果">{event.outcome}</Fact>
          <Fact name="Task">{event.task_id ? `${event.task_id} · ${event.task_status}` : undefined}</Fact>
          <Fact name="Consumer">{event.consumer_run_ids?.length ? `Consumer run：${event.consumer_run_ids.join("、")}` : undefined}</Fact>
          <Fact name="Audit">{event.audit_run_ids?.length ? `Audit run：${event.audit_run_ids.join("、")}` : undefined}</Fact>
          <Fact name="结果页证据">{event.evidence}</Fact>
          <Fact name="Receipt">{event.receipt_id}</Fact>
          <Fact name="观察摘要">{event.observation_digest}</Fact>
          <Fact name="Provider 结果">{event.provider_result_id}</Fact>
        </dl>
        {!!event.steps?.length && <ol className="email-steps">{event.steps.map(step => <li key={step.sequence}>{step.operation}：{step.state}</li>)}</ol>}
      </div>
    </article>;
  })}</div>;
}
