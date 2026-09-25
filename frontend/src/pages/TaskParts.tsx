import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import type { BusinessTaskSummary } from "../api/console";
import { TaskTime } from "./TaskTime";
import { commitmentText, labelOf, taskStatusLabels } from "./taskLabels";

export function TaskSkeleton() {
  return <div className="business-skeleton" role="status" aria-label="正在加载"><span /><span /><span /></div>;
}

/** A card whose body folds away; the count stays visible so a closed section still says how much is inside. */
export function DetailSection({ title, count, open = true, children }: { title: string; count?: number; open?: boolean; children: ReactNode }) {
  return <details className="console-card business-detail-section business-fold" open={open}>
    <summary><h2>{title}{count !== undefined && <span className="business-fold-count">{count}</span>}</h2></summary>
    {children}
  </details>;
}

/** Linked Tasks with what a reader needs to judge each one without opening it. */
export function LinkedTaskList({ tasks }: { tasks: BusinessTaskSummary[] }) {
  return <ul className="business-linked-list">{tasks.map((task) => {
    const facts = [
      task.owner || "负责人未明确",
      task.status !== "open" ? labelOf(taskStatusLabels, task.status) : "",
      commitmentText(task.status, task.commitment_status),
      task.deadline_at ? `截止 ${task.deadline_at}` : "",
    ].filter(Boolean);
    return <li key={task.id}><Link to={task.detail_url}>{task.title}</Link><span>{facts.join(" · ")} · <TaskTime value={task.updated_at} /></span></li>;
  })}</ul>;
}
