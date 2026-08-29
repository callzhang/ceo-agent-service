import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { displayValue, getResource } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";
import { StatusBadge } from "../components/status/StatusBadge";

export function RuntimeErrorDetailPage() {
  const { errorId = "" } = useParams();
  const [item, setItem] = useState<Record<string, unknown> | null>(null);
  const [snapshot, setSnapshot] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setItem(null);
    setError("");
    getResource(`/api/console/history/errors/${encodeURIComponent(errorId)}`, controller.signal)
      .then((response) => {
        setItem(response.item);
        setSnapshot(response.meta.snapshot_at);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "加载失败");
      });
    return () => controller.abort();
  }, [errorId]);

  const resolvedAt = displayValue(item?.resolved_at);
  const resolution = displayValue(item?.resolution);

  return (
    <ConsolePageLayout title="Service error" actions={<><SnapshotBadge timestamp={snapshot} /><Link className="secondary-button" to="/attention">返回 Attention</Link></>}>
      <section className="console-card runtime-error-detail">
        {error && <div className="page-state page-state-error" role="alert">{error}</div>}
        {!item && !error && <div className="page-state" role="status">正在加载…</div>}
        {item && <>
          <div className="task-overview-meta"><StatusBadge value={displayValue(item.status)} /><span>{displayValue(item.kind || item.title)}</span></div>
          <h2>{displayValue(item.summary || item.error)}</h2>
          <dl className="detail-definition-list">
            <div><dt>错误</dt><dd>{displayValue(item.error || item.summary)}</dd></div>
            <div><dt>发生时间</dt><dd>{displayValue(item.created_at)}</dd></div>
            {resolvedAt !== "未提供" && <div><dt>解决时间</dt><dd>{resolvedAt}</dd></div>}
            {resolution !== "未提供" && <div><dt>解决说明</dt><dd>{resolution}</dd></div>}
          </dl>
          <details>
            <summary>Runtime details</summary>
            <pre className="technical-details">{JSON.stringify(item.runtime || {}, null, 2)}</pre>
          </details>
        </>}
      </section>
    </ConsolePageLayout>
  );
}
