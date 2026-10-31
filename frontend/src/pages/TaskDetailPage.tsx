import { Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { getBusinessTaskDetail, getBusinessTaskSignalDetail, type BusinessSignalReference, type BusinessTaskSignal, getLegacyProjectDetail, sendBusinessTaskFollowUp, type BusinessTaskDetail, type LegacyProjectDetail } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";
import { CandidateAction, CitationList, DetailSection, signalSourceLink, TaskSkeleton } from "./TaskParts";
import { formatTaskTime } from "./TaskTime";
import { attentionEventLabels, commitmentLabels, dateTypeLabels, evidenceRoleLabels, isCurrentSuggestion, labelOf, relationTypeLabels, sourceTypeLabels, taskEventLabels, taskOriginLabel, taskStatusLabels } from "./taskLabels";

function parseJson(value: unknown): unknown {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return undefined;
  try { return JSON.parse(trimmed); } catch { return undefined; }
}

function nonEmpty(value: unknown): value is string { return typeof value === "string" && value !== ""; }

const legacyStatusLabels: Record<string, string> = { active: "进行中", paused: "已暂停", blocked: "受阻", completed: "已完成", done: "已完成", cancelled: "已取消" };

const eventLabels = { ...taskEventLabels, ...attentionEventLabels };

/** One evidence-style row: a readable line, and the raw record folded away when the source stored JSON. */
export function SourceRecordRows({ rows }: { rows: Array<Record<string, unknown>> }) {
  return <ol className="business-source-list">{rows.map((row, index) => {
    const signal = row.signal && typeof row.signal === "object" ? row.signal as Record<string, unknown> : null;
    const rawText = [signal?.evidence_text, row.evidence_text].find(nonEmpty);
    const sourceUnavailable = signal?.source_available === false || row.source_available === false;
    const raw = parseJson(rawText);
    const contextJson = signal?.context_json ?? row.context_json;
    const context = parseJson(contextJson) as Record<string, unknown> | undefined;
    const readable = [raw === undefined ? rawText : undefined, row.raw_phrase, row.title, row.summary].find(nonEmpty);
    const eventLabel = typeof row.event_type === "string" ? eventLabels[row.event_type] : undefined;
    const sourceType = String(signal?.source_type ?? row.source_type ?? "");
    const primary = eventLabel
      ?? readable
      ?? [context?.work_item_title, row.reason, row.event_type, sourceTypeLabels[sourceType], sourceType].find(nonEmpty);
    const time = [signal?.source_time, row.source_time, row.value_at, row.created_at].find(nonEmpty);
    const sourceRef = [signal?.source_ref, row.source_ref].find(nonEmpty);
    const sourceLink = signalSourceLink(contextJson);
    const secondary = [
      typeof row.role === "string" ? labelOf(evidenceRoleLabels, row.role) : undefined,
      typeof row.date_type === "string" ? labelOf(dateTypeLabels, row.date_type) : undefined,
      typeof row.relation_type === "string" ? labelOf(relationTypeLabels, row.relation_type) : undefined,
      eventLabel ? row.reason : undefined,
      labelOf(sourceTypeLabels, sourceType),
      sourceRef,
      time ? formatTaskTime(time) : undefined,
    ].filter((value) => nonEmpty(value) && value !== primary);
    return <li key={String(row.id ?? index)}><span className="business-source-text">{typeof primary === "string" ? primary : "记录"}</span>{secondary.length > 0 && <small>{secondary.join(" · ")}</small>}
      {sourceLink && <a href={sourceLink} target="_blank" rel="noreferrer">打开来源</a>}
      {sourceUnavailable && <small>原文不可用</small>}
      {raw !== undefined && <details className="business-source-raw"><summary>查看原始记录</summary><pre>{JSON.stringify(raw, null, 2)}</pre></details>}</li>;
  })}</ol>;
}

export function SourceRecordList({ title, rows, open = true }: { title: string; rows: Array<Record<string, unknown>>; open?: boolean }) {
  if (!rows.length) return null;
  return <DetailSection title={title} count={rows.length} open={open}><SourceRecordRows rows={rows} /></DetailSection>;
}

export function SourceOriginalRecord({ reference, role }: { reference: BusinessSignalReference; role?: string }) {
  const [original, setOriginal] = useState<BusinessTaskSignal | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const activeRead = useRef<AbortController | null>(null);
  useEffect(() => () => activeRead.current?.abort(), []);
  const readOriginal = async () => {
    const controller = new AbortController();
    activeRead.current = controller;
    setLoading(true); setError("");
    try {
      const result = await getBusinessTaskSignalDetail(String(reference.id), controller.signal);
      if (!controller.signal.aborted) setOriginal(result.item);
    } catch (reason: unknown) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "原文读取失败");
    } finally { if (!controller.signal.aborted) setLoading(false); }
  };
  return <div>
    <SourceRecordRows rows={[{ role, signal: original ?? reference }]} />
    {!original && <button type="button" className="secondary-button" disabled={loading} aria-label={`读取来源 ${reference.id} 原文`} onClick={() => void readOriginal()}>{loading ? "读取原文中…" : "读取原文"}</button>}
    {error && <p role="alert">{error}</p>}
  </div>;
}

const followUpStatusLabels: Record<string, string> = { draft: "待你决定", approved: "待你决定", sent: "已发送", failed: "发送失败", cancelled: "已取消", completed: "Task 已完成", skipped: "已跳过" };

function text(value: unknown) { return typeof value === "string" ? value : ""; }

function followUpFailure(row: Record<string, unknown>) {
  try {
    const result = JSON.parse(text(row.send_result_json) || "{}") as Record<string, unknown>;
    return text(result.error) ? "结果未知，请先到钉钉确认" : "钉钉未接收";
  } catch { return ""; }
}

function legacyValue(value: unknown): string {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function LegacyRawRecord({ label, value }: { label: string; value: unknown }) {
  if (value === undefined || value === null || value === "") return null;
  const content = typeof value === "string" ? parseJson(value) ?? value : value;
  return <details className="business-source-raw"><summary>{label}</summary><pre>{typeof content === "string" ? content : JSON.stringify(content, null, 2)}</pre></details>;
}

function LegacyProjectHistory({ detail }: { detail: LegacyProjectDetail }) {
  return <>
    <SourceRecordList title="历史事实" rows={detail.facts.map((fact) => ({
      id: fact.id,
      evidence_text: legacyValue(fact.description),
      source_ref: legacyValue(fact.source),
      source_time: fact.created,
    }))} />
    {!!detail.source_conversations.length && <DetailSection title="历史来源会话" count={detail.source_conversations.length} open={false}>
      <ol className="business-source-list">{detail.source_conversations.map((conversation, index) => {
        const record = conversation && typeof conversation === "object" ? conversation as Record<string, unknown> : null;
        const label = legacyValue(record?.title) || legacyValue(record?.summary) || legacyValue(record?.source_ref) || legacyValue(conversation) || "历史来源会话";
        return <li key={legacyValue(record?.id) || index}>
          <span className="business-source-text">{label}</span>
          <LegacyRawRecord label="查看来源会话记录" value={conversation} />
        </li>;
      })}</ol>
    </DetailSection>}
    {!!detail.todos.length && <DetailSection title="历史待办" count={detail.todos.length}>
      <ol className="business-source-list">{detail.todos.map((todo, index) => {
        const id = legacyValue(todo.id) || String(index);
        const status = legacyValue(todo.status);
        return <li id={`todo-${id}`} key={id}>
          <span className="business-source-text">{legacyValue(todo.title) || "历史待办"}</span>
          <small>记录状态（历史）：{status || "未提供"}{legacyValue(todo.owner_name) ? ` · 负责人：${legacyValue(todo.owner_name)}` : ""}</small>
          {legacyValue(todo.description) && <p>{legacyValue(todo.description)}</p>}
          {todo.completion_evidence !== undefined && todo.completion_evidence !== null && <LegacyRawRecord label="查看历史完成依据" value={todo.completion_evidence} />}
          {!!(Array.isArray(todo.follow_ups) ? todo.follow_ups : []).length && <ol className="business-source-list">{(todo.follow_ups as Array<Record<string, unknown>>).map((followUp, followIndex) => <LegacyFollowUp key={legacyValue(followUp.id) || followIndex} row={followUp} />)}</ol>}
          {!!(Array.isArray(todo.dingtalk_todos) ? todo.dingtalk_todos : []).length && <ol className="business-source-list">{(todo.dingtalk_todos as Array<Record<string, unknown>>).map((receipt, receiptIndex) => <LegacyDingTalkReceipt key={legacyValue(receipt.id) || receiptIndex} row={receipt} />)}</ol>}
        </li>;
      })}</ol>
    </DetailSection>}
    {!!detail.updates.length && <DetailSection title="历史更新" count={detail.updates.length} open={false}>
      <ol className="business-source-list">{detail.updates.map((update, index) => <li key={legacyValue(update.id) || index}>
        <span className="business-source-text">{legacyValue(update.summary) || "历史更新"}</span>
        <small>{[legacyValue(update.source_type), legacyValue(update.source_ref), legacyValue(update.created_at)].filter(Boolean).join(" · ")}</small>
        <LegacyRawRecord label="查看历史变更字段" value={update.changes} />
      </li>)}</ol>
    </DetailSection>}
    {!!detail.evidence_candidates.length && <DetailSection title="历史依据记录" count={detail.evidence_candidates.length}>
      <ol className="business-source-list">{detail.evidence_candidates.map((candidate, index) => <li id={`evidence-${legacyValue(candidate.id) || index}`} key={legacyValue(candidate.id) || index}>
        <span className="business-source-text">{legacyValue(candidate.evidence_text) || "历史依据记录"}</span>
        <small>{[legacyValue(candidate.source_type), legacyValue(candidate.source_ref), legacyValue(candidate.source_created_at), legacyValue(candidate.reason)].filter(Boolean).join(" · ")}</small>
        <small>记录状态（历史）：{legacyValue(candidate.status) || "未提供"}</small>
        <LegacyRawRecord label="查看历史依据记录" value={candidate.decision} />
      </li>)}</ol>
    </DetailSection>}
    {!!detail.unlinked_follow_ups.length && <DetailSection title="未关联任务的历史催办" count={detail.unlinked_follow_ups.length}>
      <ol className="business-source-list">{detail.unlinked_follow_ups.map((followUp, index) => <LegacyFollowUp key={legacyValue(followUp.id) || index} row={followUp} />)}</ol>
    </DetailSection>}
  </>;
}

function LegacyFollowUp({ row }: { row: Record<string, unknown> }) {
  const id = legacyValue(row.id);
  return <li id={id ? `follow-up-${id}` : undefined}>
    <span className="business-source-text">{legacyValue(row.question_text || row.title) || "历史催办"}</span>
    <small>记录状态（历史）：{legacyValue(row.status) || "未提供"}</small>
    <LegacyRawRecord label="查看历史发送回执" value={row.send_result_json} />
  </li>;
}

function LegacyDingTalkReceipt({ row }: { row: Record<string, unknown> }) {
  const id = legacyValue(row.id);
  return <li>
    <span className="business-source-text">钉钉待办回执{id ? ` ${id}` : ""}{legacyValue(row.title_snapshot) ? ` · ${legacyValue(row.title_snapshot)}` : ""}</span>
    <small>记录状态（历史）：{legacyValue(row.status) || "未提供"}{legacyValue(row.dingtalk_task_id) ? ` · 钉钉待办 ID：${legacyValue(row.dingtalk_task_id)}` : ""}{legacyValue(row.executor_name) ? ` · 执行人：${legacyValue(row.executor_name)}` : ""}</small>
    {legacyValue(row.last_error) && <small>历史同步错误：{legacyValue(row.last_error)}</small>}
    <LegacyRawRecord label="查看钉钉待办同步回执" value={row.last_dingtalk_payload_json} />
  </li>;
}

/**
 * 催办 on this Task. Derek, 2026-09-25: nothing is sent automatically; he
 * decides with one click, and new information that updates the Task
 * withdraws the pending suggestion (shown as 已取消 with the reason).
 */
export function FollowUpSection({ taskId, rows, onChanged }: { taskId: string; rows: Array<Record<string, unknown>>; onChanged: () => void }) {
  const [sending, setSending] = useState("");
  const [message, setMessage] = useState<{ id: string; text: string; error: boolean } | null>(null);
  if (!rows.length) return null;
  const send = (row: Record<string, unknown>) => {
    const id = String(row.id);
    setSending(id);
    setMessage(null);
    sendBusinessTaskFollowUp(taskId, id, Number(row.revision) || 0)
      .then((result) => { setMessage({ id, text: result.message, error: false }); onChanged(); })
      .catch((reason: unknown) => { setMessage({ id, text: reason instanceof Error ? reason.message : "发送失败", error: true }); onChanged(); })
      .finally(() => setSending(""));
  };
  const pending = rows.filter((row) => ["draft", "approved", "failed"].includes(text(row.status))).length;
  return <section className={`console-card business-detail-section${pending ? " has-pending-action" : ""}`}><h2>催办{pending > 0 && <span className="business-fold-count is-alert">{pending} 条待你决定</span>}</h2><ol className="business-source-list follow-up-list">{rows.map((row) => {
    const id = String(row.id);
    const status = text(row.status);
    const sendable = status === "draft" || status === "approved" || status === "failed";
    const facts = [
      followUpStatusLabels[status] || status,
      status === "failed" ? followUpFailure(row) : "",
      text(row.target_kind) === "group" ? "群里 @负责人" : "私聊负责人",
      text(row.owner_name),
      status === "sent" ? `发送于 ${formatTaskTime(text(row.sent_at))}` : `建议 ${formatTaskTime(text(row.scheduled_at))}`,
      status === "cancelled" || status === "skipped" ? text(row.suppressed_reason) : "",
    ].filter(Boolean);
    return <li key={id} className="follow-up-row"><div className="follow-up-body"><span>{text(row.question_text)}</span><small>{facts.join(" · ")}</small>
      {message?.id === id && <small role={message.error ? "alert" : "status"} className={message.error ? "follow-up-error" : ""}>{message.text}</small>}</div>
      {sendable && <button type="button" className="secondary-button follow-up-send" disabled={sending === id} onClick={() => send(row)} title={status === "failed" ? "重新发送这条催办" : "发送这条催办"}><Send size={14} aria-hidden="true" />{sending === id ? "发送中…" : status === "failed" ? "重发" : "发送"}</button>}
    </li>;
  })}</ol></section>;
}

export function TaskDetailPage({ taskId }: { taskId: string }) {
  return <TaskDetailContent key={taskId} taskId={taskId} />;
}

function TaskDetailContent({ taskId }: { taskId: string }) {
  const [detail, setDetail] = useState<BusinessTaskDetail | null>(null);
  const [snapshot, setSnapshot] = useState("");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    if (reloadKey === 0) setState("loading");
    getBusinessTaskDetail(taskId, controller.signal).then((result) => { if (!controller.signal.aborted) { setDetail(result.item); setSnapshot(result.meta.snapshot_at); setState("ready"); } }).catch((reason: unknown) => { if (!controller.signal.aborted) { setError(reason instanceof Error ? reason.message : "加载失败"); setState("error"); } });
    return () => controller.abort();
  }, [taskId, reloadKey]);
  const trail = [{ label: "Tasks", to: "/tasks" }, { label: "全部任务", to: "/tasks?view=all" }];
  if (state === "loading") return <ConsolePageLayout title="任务详情" breadcrumb={[...trail, { label: "任务详情" }]}><TaskSkeleton /></ConsolePageLayout>;
  if (state === "error" || !detail) return <ConsolePageLayout title="任务详情" breadcrumb={[...trail, { label: "任务详情" }]}><div className="page-state page-state-error" role="alert">{error || "任务不存在"}</div></ConsolePageLayout>;
  const task = detail.summary;
  const suggestion = detail.suggestion;
  const missing = detail.missing_evidence || [];
  return <ConsolePageLayout title={task.title} breadcrumb={[...trail, { label: "任务详情" }]} actions={<SnapshotBadge timestamp={snapshot} />}>
    <div className="task-domain-page business-detail-page">
      <section className="console-card business-detail-section"><div className="business-detail-badges"><span className={`business-stage ${task.stage}`}>{task.stage === "candidate" ? "候选任务" : "正式任务"}</span><span className="business-origin">{taskOriginLabel(task)}</span><span>{labelOf(taskStatusLabels, task.status)}</span>{labelOf(commitmentLabels, task.commitment_status) !== labelOf(taskStatusLabels, task.status) && <span>{labelOf(commitmentLabels, task.commitment_status)}</span>}<span className="business-detail-badge-actions"><CandidateAction task={task} showLabel onDone={() => setReloadKey((key) => key + 1)} /></span></div>
        {detail.description && <p className="business-detail-description">{detail.description}</p>}
        <dl className="business-detail-facts"><div><dt>负责人</dt><dd>{task.owner || "待明确"}</dd></div><div><dt>{task.deadline_type && task.deadline_at ? labelOf(dateTypeLabels, task.deadline_type) : "任务日期"}</dt><dd>{task.deadline_type && task.deadline_at ? task.deadline_at : "待明确"}</dd></div><div><dt>业务主线</dt><dd>{task.anchor_labels.join(" · ") || "尚未确认"}</dd></div>{missing.length > 0 && <div><dt>{task.stage === "candidate" ? "转为正式任务还需" : "还缺依据"}</dt><dd>{missing.join("；")}</dd></div>}</dl>
      </section>
      {suggestion && <DetailSection title={isCurrentSuggestion(task) ? "建议说明" : "历史建议依据"}>
        <p className="business-section-note">{isCurrentSuggestion(task) ? "建议尚未指派，不表示负责人已接受或承诺。" : "本任务已转为正式任务，保留最初建议的来源依据；当前负责人、承诺与日期以任务记录为准。"}</p>
        <dl className="business-detail-facts"><div><dt>{isCurrentSuggestion(task) ? "建议负责人" : "最初建议负责人"}</dt><dd>{suggestion.suggested_owner_name || "待明确"}</dd></div><div><dt>建议原因</dt><dd>{suggestion.reason}</dd></div></dl>
        <h3>职责依据</h3>{suggestion.responsibility_evidence.length ? <CitationList citations={suggestion.responsibility_evidence} /> : <p className="business-empty-line">职责待明确。</p>}
        <h3>建议依据</h3><CitationList citations={suggestion.basis_evidence} />
      </DetailSection>}
      <FollowUpSection taskId={taskId} rows={detail.follow_ups || []} onChanged={() => setReloadKey((key) => key + 1)} />
      {detail.evidence.length > 0 && <DetailSection title="来源证据" count={detail.evidence.length}>
        {detail.evidence.map((evidence, index) => evidence.signal
          ? <SourceOriginalRecord key={`${taskId}:${evidence.signal.id}:${evidence.role}`} reference={evidence.signal} role={evidence.role} />
          : <SourceRecordRows key={`${taskId}:missing:${index}`} rows={[evidence]} />)}
      </DetailSection>}
      {!!detail.official_projects?.length && <DetailSection title="正式项目" count={detail.official_projects.length}><ul className="business-linked-list">{detail.official_projects.map((project) => <li key={project.id}><Link to={project.detail_url}>{project.title}</Link></li>)}</ul></DetailSection>}
      <SourceRecordList title="日期依据" rows={detail.date_evidence || []} open={false} />
      <SourceRecordList title="任务变化" rows={detail.events || []} open={false} />
      <SourceRecordList title="关联关系" rows={detail.relations || []} open={false} />
      <SourceRecordList title="工作聚类" rows={detail.clusters || []} open={false} />
      <SourceRecordList title="钉钉待办" rows={detail.dingtalk_todos || []} open={false} />
    </div>
  </ConsolePageLayout>;
}

export function LegacyProjectDetailPage({ legacyProjectId }: { legacyProjectId: string }) {
  return <HistoricalProjectDetailContent key={legacyProjectId} legacyProjectId={legacyProjectId} />;
}

function HistoricalProjectDetailContent({ legacyProjectId }: { legacyProjectId: string }) {
  const [detail, setDetail] = useState<LegacyProjectDetail | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    getLegacyProjectDetail(legacyProjectId, controller.signal).then((result) => { if (!controller.signal.aborted) setDetail(result.item); }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "加载失败"); });
    return () => controller.abort();
  }, [legacyProjectId]);
  const title = detail?.title || "历史项目记录";
  return <ConsolePageLayout title={title} breadcrumb={[{ label: "Tasks", to: "/tasks" }, { label: "正式项目", to: "/tasks?view=projects" }, { label: "历史项目记录" }]}>
    <div className="task-domain-page business-detail-page">
      {error ? <div className="page-state page-state-error" role="alert">{error}</div> : !detail ? <TaskSkeleton /> : <>
        <section className="console-card business-detail-section"><div className="business-detail-badges"><span className="business-stage candidate">历史记录 · 非正式项目</span><span>记录状态（历史）：{labelOf(legacyStatusLabels, detail.status)}</span></div>
          {detail.description && <p className="business-detail-description">{detail.description}</p>}
          <dl className="business-detail-facts"><div><dt>负责人</dt><dd>{detail.owner || "未提供"}</dd></div></dl></section>
        <LegacyProjectHistory detail={detail} />
      </>}
    </div>
  </ConsolePageLayout>;
}
