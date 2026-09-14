import { Link } from "react-router-dom";
import { useState } from "react";
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


function observabilityLabel(event: EmailObservabilityEvent) {
  if (event.kind === "unsubscribe") return "自动退订";
  if (event.kind === "auto_reply") return "自动回复";
  return event.operation || "邮箱动作";
}

function UnsubscribeEvidence({ event, classificationId }: { event: EmailObservabilityEvent; classificationId: string }) {
  const [entryUrl, setEntryUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  async function reveal() {
    if (loading || entryUrl) return;
    setLoading(true);
    setError("");
    try {
      setEntryUrl(await getEmailUnsubscribeEntryUrl(classificationId, new AbortController().signal));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "退订入口暂不可用");
    } finally {
      setLoading(false);
    }
  }
  async function copy() {
    await navigator.clipboard?.writeText(entryUrl);
  }
  return <>
    {!!event.attempt_ids?.length && <div className="email-unsubscribe-attempts"><strong>处理记录</strong>{event.attempt_ids.map(id => <Link key={id} to={`/attempts/${id}`}>打开 Attempt #{id}</Link>)}</div>}
    <div className="email-unsubscribe-entry">
      <strong>退订入口</strong>
      {!entryUrl ? <button type="button" onClick={()=>void reveal()} disabled={loading}>{loading ? "正在读取地址…" : "显示完整地址"}</button> : <><input aria-label="退订入口地址" readOnly value={entryUrl}/><button type="button" onClick={()=>void copy()}>复制地址</button></>}
      {error && <p role="alert">{error}</p>}
    </div>
  </>;
}

export function ObservabilityDetails({ events, classificationId }: { events: EmailObservabilityEvent[]; classificationId: string }) {
  if (!events.length) return <p className="muted">暂无外部处理记录。</p>;
  return <div className="email-observability-list">
    {events.map((event, index) => {
      const recordedAt = event.completed_at || event.finished_at || event.created_at || "";
      return <article className="email-observability-item" key={`${event.kind}-${event.action_id || event.action_identity || index}`}>
      <div className="card-head"><div><h3>{observabilityLabel(event)}</h3><p className="muted">状态：{event.status}{recordedAt && <>；记录时间：{localTime(recordedAt)}</>}</p></div></div>
      {event.kind === "unsubscribe" ? <>
        {event.lifecycle_version === "email_unsubscribe_audited_v2" && <p><strong>Consumer → Audit</strong></p>}
        {event.result_text && <p className="email-observability-result">{event.result_text}</p>}
        <dl className="email-observability-meta">
          {event.lifecycle_version && <><dt>生命周期</dt><dd>{event.lifecycle_version}</dd></>}
          {event.task_id && <><dt>Task</dt><dd>Task {event.task_id} · {event.task_status || "未提供"}</dd></>}
          {!!event.consumer_run_ids?.length && <><dt>Consumer run</dt><dd>Consumer run：{event.consumer_run_ids.join("、")}</dd></>}
          {!!event.audit_run_ids?.length && <><dt>Audit run</dt><dd>Audit run：{event.audit_run_ids.join("、")}</dd></>}
          {event.evidence && <><dt>最终结果页证据</dt><dd>{event.evidence}</dd></>}
          {event.receipt_id && <><dt>Receipt</dt><dd>{event.receipt_id}</dd></>}
          {event.observation_digest && <><dt>观察摘要</dt><dd>{event.observation_digest}</dd></>}
        </dl>
        <UnsubscribeEvidence event={event} classificationId={classificationId}/>
        {!!event.steps?.length && <div><h4>退订步骤</h4><ol>{event.steps.map((step) => <li key={`${step.sequence}-${step.reference}`}>{step.operation}：{step.state}（{step.reference}）</li>)}</ol></div>}
      </> : <>
        {event.summary && <p>{event.summary}</p>}
        {event.provider_result_id && <p className="muted">Provider 结果：{event.provider_result_id}</p>}
        {event.error && <p className="page-state page-state-error">{event.error}</p>}
      </>}
    </article>})}
  </div>;
}

