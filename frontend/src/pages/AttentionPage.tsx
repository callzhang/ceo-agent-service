import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { displayValue, listAttention, type AttentionItem } from "../api/console";
import { SummaryText } from "../components/data/SummaryText";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { StatusBadge } from "../components/status/StatusBadge";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

export function AttentionPanel({ onCountChange }: { onCountChange?: (count: number) => void } = {}) {
  const [rows, setRows] = useState<AttentionItem[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const unresolvedCount = rows.reduce((total, row) => total + Math.max(0, row.count), 0);
  const load = useCallback(() => {
    setState("loading");
    setError("");
    return listAttention().then((page) => {
      setRows(page.items);
      onCountChange?.(page.items.reduce((total, row) => total + Math.max(0, row.count), 0));
      setSnapshot(page.meta.snapshot_at);
      setState("ready");
    }).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "加载失败");
      setState("error");
    });
  }, [onCountChange]);
  useEffect(() => { void load(); }, [load]);

  return (
    <section className="console-card attention-panel">
      <div className="attention-panel-header">
        <div>
          <p className="eyebrow">ACTION REQUIRED</p>
          <h2>待处理问题</h2>
          <p className="muted">按根因聚合未解决问题；摘要先展示影响，完整错误和命令放在详情中。</p>
        </div>
        <div className="attention-panel-actions">
          <div className="attention-snapshot"><SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} /><button type="button" className="secondary-button" onClick={() => void load()} disabled={state === "loading"}>{state === "loading" ? "刷新中…" : "刷新"}</button></div>
          {rows.length > 0 && <div className="attention-count-summary"><span className="attention-count-badge" aria-label={`${unresolvedCount} 个未解决问题`}>{unresolvedCount}</span><span>个未解决</span></div>}
        </div>
      </div>
      {state === "error" && <div className="page-state page-state-error" role="alert">{error}</div>}
      {state === "loading" && rows.length === 0 && <div className="page-state" role="status">正在加载…</div>}
      {state === "ready" && rows.length === 0 && <div className="page-state">当前没有待处理问题</div>}
      {rows.length > 0 && <div className="attention-list" role="list" aria-label="待处理问题">
        {rows.map((row) => {
          const expanded = expandedId === row.id;
          const detail = displayValue(row.detail);
          const fallbackError = displayValue(row.error);
          const detailValue = detail === "未提供" ? fallbackError : detail;
          const detailLabel = detail === "未提供" && fallbackError !== "未提供"
            ? "错误"
            : (displayValue(row.detail_label) || "状态");
          return <article className={`attention-card${expanded ? " is-expanded" : ""}`} key={row.id} role="listitem">
            <div className="attention-card-head">
              <div className="attention-card-title"><span className="attention-category">{displayValue(row.category)}</span><StatusBadge value={row.severity} /></div>
              <span className="attention-group-count" aria-label={`${row.count} 个同类问题`}>{row.count}</span>
            </div>
            <div className="attention-card-body">
              <div className="attention-root-cause"><span>根因</span><strong>{displayValue(row.root_cause) || "未分类"}</strong></div>
              <SummaryText value={displayValue(row.summary)} lines={2} label="展开摘要" />
            </div>
            <div className="attention-card-foot"><span>最近更新 · {displayValue(row.updated_at)}</span><button type="button" className="details-toggle" aria-expanded={expanded} onClick={() => setExpandedId(expanded ? null : row.id)}>{expanded ? "收起详情" : "查看详情"}</button></div>
            <div className="attention-details" hidden={!expanded}><p><strong>{detailLabel}：</strong>{detailValue === "未提供" ? "当前没有可展示的详情。" : detailValue}</p><div className="attention-links">{row.links.map((link) => <Link key={link.href} to={link.href}>{link.label}</Link>)}</div></div>
          </article>;
        })}
      </div>}
    </section>
  );
}

export function AttentionPage() {
  return <ConsolePageLayout title="Attention"><AttentionPanel /></ConsolePageLayout>;
}
