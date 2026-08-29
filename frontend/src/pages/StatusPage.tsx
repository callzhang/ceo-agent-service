import { useCallback, useEffect, useState } from "react";

import { displayValue, getStatus } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

export function StatusPage() {
  const [payload, setPayload] = useState<Record<string, unknown> | null>(null);
  const [snapshot, setSnapshot] = useState("");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const load = useCallback(() => {
    setState("loading");
    return getStatus().then((response) => {
      setPayload(response.item);
      setSnapshot(response.meta.snapshot_at);
      setState("ready");
    }).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "加载失败");
      setState("error");
    });
  }, []);
  useEffect(() => { void load(); }, [load]);

  const service = payload?.service as Record<string, unknown> | undefined;
  const summary = payload?.summary as Record<string, unknown> | undefined;
  const metrics = [
    ["Service", displayValue(service?.state || "unknown")],
    ["PID", displayValue(service?.pid)],
    ["Processing", displayValue(summary?.processing)],
    ["Retryable", displayValue(summary?.retryable)],
    ["Failed", displayValue(summary?.failed)],
  ];
  return (
    <ConsolePageLayout title="Status" actions={<><SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} /><button type="button" className="secondary-button" onClick={() => void load()}>刷新</button></>}>
      {state === "error" ? <section className="console-card page-state page-state-error" role="alert">{error}</section> : state === "loading" && !payload ? <section className="console-card page-state" role="status">正在加载…</section> : <>
        <section className="status-metric-grid">{metrics.map(([label, value]) => <article className="status-metric" key={label}><span>{label}</span><strong>{value}</strong></article>)}</section>
        <section className="console-card"><h2>组件与队列</h2><pre className="technical-details">{displayValue(payload?.components || payload?.queues || "暂无详情")}</pre></section>
      </>}
    </ConsolePageLayout>
  );
}
