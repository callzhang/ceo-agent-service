import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { displayValue, listAttention, type AttentionItem } from "../api/console";
import { ResponsiveDataList } from "../components/data/ResponsiveDataList";
import { SummaryText } from "../components/data/SummaryText";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { StatusBadge } from "../components/status/StatusBadge";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

export function AttentionPanel() {
  const [rows, setRows] = useState<AttentionItem[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const load = useCallback(() => {
    setState("loading");
    return listAttention().then((page) => {
      setRows(page.items);
      setSnapshot(page.meta.snapshot_at);
      setState("ready");
    }).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "加载失败");
      setState("error");
    });
  }, []);
  useEffect(() => { void load(); }, [load]);

  return (
      <section className="console-card attention-panel">
        <div className="status-panel-toolbar"><SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} /><button type="button" className="secondary-button" onClick={() => void load()} disabled={state === "loading"}>{state === "loading" ? "刷新中…" : "刷新"}</button></div>
        <p className="muted">当前未解决问题按根因聚合；完整错误和命令只在展开详情中显示。</p>
        <ResponsiveDataList
          ariaLabel="待处理问题"
          columns={[{ key: "category", label: "类别" }, { key: "severity", label: "严重程度" }, { key: "count", label: "数量" }, { key: "summary", label: "摘要" }, { key: "updated_at", label: "更新时间" }]}
          rows={rows}
          state={state === "loading" ? "loading" : state === "error" ? "error" : rows.length ? "ready" : "empty"}
          errorMessage={error}
          emptyMessage="当前没有待处理问题"
          renderCell={(row, key) => key === "count" ? `${row.count} 项` : key === "severity" ? <StatusBadge value={row.severity} /> : key === "summary" ? <SummaryText value={displayValue(row.summary)} /> : displayValue(row[key])}
          expandable
          renderExpanded={(row) => <div className="attention-details"><p><strong>根因：</strong>{row.root_cause || "未分类"}</p><p><strong>错误：</strong>{displayValue(row.error)}</p><div className="attention-links">{row.links.map((link) => <Link key={link.href} to={link.href}>{link.label}</Link>)}</div></div>}
        />
      </section>
  );
}

export function AttentionPage() {
  return <ConsolePageLayout title="Attention"><AttentionPanel /></ConsolePageLayout>;
}
