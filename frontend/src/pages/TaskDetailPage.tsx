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
  const path = value.split("#", 1)[0];
  return path.split(/[\\/]/).filter(Boolean).at(-1) || path || value;
}

type DetailRow = Record<string, unknown> & { id: string };

function detailRows(values: Array<Record<string, unknown>>, prefix: string): DetailRow[] {
  return values.map((value, index) => ({
    ...value,
    id: String(value.id || `${prefix}-${index + 1}`),
  }));
}

function localTime(value: unknown) {
  const text = typeof value === "string" ? value : "";
  if (!text) return "未提供";
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? text : date.toLocaleString();
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
  const todos = detailRows(task.todos, "todo");
  const updates = detailRows(task.updates, "update");
  const memory = detailRows(task.memory, "memory");
  const unlinkedFollowUps = detailRows(task.unlinked_follow_ups ?? [], "follow-up");
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
      <section className="console-card task-todos-section">
        <h2>TODOs</h2>
        {todos.length ? <ResponsiveDataList<DetailRow>
          ariaLabel="项目 TODO"
          columns={[{ key: "title", label: "TODO" }, { key: "status", label: "Status" }, { key: "owner_name", label: "Owner" }, { key: "priority", label: "Priority" }, { key: "deadline_at", label: "Deadline" }]}
          rows={todos}
          renderCell={(todo, key) => key === "title" ? <SummaryText value={displayValue(todo.title)} /> : key === "status" ? <StatusBadge value={displayValue(todo.status)} /> : key === "owner_name" ? displayValue(todo.owner || todo.owner_name || todo.owner_user_id) : key === "deadline_at" ? localTime(todo.deadline_at) : displayValue(todo[key])}
          expandable
          renderExpanded={(todo) => <div className="task-record-details"><strong>说明</strong><p>{displayValue(todo.description)}</p><strong>Blocker</strong><p>{displayValue(todo.blocker)}</p><strong>跟进问题</strong><p>{displayValue(todo.follow_up_question)}</p><details><summary>技术详情</summary><pre className="technical-details">{JSON.stringify(todo, null, 2)}</pre></details></div>}
        /> : <p className="muted">No TODOs recorded.</p>}
      </section>
      <section className="console-card task-updates-section">
        <h2>Updates</h2>
        {updates.length ? <ResponsiveDataList<DetailRow>
          ariaLabel="项目更新"
          columns={[{ key: "summary", label: "Summary" }, { key: "source_type", label: "Source" }, { key: "confidence", label: "Confidence" }, { key: "created_at", label: "Created" }]}
          rows={updates}
          renderCell={(update, key) => key === "summary" ? <SummaryText value={displayValue(update.summary)} /> : key === "source_type" ? <span title={displayValue(update.source_ref)}>{displayValue(update.source_type)} · {sourceLabel(update.source_ref)}</span> : key === "confidence" ? `${Math.round(Number(update.confidence || 0) * 100)}%` : key === "created_at" ? localTime(update.created_at) : displayValue(update[key])}
          expandable
          renderExpanded={(update) => <div className="task-record-details"><strong>合并原因</strong><p>{displayValue(update.merge_reason)}</p><strong>完整来源</strong><p>{displayValue(update.source_ref)}</p><details><summary>Changes</summary><pre className="technical-details">{JSON.stringify(update.changes || {}, null, 2)}</pre></details></div>}
        /> : <p className="muted">No updates recorded.</p>}
      </section>
      <section className="console-card task-memory-section">
        <h2>Memory context</h2>
        {memory.length ? <ResponsiveDataList<DetailRow>
          ariaLabel="项目记忆上下文"
          columns={[{ key: "summary", label: "Summary" }, { key: "source", label: "Source" }]}
          rows={memory}
          renderCell={(item, key) => key === "summary" ? <SummaryText value={displayValue(item.summary || item.content || item.text || item)} /> : <span title={displayValue(item.source)}>{sourceLabel(item.source)}</span>}
          expandable
          renderExpanded={(item) => <details open><summary>完整上下文</summary><pre className="technical-details">{JSON.stringify(item, null, 2)}</pre></details>}
        /> : <p className="muted">No memory context recorded.</p>}
      </section>
      <section className="console-card task-follow-ups-section">
        <h2>Unlinked follow-ups</h2>
        {unlinkedFollowUps.length ? <ResponsiveDataList<DetailRow>
          ariaLabel="未关联跟进"
          columns={[{ key: "summary", label: "Summary" }, { key: "status", label: "Status" }]}
          rows={unlinkedFollowUps}
          renderCell={(item, key) => key === "status" ? <StatusBadge value={displayValue(item.status)} /> : <SummaryText value={displayValue(item.summary || item.title || item.description)} />}
          expandable
          renderExpanded={(item) => <details open><summary>完整跟进</summary><pre className="technical-details">{JSON.stringify(item, null, 2)}</pre></details>}
        /> : <p className="muted">No unlinked follow-ups recorded.</p>}
      </section>
    </ConsolePageLayout>
  );
}
