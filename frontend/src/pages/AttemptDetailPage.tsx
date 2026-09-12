import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import { command, displayValue } from "../api/console";
import { getAttemptDetail, type AttemptAgentSession, type AttemptDetail, type AttemptEmail, type AttemptMetadata, type AttemptRuntimeEntry, type AttemptToolUse } from "../api/attempts";
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

function ToolUseList({ uses }: { uses: AttemptToolUse[] }) {
  if (!uses.length) return <p className="page-state">这一段没有留下可读的调用记录。</p>;
  return <ol className="attempt-tool-use-list">{uses.map((use, index) => <li key={`${use.call_id}-${index}`}><article className="attempt-tool-use"><header><strong>{use.title || use.tool || "未命名调用"}</strong>{use.source && <small>{use.source}</small>}</header>{use.relevance && <p className="attempt-tool-use-relevance">{use.relevance}</p>}<dl><div><dt>参数</dt><dd><SummaryText value={displayValue(use.args)} lines={4} /></dd></div><div><dt>结果</dt><dd><SummaryText value={displayValue(use.output)} lines={6} /></dd></div></dl></article></li>)}</ol>;
}

function RecordedCalls({ sessions, toolUses }: { sessions: AttemptAgentSession[]; toolUses: AttemptToolUse[] }) {
  // Only for an Attempt whose Codex transcript is gone. When the record
  // exists it is the one home for the process - it carries these calls with
  // their inputs and outputs, plus the reasoning and messages around them -
  // so repeating a thinner copy here only split the evidence in two.
  if (sessions.length || !toolUses.length) return null;
  return <section className="console-card attempt-process-card"><h2>执行过程</h2><p>这次处理调用的工具、参数和返回结果。本次执行的 Agent 记录已不在本机，以下是事项自身留存的调用。</p><ToolUseList uses={toolUses} /></section>;
}

function EmailContext({ email }: { email: AttemptEmail | null }) {
  if (!email) return null;
  const receipt = email.unsubscribe;
  const rows = [
    ["主题", email.subject],
    ["发件人", email.sender],
    ["收件时间", email.received_at],
    ["所在文件夹", email.folder],
    ["邮箱账号", email.account_id],
    ["Message-ID", email.rfc_message_id],
    ["动作", email.action_type],
    ["分类", email.category],
    ["退订入口来源", email.candidate_source],
  ].filter(([, value]) => value);
  return <section className="console-card attempt-email-card"><div className="execution-detail-list-header"><div><h2>关联邮件</h2><p>这条 Attempt 处理的邮件，以及它为这封邮件留下的回执。</p></div>{email.classification_url && <Link className="agent-log-button" to={email.classification_url}>打开这封邮件</Link>}</div><dl className="attempt-email-grid">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>{receipt && <div className="attempt-email-receipt"><h3>退订回执</h3><dl className="attempt-email-grid"><div><dt>结果</dt><dd>{receipt.outcome}</dd></div><div><dt>依据</dt><dd>{receipt.evidence}</dd></div><div><dt>开始</dt><dd>{receipt.started_at}</dd></div><div><dt>结束</dt><dd>{receipt.completed_at}</dd></div></dl>{receipt.entry_url && <dl className="attempt-email-entry"><div><dt>退订入口（打开会真实执行退订）</dt><dd><a href={receipt.entry_url} target="_blank" rel="noreferrer noopener">{receipt.entry_url}</a></dd></div></dl>}{receipt.result_text && <p className="attempt-email-observation">{receipt.result_text}</p>}{receipt.steps.length > 0 && <ol className="attempt-email-steps">{receipt.steps.map((step) => <li key={step.sequence}><strong>{step.operation}</strong><span>{step.state}</span></li>)}</ol>}</div>}</section>;
}

function RuntimeEntry({ entry }: { entry: AttemptRuntimeEntry }) {
  const isAudit = entry.role === "audit";
  const phase = isAudit ? "审计核验" : entry.role === "consumer" ? "处理判断" : "系统处理";
  const description = isAudit ? "核验方案的事实、边界和对外动作；必要时会要求下一轮修订。" : "根据当前消息和已知上下文形成处理方案。";
  const retry = entry.turn_attempt > 0 ? ` · 第 ${entry.turn_attempt + 1} 次尝试` : "";
  return <article className="attempt-runtime-entry"><div className="attempt-runtime-heading"><div><strong>{phase} · 第 {entry.proposal_revision + 1} 轮{retry}</strong><p>{description}</p></div><StatusBadge value={entry.status} /></div>{(entry.failure_code || entry.effect_started_at) && <dl className="attempt-runtime-grid">{entry.failure_code && <div><dt>结果说明</dt><dd>{entry.failure_code}</dd></div>}{entry.effect_started_at && <div><dt>开始外部动作</dt><dd>{entry.effect_started_at}</dd></div>}</dl>}{entry.session_url && <Link className="agent-log-button" to={entry.session_url}>查看这一步的 Agent 记录</Link>}</article>;
}

function ExecutionDetail({ detail, role, snapshot }: { detail: AttemptDetail; role: "consumer" | "audit"; snapshot: string }) {
  const isAudit = role === "audit";
  const roleLabel = isAudit ? "Audit" : "Consumer";
  const title = isAudit ? "审计过程" : "处理过程";
  const explanation = isAudit
    ? "这里只展示审计 Agent 对方案、边界和对外动作的核验记录。"
    : "这里只展示处理 Agent 形成方案的记录。";
  const entries = detail.runtime_attempts.filter((entry) => entry.role === role);
  const session = detail.agent_sessions.find((item) => item.role === role);

  return <ConsolePageLayout title={`${title} · ${roleLabel}`} actions={<><SnapshotBadge timestamp={snapshot} /><Link className="secondary-button" to={`/attempts/${detail.id}`}>返回 Attempt</Link></>}>
    <section className="console-card execution-detail-overview">
      <div><p className="eyebrow">ATTEMPT #{detail.id}</p><h2>执行概览</h2><p>{explanation}</p></div>
      <StatusBadge value={detail.status.raw} />
    </section>
    <section className="console-card execution-detail-context">
      <span>{detail.conversation.label}：<strong>{detail.conversation.title || "未记录"}</strong></span>
      <span>触发人：<strong>{detail.conversation.trigger_sender || "未提供"}</strong></span>
    </section>
    <section className="console-card attempt-process-card" aria-label={`${roleLabel} 调用记录`}>
      <div className="execution-detail-list-header"><div><h2>调用记录</h2><p>这一个角色调用的工具、参数和返回结果，连同它当时的推理与输出，都在这次执行的 Agent 记录里。</p></div>{session && <Link className="agent-log-button" to={session.url}>查看 {session.label} 的 Agent 记录</Link>}</div>
      {!session && <p className="page-state">本次执行的 Agent 记录已不在本机。</p>}
    </section>
    <section className="console-card execution-detail-list" aria-label={`${roleLabel} 执行记录`}>
      <div className="execution-detail-list-header"><div><h2>执行步骤</h2><p>每一条代表一轮处理或一次重试；它们不会自动等同于重复发送。</p></div><span>{entries.length} 个步骤</span></div>
      {entries.length ? <div className="attempt-runtime-list">{entries.map((entry, index) => <RuntimeEntry entry={entry} key={`${entry.proposal_revision}-${entry.turn_attempt}-${index}`} />)}</div> : <p className="page-state">没有可展示的{roleLabel}执行记录。</p>}
    </section>
  </ConsolePageLayout>;
}

function References({ references }: { references: AttemptDetail["references"] }) {
  if (!references.length) return null;
  return <section className="console-card attempt-references-card"><h2>参考资料</h2><ul>{references.map((reference, index) => <li key={`${reference.title}-${reference.source}-${index}`}><strong>{reference.title || reference.source || "未命名资料"}</strong>{reference.source && reference.source !== reference.title && <span>{reference.source}</span>}{reference.relevance && <small>{reference.relevance}</small>}</li>)}</ul></section>;
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
  const { attemptId = "", role = "" } = useParams();
  const [detail, setDetail] = useState<AttemptDetail | null>(null);
  const [snapshot, setSnapshot] = useState("");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("");
  const [customDecision, setCustomDecision] = useState("");
  const [skillUpdateRequested, setSkillUpdateRequested] = useState(false);
  const [decisionSubmitting, setDecisionSubmitting] = useState(false);

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
    setDecisionSubmitting(true);
    setMessage("正在提交人工决策…");
    try {
      const result = await command(url, { instruction, feedback_scope: "one_time", skill_update_requested: skillUpdateRequested });
      const refreshed = await getAttemptDetail(attemptId);
      setDetail(refreshed.item);
      setSnapshot(refreshed.meta.snapshot_at);
      setCustomDecision("");
      setMessage(result.message || "人工决策已提交");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "人工决策提交失败");
    } finally {
      setDecisionSubmitting(false);
    }
  };

  const executionRole = role === "consumer" || role === "audit" ? role : null;
  if (detail && executionRole) return <ExecutionDetail detail={detail} role={executionRole} snapshot={snapshot} />;

  return <ConsolePageLayout title={detail ? `Attempt #${detail.id}` : "Attempt"} showHeader={false} suppressHiddenTitle>
    <header className="attempt-title-row" data-testid="attempt-title-row"><div className="attempt-title-leading"><Link className="secondary-button icon-button" to="/history" aria-label="返回 History" title="返回 History"><span aria-hidden="true">←</span></Link><div><p className="eyebrow">CEO AGENT CONSOLE</p><h1 id="console-page-title">{detail ? `Attempt #${detail.id}` : "Attempt"}</h1></div></div><div className="attempt-title-actions"><SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} />{detail?.actions.wechat_open_url && <button type="button" className="secondary-button" onClick={() => void runAction(detail.actions.wechat_open_url || "", "已打开微信消息")}>查看微信消息</button>}{detail?.actions.delivery_action_url && <button type="button" className="primary-button" onClick={() => { if (window.confirm(detail.actions.delivery_action_label === "发送" ? "确认发送这条微信回复？" : "确认重新尝试发送这条微信回复？")) void runAction(detail.actions.delivery_action_url || "", `${detail.actions.delivery_action_label}已提交`); }}>{detail.actions.delivery_action_label}</button>}</div></header>
    {state === "loading" && !detail && <section className="console-card page-state" role="status">正在加载…</section>}
    {state === "error" && <section className="console-card page-state page-state-error" role="alert">{message}</section>}
    {detail && <>
      <section className="console-card compact-card attempt-conversation-banner"><div className="attempt-conversation-left" data-testid="attempt-conversation-summary"><div className="attempt-conversation-title"><span>{detail.conversation.label}：</span><strong>{detail.conversation.title}</strong></div><div className="attempt-conversation-sub">触发人：{detail.conversation.trigger_sender || "未提供"}</div></div><div className="attempt-banner-actions" data-testid="attempt-conversation-actions">{detail.actions.consumer_url && <Link className="agent-log-button" to={detail.actions.consumer_url} title="查看 Agent 如何形成这次处理方案">查看处理过程</Link>}{detail.actions.audit_url && <Link className="agent-log-button" to={detail.actions.audit_url} title="查看 Agent 如何核验方案、边界和实际结果">查看审计过程</Link>}{detail.actions.agent_url && <Link className="agent-log-button" to={detail.actions.agent_url} title="查看关联的 Agent 会话">查看 Agent session</Link>}{!detail.agent_execution_record && <span className="muted">未记录 Agent 过程</span>}{detail.actions.terminal && <span className="disabled-action">无需操作</span>}{detail.actions.dingtalk_url && <a className="compact-button open-dingtalk-action" href={detail.actions.dingtalk_url} target="_blank" rel="noreferrer">{detail.oa.url ? "查看审批" : "查看钉钉消息"}</a>}</div></section>
      {((detail.status.attention.reason || ["sent", "skipped", "needs_human", "failed"].includes(detail.status.raw.trim().toLowerCase()))) && <section className="console-card compact-card attempt-status-card"><p><strong>事项：</strong>{detail.status.subject}</p><p><strong>当前状态：</strong>{detail.status.message}</p><p><strong>需要你决策：</strong>{detail.status.requires_decision ? "是" : "否"}</p>{detail.status.attention.reason && <dl className="attempt-attention-details"><div><dt>原因</dt><dd>{detail.status.attention.reason}</dd></div><div><dt>外部副作用</dt><dd>{detail.status.attention.external_effect}</dd></div>{detail.status.attention.retry_at && <div><dt>重试计划</dt><dd>{detail.status.attention.retry_at}</dd></div>}</dl>}</section>}
      <MetadataGrid rows={[...detail.metadata, ...(detail.revision_count ? [{ label: "revisions", value: `${detail.revision_count} revisions` }] : [])]} />
      <section className="attempt-review-grid"><div className="console-card attempt-review-main"><div className="reply-meta" aria-label="处理状态">{detail.action_pills.map((pill) => <StatusBadge key={`${pill.label}-${pill.status}`} value={pill.status} />)}</div><ReviewBlock title={detail.trigger.title} value={detail.trigger.text} /><ReviewBlock title={detail.audit_explanation.title} value={detail.audit_explanation.text} className="attempt-audit-section" /><ReviewBlock title={detail.generated_reply.title} value={detail.generated_reply.text} className="attempt-generated-reply" /></div><aside className="attempt-review-side" aria-label="反馈与人工处理">{detail.status.requires_decision && <section className="console-card attempt-decision-card"><h2>需要你的判断</h2><p>选择上方方案会创建一个新的处理修订，原始 Attempt 保留。若方案说明会产生外部动作，后续处理会按说明执行并回读。</p>{detail.decision_options.map((option, index) => <button className="attempt-decision-option" type="button" disabled={decisionSubmitting} key={option.instruction} onClick={() => { if (window.confirm(`确认选择“${option.label}”？`)) void runDecision(option.url, option.instruction); }}><strong>{index + 1}. {option.label}</strong><span>{option.consequence}</span></button>)}<label htmlFor="attempt-custom-decision">其他处理指令（默认仅本次）</label><label className="attempt-skill-toggle" htmlFor="attempt-skill-update"><input id="attempt-skill-update" type="checkbox" checked={skillUpdateRequested} onChange={(event) => setSkillUpdateRequested(event.target.checked)} /> 同时把这条反馈沉淀为 Skill 规则</label><textarea id="attempt-custom-decision" value={customDecision} placeholder="例如：采用方案二，并说明交付边界" onChange={(event) => setCustomDecision(event.target.value)} /><p className="attempt-decision-hint">请填写其他处理指令后提交；下方“反馈迭代”只保存反馈，不会执行处理。</p><button type="button" className="primary-button" disabled={!customDecision.trim() || decisionSubmitting} onClick={() => { if (window.confirm("确认提交这条人工处理指令？")) void runDecision(detail.decision_options[0]?.url || `/api/console/history/${detail.id}/human-decision`, customDecision.trim()); }}>{decisionSubmitting ? "提交中…" : "提交处理指令"}</button>{message && <p className="attempt-decision-message" role="status" aria-live="polite">{message}</p>}</section>}<FeedbackPanel detail={detail} onSaved={setMessage} /></aside></section>
      <EmailContext email={detail.email} />
      <References references={detail.references} />
      {detail.failure_reason && <DetailSection title="失败原因" value={detail.failure_reason} />}
      {detail.recovery_state && <DetailSection title="后续路由恢复" value="关联任务已完成；原始失败记录仍保留，后续处理没有重写该审计事实。" />}
      {(detail.oa.process_instance_id || detail.oa.task_id || detail.oa.action || detail.oa.remark) && <DetailSection title="OA 信息" value={[detail.oa.process_instance_id, detail.oa.task_id, detail.oa.action, detail.oa.remark].filter(Boolean).join("\n")} />}
      {(detail.calendar.event_id || detail.calendar.response_status) && <DetailSection title="日历信息" value={[detail.calendar.event_id, detail.calendar.response_status, displayValue(detail.calendar.result)].filter(Boolean).join("\n")} />}
      {detail.quality_warnings.length > 0 && <section className="console-card attempt-quality-warning"><h2>Audit quality warnings</h2><ul>{detail.quality_warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></section>}
      {detail.context_only_info && <DetailSection title="Audit context" value={detail.context_only_info} />}
      <RecordedCalls sessions={detail.agent_sessions} toolUses={detail.tool_uses} />
      {detail.runtime_attempts.length > 0 && <details className="console-card attempt-runtime-card"><summary><div><h2>处理过程</h2><p>每一轮会先由处理 Agent 形成方案，再由审计 Agent 核验。多条记录表示修订、重试或重新核验，不代表重复发送。 </p></div><span>{detail.runtime_attempts.length} 个处理步骤</span></summary><div className="attempt-runtime-list">{detail.runtime_attempts.map((entry, index) => <RuntimeEntry entry={entry} key={`${entry.role}-${entry.proposal_revision}-${entry.turn_attempt}-${index}`} />)}</div></details>}
      {message && <p className="attempt-action-message" role="status" aria-live="polite">{message}</p>}
      {(detail.actions.can_rerun || detail.actions.can_recall) && <div className="attempt-bottom-actions">{detail.actions.can_rerun && <button type="button" className="danger-button" onClick={() => { if (window.confirm("确认重新处理这条 Attempt？")) void runAction(detail.actions.rerun_url, "重跑已提交"); }}>重新处理</button>}{detail.actions.can_recall && <button type="button" className="danger-button" onClick={() => { if (window.confirm("确认撤回已发送消息？")) void runAction(detail.actions.recall_url, "撤回已提交"); }}>撤回发送</button>}</div>}
    </>}
  </ConsolePageLayout>;
}
