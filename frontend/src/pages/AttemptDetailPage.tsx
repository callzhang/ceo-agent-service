import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import { command, displayValue } from "../api/console";
import { getAttemptDetail, type AttemptDetail, type AttemptMetadata, type AttemptRuntimeEntry } from "../api/attempts";
import { SummaryText } from "../components/data/SummaryText";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";
import { StatusBadge } from "../components/status/StatusBadge";

function DetailSection({ title, value, className = "" }: { title: string; value: unknown; className?: string }) {
  return <section className={`console-card attempt-detail-section ${className}`}><h2>{title}</h2><SummaryText value={displayValue(value)} lines={5} /></section>;
}

function MetadataGrid({ rows }: { rows: AttemptMetadata[] }) {
  return <section className="console-card attempt-metadata-card"><div className="attempt-metadata-grid">{rows.map((row) => <div className="attempt-metadata-item" key={row.label}><span>{row.label}</span><strong>{row.value || "未记录"}</strong></div>)}</div></section>;
}

function RuntimeEntry({ entry }: { entry: AttemptRuntimeEntry }) {
  return <article className="attempt-runtime-entry"><div className="attempt-runtime-heading"><strong>{entry.route || "Runtime"}</strong><StatusBadge value={entry.status} /></div><dl className="attempt-runtime-grid">
    <div><dt>Runtime</dt><dd>{entry.runtime || "未记录"}</dd></div>
    <div><dt>Credential mode</dt><dd>{entry.credential_mode || "未记录"}</dd></div>
    <div><dt>Model</dt><dd>{entry.model || "未记录"}</dd></div>
    <div><dt>Session</dt><dd>{entry.session_available ? "已关联会话（标识已隐藏）" : "未关联"}</dd></div>
    <div><dt>Failure code</dt><dd>{entry.failure_code || "未记录"}</dd></div>
    <div><dt>Failover permitted</dt><dd>{entry.failover_permitted ? "yes" : "no"}</dd></div>
    <div><dt>Transcript lines</dt><dd>{entry.transcript_start || entry.transcript_end ? `${entry.transcript_start}-${entry.transcript_end}` : "未记录"}</dd></div>
    <div><dt>Effect started</dt><dd>{entry.effect_started_at || "未记录"}</dd></div>
  </dl></article>;
}

function FeedbackEvents({ events }: { events: AttemptDetail["feedback"]["events"] }) {
  if (!events.length) return null;
  return <section className="console-card attempt-feedback-events"><h2>对方反馈</h2><div className="attempt-feedback-event-list">{events.map((event, index) => <article key={`${event.received_at}-${index}`}><div className="attempt-feedback-event-head"><strong>{event.rating}</strong><time>{event.received_at || "未记录时间"}</time></div><p>{event.comment || "未填写评语"}</p>{event.source && <small>来源：{event.source}</small>}</article>)}</div></section>;
}

function FeedbackPanel({ detail, onSaved }: { detail: AttemptDetail; onSaved: (message: string) => void }) {
  const [feedback, setFeedback] = useState(detail.feedback.reviewer_feedback === "未提供" ? "" : detail.feedback.reviewer_feedback);
  const [correctedReply, setCorrectedReply] = useState(detail.feedback.corrected_reply === "未提供" ? "" : detail.feedback.corrected_reply);
  const [saving, setSaving] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      const result = await command(detail.feedback.feedback_url, { feedback, corrected_reply: correctedReply });
      onSaved(result.message);
    } catch (error) {
      onSaved(error instanceof Error ? error.message : "反馈保存失败");
    } finally {
      setSaving(false);
    }
  };
  return <section className="console-card attempt-feedback-card"><h2>内部反馈/建议修改</h2><form onSubmit={submit}><label htmlFor="attempt-feedback">反馈意见</label><textarea id="attempt-feedback" value={feedback} onChange={(event) => setFeedback(event.target.value)} placeholder="这条判断哪里不对、为什么不满意、以后应该遵守什么规则" /><label htmlFor="attempt-corrected-reply">建议回复</label><textarea id="attempt-corrected-reply" value={correctedReply} onChange={(event) => setCorrectedReply(event.target.value)} placeholder="如果重写，这条消息应该怎么回复" /><button type="submit" disabled={saving}>{saving ? "保存中…" : "保存反馈"}</button></form></section>;
}

export function AttemptDetailPage() {
  const { attemptId = "" } = useParams();
  const [detail, setDetail] = useState<AttemptDetail | null>(null);
  const [snapshot, setSnapshot] = useState("");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("");
  const [customDecision, setCustomDecision] = useState("");
  const [skillUpdateRequested, setSkillUpdateRequested] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    getAttemptDetail(attemptId, controller.signal).then((response) => {
      setDetail(response.item);
      setSnapshot(response.meta.snapshot_at);
      setState("ready");
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      setMessage(error instanceof Error ? error.message : "Attempt 加载失败");
      setState("error");
    });
    return () => controller.abort();
  }, [attemptId]);

  const runAction = async (url: string, success: string) => {
    setMessage("操作进行中…");
    try {
      const result = await command(url);
      setMessage(result.message || success);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "操作失败");
    }
  };

  const runDecision = async (url: string, instruction: string) => {
    setMessage("正在提交人工决策…");
    try {
      const result = await command(url, { instruction, feedback_scope: "one_time", skill_update_requested: skillUpdateRequested });
      setMessage(result.message || "人工决策已提交");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "人工决策提交失败");
    }
  };

  return <ConsolePageLayout title={detail ? `Attempt #${detail.id}` : "Attempt"} actions={<><SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} /><Link className="secondary-button" to="/history">返回 History</Link></>}>
    {state === "loading" && !detail && <section className="console-card page-state" role="status">正在加载…</section>}
    {state === "error" && <section className="console-card page-state page-state-error" role="alert">{message}</section>}
    {detail && <>
      <section className="console-card attempt-conversation-card"><div><p className="attempt-context-label">{detail.conversation.label}</p><h2>{detail.conversation.title}</h2><p className="attempt-context-sub">触发人：{detail.conversation.trigger_sender || "未提供"}</p></div><div className="attempt-header-actions">{detail.actions.consumer_url && <Link className="primary-button" to={detail.actions.consumer_url}>查看 Consumer 记录</Link>}{detail.actions.audit_url && <Link className="primary-button" to={detail.actions.audit_url}>查看执行审计</Link>}{!detail.agent_execution_record && <span className="muted">No agent execution record</span>}{detail.actions.terminal && <span className="muted">无需操作</span>}{detail.actions.dingtalk_url && <a className="secondary-button" href={detail.actions.dingtalk_url} target="_blank" rel="noreferrer">{detail.oa.url ? "查看审批" : "查看钉钉消息"}</a>}</div></section>
      <section className="console-card attempt-status-card"><div className="attempt-status-heading"><StatusBadge value={detail.status.raw} /><span>{detail.actions.action_label}</span></div><p><strong>事项：</strong>{detail.status.subject}</p><p>{detail.status.message}</p>{detail.action_pills.length > 0 && <div className="attempt-action-pills" aria-label="处理状态">{detail.action_pills.map((pill) => <StatusBadge key={`${pill.label}-${pill.status}`} value={pill.status} />)}</div>}{detail.status.attention.reason && <dl className="attempt-attention-details"><div><dt>原因</dt><dd>{detail.status.attention.reason}</dd></div><div><dt>外部副作用</dt><dd>{detail.status.attention.external_effect}</dd></div>{detail.status.attention.retry_at && <div><dt>重试计划</dt><dd>{detail.status.attention.retry_at}</dd></div>}</dl>}</section>
      <MetadataGrid rows={[...detail.metadata, ...(detail.revision_count ? [{ label: "revisions", value: `${detail.revision_count} revisions` }] : [])]} />
      <div className="attempt-primary-grid"><div><DetailSection title={detail.trigger.title} value={detail.trigger.text} /><DetailSection title={detail.audit_explanation.title} value={detail.audit_explanation.text} className="attempt-audit-section" /><DetailSection title={detail.generated_reply.title} value={detail.generated_reply.text} /></div><FeedbackPanel detail={detail} onSaved={setMessage} /></div>
      {detail.status.requires_decision && <section className="console-card attempt-decision-card"><h2>需要你的判断</h2><p>请选择一条处理规则。提交后会生成新的处理记录，原始 Attempt 保留。</p>{detail.decision_options.map((option, index) => <button className="attempt-decision-option" type="button" key={option.instruction} onClick={() => { if (window.confirm(`确认选择“${option.label}”？`)) void runDecision(option.url, option.instruction); }}><strong>{index + 1}. {option.label}</strong><span>{option.consequence}</span></button>)}<label htmlFor="attempt-custom-decision">其他处理指令（默认仅本次）</label><label className="attempt-skill-toggle" htmlFor="attempt-skill-update"><input id="attempt-skill-update" type="checkbox" checked={skillUpdateRequested} onChange={(event) => setSkillUpdateRequested(event.target.checked)} /> 同时把这条反馈沉淀为 Skill 规则</label><textarea id="attempt-custom-decision" placeholder="例如：采用方案二，并说明交付边界" onChange={(event) => setCustomDecision(event.target.value)} /><button type="button" className="primary-button" disabled={!customDecision.trim()} onClick={() => { if (window.confirm("确认提交这条人工处理指令？")) void runDecision(detail.decision_options[0]?.url || `/api/console/history/${detail.id}/human-decision`, customDecision.trim()); }}>执行并发布</button></section>}
      {detail.failure_reason && <DetailSection title="失败原因" value={detail.failure_reason} />}
      {detail.recovery_state && <DetailSection title="后续路由恢复" value="关联任务已完成；原始失败记录仍保留，后续处理没有重写该审计事实。" />}
      {(detail.oa.process_instance_id || detail.oa.task_id || detail.oa.action || detail.oa.remark) && <DetailSection title="OA 信息" value={[detail.oa.process_instance_id, detail.oa.task_id, detail.oa.action, detail.oa.remark].filter(Boolean).join("\n")} />}
      {(detail.calendar.event_id || detail.calendar.response_status) && <DetailSection title="日历信息" value={[detail.calendar.event_id, detail.calendar.response_status, displayValue(detail.calendar.result)].filter(Boolean).join("\n")} />}
      {detail.quality_warnings.length > 0 && <section className="console-card attempt-quality-warning"><h2>Audit quality warnings</h2><ul>{detail.quality_warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></section>}
      {detail.context_only_info && <DetailSection title="Audit context" value={detail.context_only_info} />}
      <DetailSection title="Audit summary" value={detail.audit_summary} />
      {!detail.agent_execution_record && <DetailSection title="Tool uses" value={detail.tool_uses} />}
      <DetailSection title="Draft reply (raw Codex reply)" value={detail.draft_reply} />
      <FeedbackEvents events={detail.feedback.events} />
      {detail.runtime_attempts.length > 0 && <section className="console-card attempt-runtime-card"><h2>Runtime attempts</h2><div className="attempt-runtime-list">{detail.runtime_attempts.map((entry, index) => <RuntimeEntry entry={entry} key={`${entry.route}-${index}`} />)}</div></section>}
      {message && <p className="attempt-action-message" role="status" aria-live="polite">{message}</p>}
      <div className="attempt-bottom-actions">{detail.actions.can_rerun && <button type="button" className="danger-button" onClick={() => { if (window.confirm("确认重新处理这条 Attempt？")) void runAction(detail.actions.rerun_url, "重跑已提交"); }}>重新处理</button>}{detail.actions.can_recall && <button type="button" className="danger-button" onClick={() => { if (window.confirm("确认撤回已发送消息？")) void runAction(detail.actions.recall_url, "撤回已提交"); }}>撤回发送</button>}</div>
    </>}
  </ConsolePageLayout>;
}
