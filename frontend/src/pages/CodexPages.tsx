import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { displayValue, getCodexSession } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

type CodexSessionPayload = {
  available?: boolean;
  message?: string;
  events?: unknown[];
  related_attempts?: Array<{ id: number; status: string }>;
};

export function CodexSessionDetailPage() {
  const { sessionId = "" } = useParams();
  const [payload, setPayload] = useState<CodexSessionPayload | null>(null);
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState("");
  useEffect(() => { const controller = new AbortController(); getCodexSession(sessionId, controller.signal).then((response) => { setPayload(response.item); setSnapshot(response.meta.snapshot_at); }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "加载失败"); }); return () => controller.abort(); }, [sessionId]);
  const relatedAttempts = payload?.related_attempts || [];
  return <ConsolePageLayout title="Codex Session" actions={<><SnapshotBadge timestamp={snapshot} /><Link className="secondary-button" to="/codex">返回 Codex</Link></>}><section className="console-card">{error ? <div className="page-state page-state-error" role="alert">{error}</div> : !payload ? <div className="page-state" role="status">正在加载…</div> : payload.available ? <><h2>执行记录</h2><p>{displayValue(payload.message || "本次执行已关联业务历史。")}</p><details><summary>Runtime details</summary><pre className="technical-details">{JSON.stringify(payload.events || [], null, 2)}</pre></details></> : <><h2>执行记录不可用</h2><p>{displayValue(payload.message || "本机 transcript 文件已不可用。")}</p>{relatedAttempts.length > 0 && <section className="codex-related-attempts"><h3>相关处理记录</h3><ul>{relatedAttempts.map((attempt) => <li key={attempt.id}><Link to={`/attempts/${attempt.id}`}>Attempt #{attempt.id}</Link><span>{attempt.status || "未记录状态"}</span></li>)}</ul></section>}</>}</section></ConsolePageLayout>;
}
