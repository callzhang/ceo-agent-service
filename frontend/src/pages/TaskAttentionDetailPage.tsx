import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { getBusinessAttentionDetail, type BusinessAttentionDetail } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";
import { SourceRecordList } from "./TaskDetailPage";

const categoryLabels = { fyi: "仅需知晓", watch: "持续观察", decision: "需要决策", push: "需要推动" };

export function TaskAttentionDetailPage({ attentionId }: { attentionId: string }) {
  const [detail, setDetail] = useState<BusinessAttentionDetail | null>(null);
  const [snapshot, setSnapshot] = useState("");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    getBusinessAttentionDetail(attentionId, controller.signal).then((result) => { if (!controller.signal.aborted) { setDetail(result.item); setSnapshot(result.meta.snapshot_at); setState("ready"); } }).catch((reason: unknown) => { if (!controller.signal.aborted) { setError(reason instanceof Error ? reason.message : "加载失败"); setState("error"); } });
    return () => controller.abort();
  }, [attentionId]);
  if (state === "loading") return <ConsolePageLayout title="关注事项"><div className="page-state" role="status">正在加载…</div></ConsolePageLayout>;
  if (state === "error" || !detail) return <ConsolePageLayout title="关注事项"><div className="page-state page-state-error" role="alert">{error || "事项不存在"}</div></ConsolePageLayout>;
  const item = detail.summary;
  return <ConsolePageLayout title={item.title} actions={<><SnapshotBadge timestamp={snapshot} /><Link className="secondary-button" to="/tasks">返回需关注</Link></>}><div className="task-domain-page business-detail-page">
    <section className={`console-card business-detail-section category-${item.category}`}><span className="business-attention-category">{categoryLabels[item.category]}</span><span className="business-detail-area">{item.business_area}</span><dl className="business-detail-facts"><div><dt>为什么关注</dt><dd>{item.why_attention}</dd></div><div><dt>当前状态</dt><dd>{item.current_state}</dd></div><div><dt>你的动作</dt><dd>{item.ceo_action}</dd></div><div><dt>业务主线</dt><dd>{item.anchor_label || "尚未关联"}</dd></div></dl></section>
    <section className="console-card business-detail-section"><h2>关联任务</h2>{detail.linked_tasks?.length ? <ul className="business-linked-list">{detail.linked_tasks.map((task) => <li key={task.id}><Link to={task.detail_url}>{task.title}</Link><span>{task.owner || "负责人未明确"}</span></li>)}</ul> : <p>暂无关联任务。</p>}</section>
    <SourceRecordList title="来源证据" rows={detail.evidence_signals || []} />
    <SourceRecordList title="关注历程" rows={detail.events || []} />
  </div></ConsolePageLayout>;
}
