import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { listHistory, displayValue, type HistoryItem } from "../api/console";
import { ResponsiveDataList } from "../components/data/ResponsiveDataList";
import { SummaryText } from "../components/data/SummaryText";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { StatusBadge } from "../components/status/StatusBadge";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

export function HistoryPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [rows, setRows] = useState<HistoryItem[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const query = searchParams.get("q") || "";

  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    listHistory({ q: query }, controller.signal).then((page) => {
      setRows(page.items);
      setSnapshot(page.meta.snapshot_at);
      setState("ready");
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : "加载失败");
      setState("error");
    });
    return () => controller.abort();
  }, [query]);

  return (
    <ConsolePageLayout title="History" actions={<SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} />}>
      <section className="console-card">
        <form className="console-filter-bar" onSubmit={(event) => event.preventDefault()}>
          <label htmlFor="history-search">搜索历史</label>
          <input id="history-search" value={query} placeholder="搜索业务标题、摘要或操作者" onChange={(event) => {
            const next = new URLSearchParams(searchParams);
            if (event.target.value) next.set("q", event.target.value); else next.delete("q");
            setSearchParams(next);
          }} />
        </form>
        <ResponsiveDataList
          ariaLabel="执行历史"
          columns={[{ key: "occurred_at", label: "时间" }, { key: "title", label: "业务标题" }, { key: "type", label: "类型" }, { key: "status", label: "状态" }, { key: "summary", label: "摘要" }, { key: "actor", label: "操作者" }]}
          rows={rows}
          state={state === "loading" ? "loading" : state === "error" ? "error" : rows.length ? "ready" : "empty"}
          errorMessage={error}
          renderCell={(row, key) => {
            if (key === "title") return <Link to={row.detail_url || `/attempts/${row.id}`}>{row.title}</Link>;
            if (key === "status") return <StatusBadge value={row.status} />;
            if (key === "summary") return <SummaryText value={displayValue(row.summary)} />;
            return row[key] || "未提供";
          }}
          expandable
          renderExpanded={(row) => <Link to={row.detail_url || `/attempts/${row.id}`}>查看业务详情与 Runtime details</Link>}
        />
      </section>
    </ConsolePageLayout>
  );
}
