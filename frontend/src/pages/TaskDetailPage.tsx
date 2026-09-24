import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { getBusinessTaskDetail, getLegacyProjectDetail, type BusinessTaskDetail, type TaskDetail } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

const commitmentLabels: Record<string, string> = { none: "承诺待明确", assigned_unaccepted: "已指派，未接受", accepted: "已接受", disputed: "存在争议", completed: "已完成", cancelled: "已取消" };

export function SourceRecordList({ title, rows }: { title: string; rows: Array<Record<string, unknown>> }) {
  if (!rows.length) return null;
  return <section className="console-card business-detail-section"><h2>{title}</h2><ol className="business-source-list">{rows.map((row, index) => {
    const signal = row.signal && typeof row.signal === "object" ? row.signal as Record<string, unknown> : null;
    const primary = [signal?.evidence_text, row.evidence_text, row.raw_phrase, row.title, row.summary, row.reason, row.event_type, row.source_type].find((value) => typeof value === "string" && value);
    const secondary = [row.role, row.date_type, row.value_at, signal?.source_type, row.source_type, row.created_at].filter((value) => typeof value === "string" && value && value !== primary);
    return <li key={String(row.id ?? index)}><span>{typeof primary === "string" ? primary : "记录"}</span>{secondary.length > 0 && <small>{secondary.join(" · ")}</small>}</li>;
  })}</ol></section>;
}

export function TaskDetailPage({ taskId }: { taskId: string }) {
  const [detail, setDetail] = useState<BusinessTaskDetail | null>(null);
  const [snapshot, setSnapshot] = useState("");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    getBusinessTaskDetail(taskId, controller.signal).then((result) => { if (!controller.signal.aborted) { setDetail(result.item); setSnapshot(result.meta.snapshot_at); setState("ready"); } }).catch((reason: unknown) => { if (!controller.signal.aborted) { setError(reason instanceof Error ? reason.message : "加载失败"); setState("error"); } });
    return () => controller.abort();
  }, [taskId]);
  if (state === "loading") return <ConsolePageLayout title="任务详情"><div className="page-state" role="status">正在加载…</div></ConsolePageLayout>;
  if (state === "error" || !detail) return <ConsolePageLayout title="任务详情"><div className="page-state page-state-error" role="alert">{error || "任务不存在"}</div></ConsolePageLayout>;
  const task = detail.summary;
  return <ConsolePageLayout title={task.title} actions={<><SnapshotBadge timestamp={snapshot} /><Link className="secondary-button" to="/tasks?view=all">返回全部任务</Link></>}>
    <div className="task-domain-page business-detail-page">
      <section className="console-card business-detail-section"><div className="business-detail-badges"><span className={`business-stage ${task.stage}`}>{task.stage === "candidate" ? "候选任务" : "正式任务"}</span><span>{task.status}</span><span>{commitmentLabels[task.commitment_status] || task.commitment_status}</span></div>
        {detail.description && <p>{detail.description}</p>}
        <dl className="business-detail-facts"><div><dt>负责人</dt><dd>{task.owner || "尚无明确负责人"}</dd></div><div><dt>截止日期</dt><dd>{task.deadline_at || "未明确"}</dd></div><div><dt>业务主线</dt><dd>{task.anchor_labels.join(" · ") || "尚未确认"}</dd></div></dl>
      </section>
      <SourceRecordList title="来源证据" rows={detail.evidence || []} />
      <SourceRecordList title="日期依据" rows={detail.date_evidence || []} />
      <SourceRecordList title="任务变化" rows={detail.events || []} />
      <SourceRecordList title="关联关系" rows={detail.relations || []} />
      <SourceRecordList title="工作聚类" rows={detail.clusters || []} />
      <SourceRecordList title="跟进记录" rows={detail.follow_ups || []} />
      <SourceRecordList title="钉钉待办" rows={detail.dingtalk_todos || []} />
      {!!detail.official_projects?.length && <section className="console-card business-detail-section"><h2>正式项目</h2><ul className="business-linked-list">{detail.official_projects.map((project) => <li key={project.id}><Link to={project.detail_url}>{project.title}</Link></li>)}</ul></section>}
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
  return <ConsolePageLayout title={detail?.title || "历史项目记录"} actions={<Link className="secondary-button" to="/tasks">返回 Tasks</Link>}><section className="console-card business-detail-section"><span className="business-stage candidate">历史记录 · 非正式项目</span>{error ? <p role="alert">{error}</p> : !detail ? <p role="status">正在加载…</p> : <><p>{detail.description}</p><dl className="business-detail-facts"><div><dt>负责人</dt><dd>{detail.owner || "未提供"}</dd></div><div><dt>状态</dt><dd>{detail.status}</dd></div></dl><SourceRecordList title="历史事实" rows={detail.facts.map((fact) => ({ ...fact, evidence_text: String(fact.description) }))} /></>}</section></ConsolePageLayout>;
}
