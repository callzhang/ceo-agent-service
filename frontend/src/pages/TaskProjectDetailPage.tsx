import { useEffect, useState } from "react";

import { getBusinessProjectDetail, type BusinessProjectDetail } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";
import { DetailSection, LinkedTaskList, TaskSkeleton } from "./TaskParts";
import { labelOf, taskStatusLabels } from "./taskLabels";

const trail = [{ label: "Tasks", to: "/tasks" }, { label: "正式项目", to: "/tasks?view=projects" }];

/** "待处理 2 · 已完成 1": how the confirmed Tasks stand, in the order a reader cares about. */
function statusSummary(tasks: BusinessProjectDetail["confirmed_tasks"]) {
  const counts = new Map<string, number>();
  for (const task of tasks) counts.set(task.status, (counts.get(task.status) ?? 0) + 1);
  return Object.keys(taskStatusLabels).filter((status) => counts.has(status)).map((status) => `${labelOf(taskStatusLabels, status)} ${counts.get(status)}`).join(" · ");
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
  const tasks = detail.confirmed_tasks ?? [];
  const sourceLabel = project.source_title || project.registry_source || "权威周报";
  const totalTaskCount = project.confirmed_task_count;
  const openTaskCount = project.open_task_count ?? tasks.filter((task) => task.status !== "done" && task.status !== "cancelled").length;
  const doneTaskCount = project.done_task_count ?? tasks.filter((task) => task.status === "done").length;
  return <ConsolePageLayout title={project.title} breadcrumb={[...trail, { label: "项目详情" }]} actions={<SnapshotBadge timestamp={snapshot} />}><div className="task-domain-page business-detail-page">
    <section className="console-card business-detail-section"><div className="business-detail-badges"><span className="business-stage formal">正式项目</span>{tasks.length > 0 && <span>{statusSummary(tasks)}</span>}</div>
      <dl className="business-detail-facts">
        {project.responsible_content && <div><dt>负责内容</dt><dd>{project.responsible_content}</dd></div>}
        {project.goal && <div><dt>项目目标</dt><dd>{project.goal}</dd></div>}
        {project.current_status && <div><dt>当前状态</dt><dd>{project.current_status}</dd></div>}
        {project.deadline && <div><dt>截止/周期</dt><dd>{project.deadline}</dd></div>}
        <div><dt>所属业务主线</dt><dd>{typeof detail.anchor?.title === "string" ? detail.anchor.title : "未提供"}</dd></div>
        <div><dt>关联任务</dt><dd>{totalTaskCount} 个（进行中 {openTaskCount} · 已完成 {doneTaskCount}）</dd></div>
        <div><dt>登记依据</dt><dd>{project.source_url ? <><a href={project.source_url} target="_blank" rel="noreferrer">{sourceLabel}{project.reporting_period ? ` · ${project.reporting_period}` : ""}</a>{project.registry_source && project.registry_source !== sourceLabel && <span>（<span>{project.registry_source}</span>）</span>}</> : project.registry_source}</dd></div>
      </dl>
      {project.source_excerpt && <p className="business-task-meta">原文摘录：{project.source_excerpt}</p>}
    </section>
    <DetailSection title="确认关联的任务" count={tasks.length}>{tasks.length ? <LinkedTaskList tasks={tasks} /> : <p className="business-empty-line">暂无确认关联任务。</p>}</DetailSection>
  </div></ConsolePageLayout>;
}
