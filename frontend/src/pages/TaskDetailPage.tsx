import { Send } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { getBusinessTaskDetail, getLegacyProjectDetail, sendBusinessTaskFollowUp, type BusinessTaskDetail, type TaskDetail } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";
import { CandidateAction, DetailSection, TaskSkeleton } from "./TaskParts";
import { formatTaskTime } from "./TaskTime";
import { attentionEventLabels, commitmentLabels, dateTypeLabels, evidenceRoleLabels, labelOf, relationTypeLabels, sourceTypeLabels, taskEventLabels, taskStatusLabels } from "./taskLabels";

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
function SourceRecordRows({ rows }: { rows: Array<Record<string, unknown>> }) {
  return <ol className="business-source-list">{rows.map((row, index) => {
    const signal = row.signal && typeof row.signal === "object" ? row.signal as Record<string, unknown> : null;
    const rawText = [signal?.evidence_text, row.evidence_text].find(nonEmpty);
    const raw = parseJson(rawText);
    const context = parseJson(signal?.context_json) as Record<string, unknown> | undefined;
    const readable = [raw === undefined ? rawText : undefined, row.raw_phrase, row.title, row.summary].find(nonEmpty);
    const eventLabel = typeof row.event_type === "string" ? eventLabels[row.event_type] : undefined;
    const sourceType = String(signal?.source_type ?? row.source_type ?? "");
    const primary = eventLabel
      ?? readable
      ?? [context?.work_item_title, row.reason, row.event_type, sourceTypeLabels[sourceType], sourceType].find(nonEmpty);
    const time = [signal?.source_time, row.value_at, row.created_at].find(nonEmpty);
    const secondary = [
      typeof row.role === "string" ? labelOf(evidenceRoleLabels, row.role) : undefined,
      typeof row.date_type === "string" ? labelOf(dateTypeLabels, row.date_type) : undefined,
      typeof row.relation_type === "string" ? labelOf(relationTypeLabels, row.relation_type) : undefined,
      eventLabel ? row.reason : undefined,
      labelOf(sourceTypeLabels, sourceType),
      time ? formatTaskTime(time) : undefined,
    ].filter((value) => nonEmpty(value) && value !== primary);
    return <li key={String(row.id ?? index)}><span>{typeof primary === "string" ? primary : "记录"}</span>{secondary.length > 0 && <small>{secondary.join(" · ")}</small>}
      {raw !== undefined && <details className="business-source-raw"><summary>查看原始记录</summary><pre>{JSON.stringify(raw, null, 2)}</pre></details>}</li>;
  })}</ol>;
}

export function SourceRecordList({ title, rows, open = true }: { title: string; rows: Array<Record<string, unknown>>; open?: boolean }) {
  if (!rows.length) return null;
  return <DetailSection title={title} count={rows.length} open={open}><SourceRecordRows rows={rows} /></DetailSection>;
}

const followUpStatusLabels: Record<string, string> = { draft: "待你决定", approved: "待你决定", sent: "已发送", failed: "发送失败", cancelled: "已取消", completed: "Task 已完成", skipped: "已跳过" };

function text(value: unknown) { return typeof value === "string" ? value : ""; }

function followUpFailure(row: Record<string, unknown>) {
  try {
    const result = JSON.parse(text(row.send_result_json) || "{}") as Record<string, unknown>;
    return text(result.error) ? "结果未知，请先到钉钉确认" : "钉钉未接收";
  } catch { return ""; }
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
  const missing = detail.missing_evidence || [];
  return <ConsolePageLayout title={task.title} breadcrumb={[...trail, { label: "任务详情" }]} actions={<SnapshotBadge timestamp={snapshot} />}>
    <div className="task-domain-page business-detail-page">
      <section className="console-card business-detail-section"><div className="business-detail-badges"><span className={`business-stage ${task.stage}`}>{task.stage === "candidate" ? "候选任务" : "正式任务"}</span><span>{labelOf(taskStatusLabels, task.status)}</span>{labelOf(commitmentLabels, task.commitment_status) !== labelOf(taskStatusLabels, task.status) && <span>{labelOf(commitmentLabels, task.commitment_status)}</span>}<span className="business-detail-badge-actions"><CandidateAction task={task} showLabel onDone={() => setReloadKey((key) => key + 1)} /></span></div>
        {detail.description && <p className="business-detail-description">{detail.description}</p>}
        <dl className="business-detail-facts"><div><dt>负责人</dt><dd>{task.owner || "未指定"}</dd></div><div><dt>截止日期</dt><dd>{task.deadline_at || "未明确"}</dd></div><div><dt>业务主线</dt><dd>{task.anchor_labels.join(" · ") || "尚未确认"}</dd></div>{missing.length > 0 && <div><dt>{task.stage === "candidate" ? "转为正式任务还需" : "还缺依据"}</dt><dd>{missing.join("；")}</dd></div>}</dl>
      </section>
      <FollowUpSection taskId={taskId} rows={detail.follow_ups || []} onChanged={() => setReloadKey((key) => key + 1)} />
      <SourceRecordList title="来源证据" rows={detail.evidence || []} />
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
  const [detail, setDetail] = useState<TaskDetail | null>(null);
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
        <section className="console-card business-detail-section"><div className="business-detail-badges"><span className="business-stage candidate">历史记录 · 非正式项目</span><span>{labelOf(legacyStatusLabels, detail.status)}</span></div>
          {detail.description && <p className="business-detail-description">{detail.description}</p>}
          <dl className="business-detail-facts"><div><dt>负责人</dt><dd>{detail.owner || "未提供"}</dd></div></dl></section>
        <SourceRecordList title="历史事实" rows={detail.facts.map((fact) => ({ ...fact, evidence_text: String(fact.description) }))} />
      </>}
    </div>
  </ConsolePageLayout>;
}
