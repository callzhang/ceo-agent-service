import { EyeOff, Undo2 } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Link } from "react-router-dom";

import { decideCandidateTask, type BusinessTaskSignal, type BusinessTaskSummary, type SourceCitation } from "../api/console";
import { TaskTime } from "./TaskTime";
import { commitmentText, isCurrentSuggestion, labelOf, taskDateText, taskOriginLabel, taskOwnerText, taskStatusLabels } from "./taskLabels";

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
      taskOwnerText(task),
      labelOf(taskStatusLabels, task.status),
      commitmentText(task.status, task.commitment_status),
      taskDateText(task),
      ...task.anchor_labels.map((label) => `项目：${label}`),
    ].filter(Boolean);
    return <li key={task.id}>
      <div className="business-task-row-main"><Link to={task.detail_url}>{task.title}</Link><span className={`business-stage ${task.stage}`}>{task.stage === "formal" ? "正式任务" : "候选任务"}</span><span className="business-origin">{taskOriginLabel(task)}</span></div>
      <div className="business-linked-facts">{facts.map((fact) => <span key={fact}>{fact}</span>)}<TaskTime value={task.updated_at} /></div>
      {isCurrentSuggestion(task) && task.suggestion_reason && <p className="business-suggestion-reason">{task.suggestion_reason}</p>}
    </li>;
  })}</ul>;
}

/** These are saved quotations, not a paraphrase or a new attribution. */
export function CitationList({ citations, signals = [] }: { citations: SourceCitation[]; signals?: BusinessTaskSignal[] }) {
  if (!citations.length) return null;
  return <ul className="business-citations">{citations.map((citation, index) => {
    const signal = signals.find((source) => source.id === citation.signal_id);
    const link = signal ? signalSourceLink(signal.context_json) : "";
    return <li key={`${citation.signal_id}-${citation.source_ref}-${index}`}>
      <blockquote>{citation.source_excerpt}</blockquote>
      <small>{link ? <a href={link} target="_blank" rel="noreferrer">来源：{citation.source_ref}</a> : <span>来源：{citation.source_ref}</span>}{signal?.source_time && <> · <time dateTime={signal.source_time}>{signal.source_time}</time></>}</small>
    </li>;
  })}</ul>;
}

export function signalSourceLink(contextJson: unknown): string {
  if (typeof contextJson !== "string") return "";
  try {
    const context: unknown = JSON.parse(contextJson);
    return context && typeof context === "object" && "source_link" in context && typeof context.source_link === "string" ? context.source_link : "";
  } catch { return ""; }
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
