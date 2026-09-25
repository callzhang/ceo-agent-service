import { EyeOff, Undo2 } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Link } from "react-router-dom";

import { decideCandidateTask, type BusinessTaskSummary } from "../api/console";
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
      task.owner,
      task.status !== "open" ? labelOf(taskStatusLabels, task.status) : "",
      commitmentText(task.status, task.commitment_status),
      task.deadline_at ? `截止 ${task.deadline_at}` : "",
    ].filter(Boolean);
    return <li key={task.id}><Link to={task.detail_url}>{task.title}</Link><span>{facts.join(" · ")} · <TaskTime value={task.updated_at} /></span></li>;
  })}</ul>;
}

/**
 * Set a candidate aside (标为已取消, nothing is deleted) or take that back. Only candidates
 * get the button: a formal Task has an owner and its own evidence.
 */
export function CandidateAction({ task, onDone, showLabel = false }: { task: Pick<BusinessTaskSummary, "id" | "title" | "stage" | "status">; onDone: () => void; showLabel?: boolean }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  if (task.stage !== "candidate") return null;
  const ignored = task.status === "cancelled";
  if (!ignored && task.status !== "open" && task.status !== "waiting") return null;
  const label = ignored ? "恢复" : "忽略";
  const run = () => {
    setPending(true);
    setError("");
    decideCandidateTask(task.id, ignored ? "restore" : "ignore")
      .then(onDone)
      .catch((reason: unknown) => { setError(reason instanceof Error ? reason.message : "操作失败"); onDone(); })
      .finally(() => setPending(false));
  };
  const Icon = ignored ? Undo2 : EyeOff;
  return <>
    <button type="button" className={`secondary-button candidate-action${showLabel ? "" : " is-icon"}`} disabled={pending} onClick={run}
      aria-label={`${label} ${task.title}`} title={ignored ? "恢复为待处理的候选任务" : "这不是任务：标为已取消，记录都会保留，可随时恢复"}>
      <Icon size={14} aria-hidden="true" />{showLabel && (pending ? "处理中…" : label)}
    </button>
    {error && <small role="alert" className="follow-up-error">{error}</small>}
  </>;
}
