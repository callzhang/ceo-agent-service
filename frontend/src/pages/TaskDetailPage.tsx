import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { displayValue, getTaskDetail, type TaskDetail } from "../api/console";
import { ResponsiveDataList } from "../components/data/ResponsiveDataList";
import { SummaryText } from "../components/data/SummaryText";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { StatusBadge } from "../components/status/StatusBadge";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

function sourceLabel(source: unknown) {
  const value = displayValue(source);
  return value.split(/[\\/]/).filter(Boolean).at(-1) || value;
}

export function TaskDetailPage({ projectId }: { projectId: string }) {
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("");
  const [snapshot, setSnapshot] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    getTaskDetail(projectId, controller.signal).then((response) => {
      setTask(response.item);
      setSnapshot(response.meta.snapshot_at);
      setState("ready");
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      setMessage(error instanceof Error ? error.message : "加载失败");
      setState("error");
    });
    return () => controller.abort();
  }, [projectId]);

  if (state === "loading") return <ConsolePageLayout title={`Task ${projectId}`}><div className="console-card page-state" role="status">正在加载…</div></ConsolePageLayout>;
  if (state === "error" || !task) return <ConsolePageLayout title={`Task ${projectId}`}><div className="console-card page-state page-state-error" role="alert">{message || "任务不存在"}</div></ConsolePageLayout>;

  const facts = task.facts.map((fact) => ({ ...fact, id: String(fact.id) }));
  return (
    <ConsolePageLayout title={task.title} actions={<><SnapshotBadge timestamp={snapshot} /><Link className="secondary-button" to="/tasks">返回 Tasks</Link></>}>
      <section className="console-card task-overview">
        <div className="task-overview-meta"><StatusBadge value={task.status} /><span>{task.category}</span><span>Priority: {task.priority}</span><span>Owner: {task.owner || "未提供"}</span></div>
        <h2>Project details</h2>
        <dl className="detail-definition-list">
          <div><dt>说明</dt><dd><SummaryText value={task.description} /></dd></div>
          <div><dt>背景</dt><dd><SummaryText value={task.background} /></dd></div>
          <div><dt>Blocker</dt><dd><SummaryText value={task.blocker} /></dd></div>
          <div><dt>Next step</dt><dd><SummaryText value={task.next_summary} /></dd></div>
        </dl>
      </section>
      <section className="console-card task-facts-section">
        <h2>Facts</h2>
        <ResponsiveDataList
          ariaLabel="项目事实"
          columns={[{ key: "description", label: "Description" }, { key: "source", label: "Source" }, { key: "created", label: "Created" }, { key: "updated", label: "Updated" }]}
          rows={facts}
          renderCell={(fact, key) => key === "source" ? <span title={displayValue(fact.source)}>{sourceLabel(fact.source)}</span> : key === "description" ? <SummaryText value={displayValue(fact.description)} /> : fact[key] || "未提供"}
          expandable
          renderExpanded={(fact) => <div><strong>完整描述</strong><p>{displayValue(fact.description)}</p><strong>完整来源</strong><p>{displayValue(fact.source)}</p></div>}
        />
      </section>
      <section className="console-card">
        <h2>TODOs</h2>
        {task.todos.length ? <pre className="technical-details">{task.todos.map((todo) => displayValue(todo)).join("\n")}</pre> : <p className="muted">No TODOs recorded.</p>}
      </section>
      <section className="console-card">
        <h2>Updates</h2>
        {task.updates.length ? <pre className="technical-details">{task.updates.map((update) => displayValue(update)).join("\n")}</pre> : <p className="muted">No updates recorded.</p>}
      </section>
    </ConsolePageLayout>
  );
}
