import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { listTasks, type TaskSummary } from "../api/console";
import { ResponsiveDataList } from "../components/data/ResponsiveDataList";
import { SummaryText } from "../components/data/SummaryText";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { StatusBadge } from "../components/status/StatusBadge";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

export function TasksPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [rows, setRows] = useState<TaskSummary[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const query = searchParams.get("q") || "";

  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    listTasks({ q: query }, controller.signal).then((page) => {
      setRows(page.items);
      setSnapshot(page.meta.snapshot_at);
      setState("ready");
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      setMessage(error instanceof Error ? error.message : "加载失败");
      setState("error");
    });
    return () => controller.abort();
  }, [query]);

  return (
    <ConsolePageLayout title="Tasks" actions={<SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} />}>
      <section className="console-card">
        <form className="console-filter-bar" onSubmit={(event) => event.preventDefault()}>
          <label htmlFor="task-search">搜索任务</label>
          <input id="task-search" value={query} placeholder="搜索项目、TODO 或下一步" onChange={(event) => {
            const next = new URLSearchParams(searchParams);
            if (event.target.value) next.set("q", event.target.value); else next.delete("q");
            setSearchParams(next);
          }} />
        </form>
        <ResponsiveDataList
          ariaLabel="任务列表"
          columns={[
            { key: "title", label: "Project" },
            { key: "status", label: "Status" },
            { key: "priority", label: "Priority" },
            { key: "owner", label: "Owner" },
            { key: "progress", label: "Progress" },
            { key: "todo_count", label: "ToDos" },
          ]}
          rows={rows}
          state={state === "loading" ? "loading" : state === "error" ? "error" : rows.length ? "ready" : "empty"}
          errorMessage={message}
          renderCell={(row, key) => {
            if (key === "title") return <Link to={`/tasks/${row.id}`} aria-label={`查看详情 ${row.title}`}>{row.title}</Link>;
            if (key === "status") return <StatusBadge value={row.status} />;
            if (key === "todo_count") return `${row.todo_count} 个 TODO`;
            if (key === "progress") return row.progress || "未提供";
            return row[key] || "未提供";
          }}
          expandable
          renderExpanded={(row) => <div><SummaryText value={row.state_summary} /><SummaryText value={row.next_summary} /></div>}
        />
      </section>
      <section className="console-card console-card-muted">
        <h2>Sent TODOs</h2>
        <p>已发送的 DingTalk Todo 和 follow-up 会在详情中显示。</p>
      </section>
    </ConsolePageLayout>
  );
}
