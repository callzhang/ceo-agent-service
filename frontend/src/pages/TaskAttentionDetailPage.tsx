import { useEffect, useState } from "react";

import { getBusinessAttentionDetail, type BusinessAttentionDetail } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";
import { DetailSection, LinkedTaskList, TaskSkeleton } from "./TaskParts";
import { SourceRecordList } from "./TaskDetailPage";
import { categoryLabels } from "./taskLabels";

const trail = [{ label: "Tasks", to: "/tasks" }, { label: "需关注", to: "/tasks" }];

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
  if (state === "loading") return <ConsolePageLayout title="关注事项" breadcrumb={[...trail, { label: "关注事项" }]}><TaskSkeleton /></ConsolePageLayout>;
  if (state === "error" || !detail) return <ConsolePageLayout title="关注事项" breadcrumb={[...trail, { label: "关注事项" }]}><div className="page-state page-state-error" role="alert">{error || "事项不存在"}</div></ConsolePageLayout>;
  const item = detail.summary;
  return <ConsolePageLayout title={item.title} breadcrumb={[...trail, { label: "关注事项" }]} actions={<SnapshotBadge timestamp={snapshot} />}><div className="task-domain-page business-detail-page">
    <section className={`business-action-callout category-${item.category}`} aria-label="你的动作"><span className="business-attention-category">{categoryLabels[item.category]}</span><span className="business-detail-area">{item.business_area}</span><h2>你的动作</h2><p>{item.ceo_action}</p></section>
    <section className={`console-card business-detail-section category-${item.category}`}><dl className="business-detail-facts"><div><dt>为什么关注</dt><dd>{item.why_attention}</dd></div><div><dt>当前状态</dt><dd>{item.current_state}</dd></div><div><dt>业务主线</dt><dd>{item.anchor_label || "尚未关联"}</dd></div></dl></section>
    <DetailSection title="关联任务" count={detail.linked_tasks?.length ?? 0}>{detail.linked_tasks?.length ? <LinkedTaskList tasks={detail.linked_tasks} /> : <p className="business-empty-line">暂无关联任务。</p>}</DetailSection>
    <SourceRecordList title="来源证据" rows={detail.evidence_signals || []} open={false} />
    <SourceRecordList title="关注历程" rows={detail.events || []} open={false} />
  </div></ConsolePageLayout>;
}
