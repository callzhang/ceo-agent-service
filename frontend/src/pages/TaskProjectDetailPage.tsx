import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { getBusinessProjectDetail, type BusinessProjectDetail } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

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
  if (state === "loading") return <ConsolePageLayout title="项目详情"><div className="page-state" role="status">正在加载…</div></ConsolePageLayout>;
  if (state === "error" || !detail) return <ConsolePageLayout title="项目详情"><div className="page-state page-state-error" role="alert">{error || "项目不存在"}</div></ConsolePageLayout>;
  const project = detail.summary;
  return <ConsolePageLayout title={project.title} actions={<><SnapshotBadge timestamp={snapshot} /><Link className="secondary-button" to="/tasks?view=projects">返回正式项目</Link></>}><div className="task-domain-page business-detail-page">
    <section className="console-card business-detail-section"><span className="business-stage formal">正式项目</span><dl className="business-detail-facts"><div><dt>登记依据</dt><dd>{project.registry_source}</dd></div><div><dt>所属业务主线</dt><dd>{typeof detail.anchor?.title === "string" ? detail.anchor.title : "未提供"}</dd></div><div><dt>确认关联任务</dt><dd>{project.confirmed_task_count} 个</dd></div></dl></section>
    <section className="console-card business-detail-section"><h2>确认关联的任务</h2>{detail.confirmed_tasks?.length ? <ul className="business-linked-list">{detail.confirmed_tasks.map((task) => <li key={task.id}><Link to={task.detail_url}>{task.title}</Link><span>{task.owner || "负责人未明确"}</span></li>)}</ul> : <p>暂无确认关联任务。</p>}</section>
  </div></ConsolePageLayout>;
}
