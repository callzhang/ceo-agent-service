import { useEffect, useState, type ReactNode } from "react";

import { displayValue, getResource } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

export function ResourcePage({ title, endpoint, children }: { title: string; endpoint: string; children?: (payload: Record<string, unknown>) => ReactNode }) {
  const [payload, setPayload] = useState<Record<string, unknown> | null>(null);
  const [snapshot, setSnapshot] = useState("");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    getResource(endpoint, controller.signal).then((response) => {
      setPayload(response.item);
      setSnapshot(response.meta.snapshot_at);
      setState("ready");
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : "加载失败");
      setState("error");
    });
    return () => controller.abort();
  }, [endpoint]);
  return <ConsolePageLayout title={title} actions={<SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} />}>
    <section className="console-card">
      {state === "loading" && <div className="page-state" role="status">正在加载…</div>}
      {state === "error" && <div className="page-state page-state-error" role="alert">{error}</div>}
      {state === "ready" && payload && (children ? children(payload) : <pre className="technical-details">{displayValue(payload)}</pre>)}
    </section>
  </ConsolePageLayout>;
}
