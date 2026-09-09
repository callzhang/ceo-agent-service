import { useCallback, useEffect, useState, type ReactNode } from "react";

import { displayValue, getStatus, type WorkerStatus } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";
import { StatusBadge } from "../components/status/StatusBadge";

function StatusSection({ title, children }: { title: string; children: ReactNode }) {
  return <section className="console-card status-section"><h2>{title}</h2>{children}</section>;
}

function StatusMetric({ label, value, detail, tone }: { label: string; value: string; detail: string; tone?: "good" | "bad" | "warning" }) {
  return <article className={`status-metric${tone ? ` status-metric-${tone}` : ""}`}>
    <span>{label}</span>
    <strong>{value}</strong>
    <small>{detail}</small>
  </article>;
}

function StatusTable({ headers, rows, mobileLabels }: { headers: string[]; rows: ReactNode[][]; mobileLabels: string[] }) {
  return <div className="status-table-wrap"><table className="status-table">
    <thead><tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr></thead>
    <tbody>{rows.map((cells, rowIndex) => <tr key={rowIndex}>{cells.map((cell, cellIndex) => <td data-label={mobileLabels[cellIndex] || headers[cellIndex]} key={cellIndex}>{cell}</td>)}</tr>)}</tbody>
  </table></div>;
}

export function StatusPanel() {
  const [payload, setPayload] = useState<WorkerStatus | null>(null);
  const [snapshot, setSnapshot] = useState("");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const load = useCallback(() => {
    setState((current) => current === "ready" ? "ready" : "loading");
    return getStatus().then((response) => {
      setPayload(response.item);
      setSnapshot(response.meta.snapshot_at);
      setState("ready");
      setError("");
    }).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "加载失败");
      setState("error");
    });
  }, []);
  useEffect(() => { void load(); }, [load]);

  if (state === "error" && !payload) return <section className="console-card page-state page-state-error" role="alert">{error}<button type="button" className="secondary-button" onClick={() => void load()}>重试</button></section>;
  if (!payload) return <section className="console-card page-state" role="status">正在加载…</section>;

  const { service, system_health: systemHealth, summary, components, queues, dispatcher_queues: dispatcherQueues, connectors, email, wechat } = payload;
  const emailRows = email.entries;
  const connectorRows = Object.entries(connectors).map(([name, value]) => {
    return [name, <StatusBadge value={value.state} key="state" />, displayValue(value.reason_code || value.detail || "未提供")];
  });
  const wechatRows = [
    ["Reader IPC", <StatusBadge value={wechat.reader.status} key="status" />, wechat.reader.enabled ? "enabled" : "disabled"],
    ["Sender IPC", <StatusBadge value={wechat.sender.status} key="status" />, wechat.sender.enabled ? "enabled" : "disabled"],
    ["Sender preflight", <StatusBadge value={wechat.preflight.status} key="status" />, displayValue(wechat.preflight.error)],
    ["Account", <StatusBadge value={wechat.account.ready ? "ready" : "not ready"} key="status" />, displayValue(wechat.account.account_id)],
  ];
  return <>
    <div className="status-panel-toolbar"><SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} /><button type="button" className="secondary-button" onClick={() => void load()} disabled={state === "loading"}>{state === "loading" ? "刷新中…" : "刷新"}</button></div>
    <section className="status-metric-grid">
      <StatusMetric label="Service" value={displayValue(service.state || "unknown")} detail={displayValue(service.detail || "-")} tone={service.ok === false ? "bad" : "good"} />
      <StatusMetric label="System health" value={displayValue(systemHealth.state || "unavailable")} detail={displayValue(systemHealth.detail || "-")} tone={systemHealth.state === "healthy" ? "good" : systemHealth.state === "observing" ? "warning" : "bad"} />
      <StatusMetric label="PID" value={displayValue(service.pid)} detail={`runs ${displayValue(service.runs)}`} />
      <StatusMetric label="Processing" value={displayValue(summary.processing)} detail="all queues" />
      <StatusMetric label="Retryable" value={displayValue(summary.retryable)} detail="waiting for dependency" />
      <StatusMetric label="Failed" value={displayValue(summary.failed)} detail="current queue status" tone={Number(summary.failed || 0) ? "bad" : "good"} />
    </section>
    <StatusSection title="Runtime Monitor">
      <StatusTable headers={["Worker", "Status", "Role", "Cadence", "Latest tick", "Latest error"]} mobileLabels={["Worker", "Status", "Role", "Cadence", "Latest tick", "Latest error"]} rows={components.map((item) => [displayValue(item.name), <StatusBadge value={displayValue(item.status)} key="status" />, displayValue(item.role), displayValue(item.cadence), displayValue(item.latest_tick_at || "-"), displayValue(item.latest_error ? `${item.latest_error}${item.latest_error_at ? ` · ${item.latest_error_at}` : ""}` : "-")])} />
    </StatusSection>
    <StatusSection title="Connector health">
      <StatusTable headers={["Connector", "Status", "Reason"]} mobileLabels={["Connector", "Status", "Reason"]} rows={connectorRows} />
      {wechatRows.length > 0 && <div className="status-table-secondary"><StatusTable headers={["Check", "Status", "Detail"]} mobileLabels={["Check", "Status", "Detail"]} rows={wechatRows} /></div>}
    </StatusSection>
    <StatusSection title="Email worker">
      <StatusTable headers={["Scope", "Status", "Detail", "Updated"]} mobileLabels={["Scope", "Status", "Detail", "Updated"]} rows={emailRows.map((item) => [displayValue(item.scope), <StatusBadge value={displayValue(item.status)} key="status" />, displayValue(item.error_code || (item.accounts !== undefined ? `accounts ${item.accounts}` : item.failures !== undefined ? `failures ${item.failures}` : "-")), displayValue(item.updated_at || "-")])} />
    </StatusSection>
    <StatusSection title="Queues">
      <StatusTable headers={["Queue", "Status counts", "Pending", "Processing", "Retryable", "Failed", "Updated", "Latest error"]} mobileLabels={["Queue", "Status counts", "Pending", "Processing", "Retryable", "Failed", "Updated", "Latest error"]} rows={queues.map((item) => [<><strong>{displayValue(item.name)}</strong><small className="table-subtitle">{displayValue(item.table)}</small></>, displayValue(item.counts), displayValue(item.pending), displayValue(item.processing), displayValue(item.retryable), displayValue(item.failed), displayValue(item.latest_updated_at), displayValue(item.latest_error || "-")])} />
    </StatusSection>
    <StatusSection title="Dispatcher queues">
      <StatusTable headers={["Adapter", "Pending", "Due", "Oldest", "Running", "Latest error"]} mobileLabels={["Adapter", "Pending", "Due", "Oldest", "Running", "Latest error"]} rows={dispatcherQueues.map((item) => [displayValue(item.name), displayValue(item.pending), displayValue(item.due), displayValue(item.oldest_available_at || "-"), displayValue(item.running), displayValue(item.latest_error || "-")])} />
    </StatusSection>
    {state === "error" && <p className="inline-alert" role="alert">刷新失败，页面继续显示上一份快照：{error}</p>}
  </>;
}

export function StatusPage() {
  return <ConsolePageLayout title="Status"><StatusPanel /></ConsolePageLayout>;
}
