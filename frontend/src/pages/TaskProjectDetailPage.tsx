import { useEffect, useState } from "react";

import { getBusinessProjectDetail, type BusinessProjectDetail, type BusinessTaskSignal, type ProjectContext, type ProjectResponsibility } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";
import { CitationList, DetailSection, LinkedTaskList, TaskSkeleton } from "./TaskParts";
import { SourceRecordRows } from "./TaskDetailPage";
import { TaskTime } from "./TaskTime";
import { dateTypeLabels, labelOf } from "./taskLabels";

const trail = [{ label: "Tasks", to: "/tasks" }, { label: "正式项目", to: "/tasks?view=projects" }];

function Responsibilities({ people, signals = [] }: { people: ProjectResponsibility[]; signals?: BusinessTaskSignal[] }) {
  return people.length ? <ul className="business-responsibilities">{people.map((person, index) => <li key={`${person.person_user_id}-${index}`}>
    <strong>{person.person_name}</strong><p>{person.responsibility}</p><CitationList citations={person.evidence} signals={signals} />
  </li>)}</ul> : <p className="business-empty-line">人员分工待明确。</p>;
}

function ProjectFacts({ context, signals = [] }: { context: ProjectContext | null; signals?: BusinessTaskSignal[] }) {
  return context?.facts.length ? <ul className="business-project-state">{context.facts.map((fact, index) => <li key={`${fact.key}-${index}`}>
    <p>{fact.text}</p>
    {fact.date_type && fact.date_value && <p className="business-typed-date">{labelOf(dateTypeLabels, fact.date_type)}：{fact.date_value}</p>}
    <CitationList citations={fact.evidence} signals={signals} />
  </li>)}</ul> : <p className="business-empty-line">待明确</p>;
}

export function TaskProjectDetailPage({ projectId }: { projectId: string }) {
  const [detail, setDetail] = useState<BusinessProjectDetail | null>(null);
  const [snapshot, setSnapshot] = useState("");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    getBusinessProjectDetail(projectId, controller.signal).then((result) => { if (!controller.signal.aborted) { setDetail(result.item); setSnapshot(result.meta.snapshot_at); setState("ready"); } }).catch((reason: unknown) => { if (!controller.signal.aborted) { setError(reason instanceof Error ? reason.message : "加载失败"); setState("error"); } });
    return () => controller.abort();
  }, [projectId]);
  if (state === "loading") return <ConsolePageLayout title="项目详情" breadcrumb={[...trail, { label: "项目详情" }]}><TaskSkeleton /></ConsolePageLayout>;
  if (state === "error" || !detail) return <ConsolePageLayout title="项目详情" breadcrumb={[...trail, { label: "项目详情" }]}><div className="page-state page-state-error" role="alert">{error || "项目不存在"}</div></ConsolePageLayout>;
  const project = detail.summary;
  const tasks = detail.confirmed_tasks;
  const context = detail.context;
  const owner = context?.overall_owner;
  const sourceLabel = project.source_title || project.registry_source || "待明确";
  const totalTaskCount = project.confirmed_task_count;
  return <ConsolePageLayout title={project.title} breadcrumb={[...trail, { label: "项目详情" }]} actions={<SnapshotBadge timestamp={snapshot} />}><div className="task-domain-page business-detail-page">
    <section className="console-card business-detail-section"><h2>项目整体情况</h2><div className="business-detail-badges"><span className="business-stage formal">正式项目</span></div>
      <dl className="business-detail-facts">
        <div><dt>项目目标</dt><dd>{context?.goal || "待明确"}</dd></div>
        <div><dt>项目范围</dt><dd>{context?.scope || "待明确"}</dd></div>
        <div><dt>总体情况</dt><dd><ProjectFacts context={context} signals={detail.evidence_signals} /></dd></div>
        <div><dt>所属业务主线</dt><dd>{typeof detail.anchor?.title === "string" ? detail.anchor.title : "未提供"}</dd></div>
        <div><dt>资料更新</dt><dd><TaskTime value={project.updated_at} /></dd></div>
      </dl>
    </section>
    <section className="console-card business-detail-section"><h2>总负责人及分工</h2>
      <dl className="business-detail-facts">
        <div><dt>总负责人</dt><dd>{owner?.person_name || "待明确"}</dd></div>
        <div><dt>整体负责事项</dt><dd>{owner?.responsibility || "待明确"}{owner && <CitationList citations={owner.evidence} signals={detail.evidence_signals} />}</dd></div>
      </dl>
      <h3>人员分工</h3><Responsibilities people={detail.responsibilities} signals={detail.evidence_signals} />
    </section>
    <section className="console-card business-detail-section"><h2>为什么关注</h2><p className="business-project-attention">{project.attention_reason || "待明确"}</p></section>
    <DetailSection title="来源任务" count={tasks.length}>
      <p className="business-section-note">实际安排以任务阶段和承诺为准。</p>
      <p className="business-section-note">任务执行统计：{totalTaskCount} 个{project.open_task_count !== undefined && ` · 进行中 ${project.open_task_count}`}{project.done_task_count !== undefined && ` · 已完成 ${project.done_task_count}`}。不代表项目总体状态。</p>
      {tasks.length ? <LinkedTaskList tasks={tasks} /> : <p className="business-empty-line">暂无关联任务。</p>}
    </DetailSection>
    <DetailSection title="Agent 建议" count={detail.suggestions.length}>
      <p className="business-section-note">建议尚未指派，不表示负责人已接受或承诺。</p>
      {detail.suggestions.length ? <LinkedTaskList tasks={detail.suggestions} /> : <p className="business-empty-line">暂无 Agent 建议。</p>}
    </DetailSection>
    <DetailSection title="来源与历史">
      <dl className="business-detail-facts"><div><dt>登记依据</dt><dd>{project.source_url ? <a href={project.source_url} target="_blank" rel="noreferrer">{sourceLabel}</a> : sourceLabel}</dd></div></dl>
      {project.source_excerpt && <blockquote>{project.source_excerpt}</blockquote>}
      <h3>来源证据</h3>
      <p className="business-section-note">已显示 {detail.evidence_signals.length} 条，共 {detail.evidence_meta.total} 条来源证据。</p>
      {detail.evidence_meta.has_more && <p className="business-section-note">仅展示最近 20 条及当前项目资料引用的来源，未展示全部历史。</p>}
      {detail.evidence_signals.length ? <SourceRecordRows rows={detail.evidence_signals} /> : <p className="business-empty-line">暂无保存的来源证据。</p>}
      <h3>项目资料修订</h3>
      <p className="business-section-note">已显示 {detail.context_revisions.length} 条，共 {detail.context_revision_meta.total} 条资料修订。</p>
      {detail.context_revision_meta.has_more && <p className="business-section-note">仅展示最近 20 条修订，未展示全部历史。</p>}
      {detail.context_revisions.length ? <ol className="business-context-history">{detail.context_revisions.map((revision) => <li key={revision.id}>
        <details className="business-context-revision"><summary>资料版本 #{revision.id} · <TaskTime value={revision.created_at} /></summary>
          <dl className="business-detail-facts"><div><dt>项目目标</dt><dd>{revision.context.goal || "待明确"}</dd></div><div><dt>项目范围</dt><dd>{revision.context.scope || "待明确"}</dd></div><div><dt>总负责人</dt><dd>{revision.context.overall_owner?.person_name || "待明确"}</dd></div><div><dt>整体负责事项</dt><dd>{revision.context.overall_owner?.responsibility || "待明确"}{revision.context.overall_owner && <CitationList citations={revision.context.overall_owner.evidence} />}</dd></div></dl>
          <Responsibilities people={revision.context.responsibilities} /><ProjectFacts context={revision.context} />
        </details>
      </li>)}</ol> : <p className="business-empty-line">暂无项目资料修订。</p>}
    </DetailSection>
  </div></ConsolePageLayout>;
}
