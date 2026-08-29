import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { approveWechatDelivery, displayValue, listCodexSessions, listWechat, rejectWechatDelivery, reviewWechatMemory } from "../api/console";
import { ResponsiveDataList } from "../components/data/ResponsiveDataList";
import { SummaryText } from "../components/data/SummaryText";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";
import { StatusBadge } from "../components/status/StatusBadge";

function NotificationStatusPanel() {
  const [permission, setPermission] = useState("检查中");
  const [worker, setWorker] = useState("检查中");

  useEffect(() => {
    if ("Notification" in window) {
      setPermission(Notification.permission === "granted" ? "已允许" : Notification.permission === "denied" ? "已拒绝" : "未设置");
    } else {
      setPermission("浏览器不支持");
    }
    if (!("serviceWorker" in navigator)) {
      setWorker("浏览器不支持");
      return;
    }
    navigator.serviceWorker.register("/notification-service-worker.js")
      .then(() => navigator.serviceWorker.ready)
      .then(() => setWorker("已连接"))
      .catch(() => setWorker("连接失败"));
  }, []);

  return <section className="console-card notification-status-panel" aria-labelledby="notification-status-title">
    <div className="settings-card-heading">
      <div><p className="eyebrow">DELIVERY STATUS</p><h2 id="notification-status-title">浏览器通知状态</h2></div>
      <span className="console-card-muted">不会自动申请通知权限</span>
    </div>
    <div className="notification-status-grid">
      <div className="notification-status-item"><span>通知权限</span><strong>{permission}</strong><small>需要在浏览器设置中允许后，才能显示桌面提醒。</small></div>
      <div className="notification-status-item"><span>Service Worker</span><strong>{worker}</strong><small>负责接收事件并处理通知点击跳转。</small></div>
    </div>
    <p className="field-help" aria-live="polite">实时事件流仍由本地服务维护；本页仅展示连接与权限状态。</p>
  </section>;
}

export function DomainListPage({ title, endpoint, kind = "resource", showNotificationStatus = false }: { title: string; endpoint: string; kind?: "resource" | "codex" | "wechat"; showNotificationStatus?: boolean }) {
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [snapshot, setSnapshot] = useState("");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    const promise = kind === "codex" ? listCodexSessions(controller.signal) : listWechat(endpoint, controller.signal);
    promise.then((page) => { setRows(page.items as Record<string, unknown>[]); setSnapshot(page.meta.snapshot_at); setState("ready"); }).catch((reason: unknown) => { if (controller.signal.aborted) return; setError(reason instanceof Error ? reason.message : "加载失败"); setState("error"); });
    return () => controller.abort();
  }, [endpoint, kind]);
  const columns = kind === "codex"
    ? [{ key: "title", label: "会话" }, { key: "type", label: "类型" }, { key: "detail_url", label: "详情" }]
    : [{ key: "status", label: "状态" }, { key: "target_type", label: "对象" }, { key: "target_id", label: "目标" }, { key: "reply_text", label: "摘要" }, { key: "error", label: "诊断" }];
  return <ConsolePageLayout title={title} actions={<SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} />}>
    {showNotificationStatus && <NotificationStatusPanel />}
    <section className="console-card">
      <ResponsiveDataList
        ariaLabel={title}
        columns={columns as never}
        rows={rows.map((row, index) => ({ id: String(row.id ?? row.session_id ?? index), ...row })) as never}
        state={state === "loading" ? "loading" : state === "error" ? "error" : rows.length ? "ready" : "empty"}
        errorMessage={error}
        emptyMessage="暂无记录"
        renderCell={(row: Record<string, unknown>, key: string) => {
          if (key === "title") return row.detail_url ? <Link to={String(row.detail_url)}>{displayValue(row.title)}</Link> : displayValue(row.title);
          if (key === "detail_url") return row.detail_url ? <Link to={String(row.detail_url)}>查看详情</Link> : "未提供";
          if (key === "status") return <StatusBadge value={displayValue(row.status)} />;
          return key === "reply_text" || key === "error" ? <SummaryText value={displayValue(row[key])} /> : displayValue(row[key]);
        }}
        expandable
        renderExpanded={(row: Record<string, unknown>) => <div><pre className="technical-details">{JSON.stringify(row, null, 2)}</pre>{kind === "wechat" && Boolean(row.id) && <div className="console-page-actions">{endpoint.includes("memory-review") ? <><button type="button" className="secondary-button" onClick={() => void reviewWechatMemory(String(row.id), "approve", displayValue(row.edited_statement || row.statement))}>批准</button><button type="button" className="secondary-button" onClick={() => void reviewWechatMemory(String(row.id), "reject")}>拒绝</button>{row.status === "approved" && <button type="button" className="secondary-button" onClick={() => void reviewWechatMemory(String(row.id), "revoke")}>撤销批准</button>}</> : row.status === "ready_to_send" ? <><button type="button" className="secondary-button" onClick={() => void approveWechatDelivery(String(row.id))}>发送/批准</button><button type="button" className="secondary-button" onClick={() => void rejectWechatDelivery(String(row.id))}>拒绝</button></> : null}</div>}</div>}
      />
    </section>
  </ConsolePageLayout>;
}
