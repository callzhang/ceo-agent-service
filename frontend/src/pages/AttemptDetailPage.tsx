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

function ReviewBlock({ title, value, className = "", lines = 5 }: { title: string; value: unknown; className?: string; lines?: number }) {
  return <section className={`attempt-review-block ${className}`}><h2>{title}</h2><SummaryText value={displayValue(value)} lines={lines} /></section>;
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
  const rows = events.length ? events : [{ rating: "", rating_label: "", rating_stars: "", comment: "", source: "", received_at: "" }];
  return <section className="feedback-iteration-section"><h3>对方反馈</h3><div className="attempt-feedback-event-list">{rows.map((event, index) => <article key={`${event.received_at}-${index}`}><dl className="feedback-iteration-values"><div><dt>对方反馈（1-5星）</dt><dd>{event.rating_stars || event.rating_label || "未提供"}</dd></div><div><dt>对方反馈内容</dt><dd className="feedback-iteration-text">{event.comment || ""}</dd></div></dl>{(event.source || event.received_at) && <div className="attempt-feedback-event-meta">{event.source && <span>来源：{event.source}</span>}{event.received_at && <time>{event.received_at}</time>}</div>}</article>)}</div></section>;
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
  return <section className="console-card attempt-feedback-card"><h2>反馈迭代</h2><form onSubmit={submit}><section className="feedback-iteration-section"><h3>内部反馈</h3><label htmlFor="attempt-feedback">内部反馈意见</label><textarea id="attempt-feedback" className="feedback-iteration-input" rows={1} value={feedback} onChange={(event) => setFeedback(event.target.value)} placeholder="这条判断哪里不对、为什么不满意、以后应该遵守什么规则" /><label htmlFor="attempt-corrected-reply">建议回复</label><textarea id="attempt-corrected-reply" className="feedback-iteration-input" rows={1} value={correctedReply} onChange={(event) => setCorrectedReply(event.target.value)} placeholder="如果重写，这条消息应该怎么回复" /></section><FeedbackEvents events={detail.feedback.events} /><button type="submit" disabled={saving}>{saving ? "保存中…" : "保存反馈"}</button></form></section>;
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
      const refreshed = await getAttemptDetail(attemptId);
      setDetail(refreshed.item);
      setSnapshot(refreshed.meta.snapshot_at);
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

  return <ConsolePageLayout title={detail ? `Attempt #${detail.id}` : "Attempt"} showHeader={false} suppressHiddenTitle>
    <header className="attempt-title-row" data-testid="attempt-title-row"><div className="attempt-title-leading"><Link className="secondary-button" to="/history">返回 History</Link><div><p className="eyebrow">CEO AGENT CONSOLE</p><h1 id="console-page-title">{detail ? `Attempt #${detail.id}` : "Attempt"}</h1></div></div><div className="attempt-title-actions"><SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} />{detail?.actions.wechat_open_url && <button type="button" className="secondary-button" onClick={() => void runAction(detail.actions.wechat_open_url || "", "已打开微信消息")}>查看微信消息</button>}{detail?.actions.delivery_action_url && <button type="button" className="primary-button" onClick={() => { if (window.confirm(detail.actions.delivery_action_label === "发送" ? "确认发送这条微信回复？" : "确认重新尝试发送这条微信回复？")) void runAction(detail.actions.delivery_action_url || "", `${detail.actions.delivery_action_label}已提交`); }}>{detail.actions.delivery_action_label}</button>}</div></header>
    {state === "loading" && !detail && <section className="console-card page-state" role="status">正在加载…</section>}
    {state === "error" && <section className="console-card page-state page-state-error" role="alert">{message}</section>}
    {detail && <>
      <section className="console-card compact-card attempt-conversation-banner"><div className="attempt-conversation-left"><div className="attempt-conversation-label">{detail.conversation.label}</div><div className="attempt-conversation-main"><div className="attempt-conversation-title">{detail.conversation.title}</div><div className="attempt-conversation-sub">触发人：{detail.conversation.trigger_sender || "未提供"}</div></div></div><div className="attempt-banner-actions">{detail.actions.consumer_url && <Link className="agent-log-button" to={detail.actions.consumer_url}>查看 Consumer 记录</Link>}{detail.actions.audit_url && <Link className="agent-log-button" to={detail.actions.audit_url}>查看执行审计</Link>}{!detail.agent_execution_record && <span className="muted">No agent execution record</span>}{detail.actions.terminal && <span className="disabled-action">无需操作</span>}{detail.actions.dingtalk_url && <a className="compact-button open-dingtalk-action" href={detail.actions.dingtalk_url} target="_blank" rel="noreferrer">{detail.oa.url ? "查看审批" : "查看钉钉消息"}</a>}</div></section>
      {((detail.status.attention.reason || ["sent", "skipped", "needs_human", "failed"].includes(detail.status.raw.trim().toLowerCase()))) && <section className="console-card compact-card attempt-status-card"><p><strong>事项：</strong>{detail.status.subject}</p><p><strong>当前状态：</strong>{detail.status.message}</p><p><strong>需要你决策：</strong>{detail.status.requires_decision ? "是" : "否"}</p>{detail.status.attention.reason && <dl className="attempt-attention-details"><div><dt>原因</dt><dd>{detail.status.attention.reason}</dd></div><div><dt>外部副作用</dt><dd>{detail.status.attention.external_effect}</dd></div>{detail.status.attention.retry_at && <div><dt>重试计划</dt><dd>{detail.status.attention.retry_at}</dd></div>}</dl>}</section>}
      <MetadataGrid rows={[...detail.metadata, ...(detail.revision_count ? [{ label: "revisions", value: `${detail.revision_count} revisions` }] : [])]} />
      <section className="attempt-review-grid"><div className="console-card attempt-review-main"><div className="reply-meta" aria-label="处理状态">{detail.action_pills.map((pill) => <StatusBadge key={`${pill.label}-${pill.status}`} value={pill.status} />)}</div><ReviewBlock title={detail.trigger.title} value={detail.trigger.text} /><ReviewBlock title={detail.audit_explanation.title} value={detail.audit_explanation.text} className="attempt-audit-section" /><ReviewBlock title={detail.generated_reply.title} value={detail.generated_reply.text} className="attempt-generated-reply" /></div><aside className="attempt-review-side" aria-label="反馈与人工处理">{detail.status.requires_decision && <section className="console-card attempt-decision-card"><h2>需要你的判断</h2><p>请选择一条处理规则。提交后会生成新的处理记录，原始 Attempt 保留。</p>{detail.decision_options.map((option, index) => <button className="attempt-decision-option" type="button" key={option.instruction} onClick={() => { if (window.confirm(`确认选择“${option.label}”？`)) void runDecision(option.url, option.instruction); }}><strong>{index + 1}. {option.label}</strong><span>{option.consequence}</span></button>)}<label htmlFor="attempt-custom-decision">其他处理指令（默认仅本次）</label><label className="attempt-skill-toggle" htmlFor="attempt-skill-update"><input id="attempt-skill-update" type="checkbox" checked={skillUpdateRequested} onChange={(event) => setSkillUpdateRequested(event.target.checked)} /> 同时把这条反馈沉淀为 Skill 规则</label><textarea id="attempt-custom-decision" placeholder="例如：采用方案二，并说明交付边界" onChange={(event) => setCustomDecision(event.target.value)} /><button type="button" className="primary-button" disabled={!customDecision.trim()} onClick={() => { if (window.confirm("确认提交这条人工处理指令？")) void runDecision(detail.decision_options[0]?.url || `/api/console/history/${detail.id}/human-decision`, customDecision.trim()); }}>执行并发布</button></section>}<FeedbackPanel detail={detail} onSaved={setMessage} /></aside></section>
      {detail.failure_reason && <DetailSection title="失败原因" value={detail.failure_reason} />}
      {detail.recovery_state && <DetailSection title="后续路由恢复" value="关联任务已完成；原始失败记录仍保留，后续处理没有重写该审计事实。" />}
      {(detail.oa.process_instance_id || detail.oa.task_id || detail.oa.action || detail.oa.remark) && <DetailSection title="OA 信息" value={[detail.oa.process_instance_id, detail.oa.task_id, detail.oa.action, detail.oa.remark].filter(Boolean).join("\n")} />}
      {(detail.calendar.event_id || detail.calendar.response_status) && <DetailSection title="日历信息" value={[detail.calendar.event_id, detail.calendar.response_status, displayValue(detail.calendar.result)].filter(Boolean).join("\n")} />}
      {detail.quality_warnings.length > 0 && <section className="console-card attempt-quality-warning"><h2>Audit quality warnings</h2><ul>{detail.quality_warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></section>}
      {detail.context_only_info && <DetailSection title="Audit context" value={detail.context_only_info} />}
      {!detail.agent_execution_record && <DetailSection title="Tool uses" value={detail.tool_uses} />}
      {detail.runtime_attempts.length > 0 && <details className="console-card attempt-runtime-card"><summary><h2>Runtime attempts</h2><span>{detail.runtime_attempts.length} 条执行记录</span></summary><div className="attempt-runtime-list">{detail.runtime_attempts.map((entry, index) => <RuntimeEntry entry={entry} key={`${entry.route}-${index}`} />)}</div></details>}
      {message && <p className="attempt-action-message" role="status" aria-live="polite">{message}</p>}
      {(detail.actions.can_rerun || detail.actions.can_recall) && <div className="attempt-bottom-actions">{detail.actions.can_rerun && <button type="button" className="danger-button" onClick={() => { if (window.confirm("确认重新处理这条 Attempt？")) void runAction(detail.actions.rerun_url, "重跑已提交"); }}>重新处理</button>}{detail.actions.can_recall && <button type="button" className="danger-button" onClick={() => { if (window.confirm("确认撤回已发送消息？")) void runAction(detail.actions.recall_url, "撤回已提交"); }}>撤回发送</button>}</div>}
    </>}
  </ConsolePageLayout>;
}
