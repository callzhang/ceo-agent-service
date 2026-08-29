import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { command, displayValue, getResource } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";
import { StatusBadge } from "../components/status/StatusBadge";

export function BusinessDetailPage({ kind, endpoint }: { kind: string; endpoint: string }) {
  const params = useParams();
  const id = params.attemptId || params.runId || params.processInstanceId || "";
  const [payload, setPayload] = useState<Record<string, unknown> | null>(null);
  const [snapshot, setSnapshot] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => { const controller = new AbortController(); getResource(endpoint.replace(":id", encodeURIComponent(id)), controller.signal).then((response) => { setPayload(response.item); setSnapshot(response.meta.snapshot_at); }).catch((error: unknown) => { if (!controller.signal.aborted) setMessage(error instanceof Error ? error.message : "加载失败"); }); return () => controller.abort(); }, [endpoint, id]);
  const runAction = async (action: string) => { setMessage("操作进行中…"); try { const result = await command(`/api/console/history/${encodeURIComponent(id)}/${action}`); setMessage(result.message); } catch (error) { setMessage(error instanceof Error ? error.message : "操作失败"); } };
  return <ConsolePageLayout title={kind} actions={<><SnapshotBadge timestamp={snapshot} /><Link className="secondary-button" to="/history">返回 History</Link></>}><section className="console-card">{message && <p className="page-state">{message}</p>}{!payload && !message ? <p className="page-state" role="status">正在加载…</p> : payload ? <><div className="task-overview-meta"><StatusBadge value={displayValue(payload.status)} /><span>{displayValue(payload.title)}</span></div><dl className="detail-definition-list">{["input", "decision", "output", "reviewer_feedback", "corrected_reply"].map((key) => <div key={key}><dt>{key}</dt><dd>{displayValue(payload[key])}</dd></div>)}</dl><div className="console-page-actions"><button type="button" className="secondary-button" onClick={() => void runAction("rerun")}>重跑</button><button type="button" className="secondary-button" onClick={() => void runAction("feedback")}>提交反馈</button></div><details><summary>Runtime details</summary><pre className="technical-details">{JSON.stringify(payload.runtime || {}, null, 2)}</pre></details></> : <div className="page-state page-state-error" role="alert">{message}</div>}</section></ConsolePageLayout>;
}
