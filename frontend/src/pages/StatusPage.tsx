import { useCallback, useEffect, useState, type ReactNode } from "react";

import { displayValue, getStatus } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";
import { StatusBadge } from "../components/status/StatusBadge";

type RecordValue = Record<string, unknown>;

function record(value: unknown): RecordValue {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as RecordValue : {};
}

function list(value: unknown): RecordValue[] {
  return Array.isArray(value) ? value.map(record) : [];
}

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
  const [payload, setPayload] = useState<RecordValue | null>(null);
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

  const service = record(payload.service);
  const systemHealth = record(payload.system_health);
  const summary = record(payload.summary);
  const components = list(payload.components);
  const queues = list(payload.queues);
  const connectors = record(payload.connectors);
  const wechat = record(payload.wechat);
  const connectorRows = Object.entries(connectors).map(([name, value]) => {
    const item = record(value);
    return [name, <StatusBadge value={displayValue(item.state)} key="state" />, displayValue(item.reason_code || item.detail || "未提供")];
  });
  const wechatRows = [
    ["Reader IPC", <StatusBadge value={displayValue(record(wechat.reader).status)} key="status" />, displayValue(record(wechat.reader).enabled ? "enabled" : "disabled")],
    ["Sender IPC", <StatusBadge value={displayValue(record(wechat.sender).status)} key="status" />, displayValue(record(wechat.sender).enabled ? "enabled" : "disabled")],
    ["Sender preflight", <StatusBadge value={displayValue(record(wechat.preflight).status)} key="status" />, displayValue(record(wechat.preflight).error)],
    ["Account", <StatusBadge value={displayValue(record(wechat.account).ready ? "ready" : "not ready")} key="status" />, displayValue(record(wechat.account).account_id)],
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
      <StatusTable headers={["Worker", "Role", "Cadence"]} mobileLabels={["Worker", "Role", "Cadence"]} rows={components.map((item) => [displayValue(item.name), displayValue(item.role), displayValue(item.cadence)])} />
    </StatusSection>
    <StatusSection title="Connector health">
      <StatusTable headers={["Connector", "Status", "Reason"]} mobileLabels={["Connector", "Status", "Reason"]} rows={connectorRows} />
      {wechatRows.length > 0 && <div className="status-table-secondary"><StatusTable headers={["Check", "Status", "Detail"]} mobileLabels={["Check", "Status", "Detail"]} rows={wechatRows} /></div>}
    </StatusSection>
    <StatusSection title="Queues">
      <StatusTable headers={["Queue", "Status counts", "Pending", "Processing", "Retryable", "Failed", "Updated", "Latest error"]} mobileLabels={["Queue", "Status counts", "Pending", "Processing", "Retryable", "Failed", "Updated", "Latest error"]} rows={queues.map((item) => [<><strong>{displayValue(item.name)}</strong><small className="table-subtitle">{displayValue(item.table)}</small></>, displayValue(item.counts), displayValue(item.pending), displayValue(item.processing), displayValue(item.retryable), displayValue(item.failed), displayValue(item.latest_updated_at), displayValue(item.latest_error || "-")])} />
    </StatusSection>
    {state === "error" && <p className="inline-alert" role="alert">刷新失败，页面继续显示上一份快照：{error}</p>}
  </>;
}

export function StatusPage() {
  return <ConsolePageLayout title="Status"><StatusPanel /></ConsolePageLayout>;
}
