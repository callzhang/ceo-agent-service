import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { displayValue, getCodexSession } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

export function CodexSessionDetailPage() {
  const { sessionId = "" } = useParams();
  const [payload, setPayload] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState("");
  useEffect(() => { const controller = new AbortController(); getCodexSession(sessionId, controller.signal).then((response) => { setPayload(response.item); setSnapshot(response.meta.snapshot_at); }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "加载失败"); }); return () => controller.abort(); }, [sessionId]);
  return <ConsolePageLayout title="Codex Session" actions={<><SnapshotBadge timestamp={snapshot} /><Link className="secondary-button" to="/codex">返回 Codex</Link></>}><section className="console-card">{error ? <div className="page-state page-state-error" role="alert">{error}</div> : !payload ? <div className="page-state" role="status">正在加载…</div> : <><h2>{payload.available ? "执行记录" : "执行记录不可用"}</h2><p>{displayValue(payload.message || (payload.available ? "本次执行已关联业务历史。" : "本机 transcript 文件已不可用，仍保留关联历史。"))}</p><details><summary>Runtime details</summary><pre className="technical-details">{JSON.stringify(payload.events || payload.related_attempts || {}, null, 2)}</pre></details></>}</section></ConsolePageLayout>;
}
