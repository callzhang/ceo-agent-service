import { Archive, Check, Pencil, X } from "lucide-react";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { Virtuoso } from "react-virtuoso";

import { parseWorkbenchTimestamp, taskStateLabel } from "../presentation";
import type { Task } from "../types";
import { SearchField } from "./filters/SearchField";

interface TaskListProps {
  tasks: Task[];
  activeTaskId: string | null;
  onSelect: (taskId: string) => void;
  onNewTask: () => void;
  onRename: (taskId: string, title: string) => void | Promise<void>;
  onArchive: (taskId: string) => void | Promise<void>;
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
  pendingOperations?: Record<string, "rename" | "archive">;
  pendingFeedbackCount?: number;
  onProcessFeedback?: () => void;
}

interface TaskGroup {
  key: "today" | "yesterday" | "earlier";
  label: string;
  tasks: Task[];
}

function localDay(value: string): number | null {
  const parsed = parseWorkbenchTimestamp(value);
  if (!parsed) return null;
  return new Date(parsed.getFullYear(), parsed.getMonth(), parsed.getDate()).getTime();
}

function groupTasks(tasks: Task[]): TaskGroup[] {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1).getTime();
  const groups: TaskGroup[] = [
    { key: "today", label: "今天", tasks: [] },
    { key: "yesterday", label: "昨天", tasks: [] },
    { key: "earlier", label: "更早", tasks: [] },
  ];

  for (const task of tasks) {
    const day = localDay(task.updated_at);
    const index = day === today ? 0 : day === yesterday ? 1 : 2;
    groups[index].tasks.push(task);
  }
  return groups.filter((group) => group.tasks.length > 0);
}

function activityDetails(value: string, groupKey: TaskGroup["key"]) {
  const parsed = parseWorkbenchTimestamp(value);
  if (!parsed) return null;
  const formatter = groupKey === "earlier"
    ? new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" })
    : new Intl.DateTimeFormat("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
  return { dateTime: parsed.toISOString(), label: formatter.format(parsed) };
}

function millisecondsUntilLocalMidnight(now: Date) {
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  return Math.max(1, midnight.getTime() - now.getTime());
}

export function TaskList({
  tasks,
  activeTaskId,
  onSelect,
  onNewTask,
  onRename,
  onArchive,
  hasMore = false,
  loadingMore = false,
  onLoadMore,
  pendingOperations = {},
  pendingFeedbackCount = 0,
  onProcessFeedback = () => undefined,
}: TaskListProps) {
  const [query, setQuery] = useState("");
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [dayEpoch, setDayEpoch] = useState(0);
  useEffect(() => {
    const timer = window.setTimeout(
      () => setDayEpoch((current) => current + 1),
      millisecondsUntilLocalMidnight(new Date()),
    );
    return () => window.clearTimeout(timer);
  }, [dayEpoch]);
  const groups = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    const visibleTasks = normalizedQuery
      ? tasks.filter((task) => task.title.toLocaleLowerCase().includes(normalizedQuery))
      : tasks;
    return groupTasks(visibleTasks);
  }, [dayEpoch, query, tasks]);

  function beginRename(task: Task) {
    setEditingTaskId(task.id);
    setDraftTitle(task.title);
  }

  function submitRename(event: FormEvent<HTMLFormElement>, taskId: string) {
    event.preventDefault();
    const title = draftTitle.trim();
    if (!title) {
      return;
    }
    void onRename(taskId, title);
    setEditingTaskId(null);
  }

  function requestArchive(task: Task) {
    if (window.confirm(`归档“${task.title}”？归档后它将从当前任务列表中移除。`)) {
      void onArchive(task.id);
    }
  }

  function renderTask(task: Task, groupKey: TaskGroup["key"], virtualized = false) {
    const pendingOperation = pendingOperations[task.id];
    const activity = activityDetails(task.updated_at, groupKey);
    return (
      <article
        className="task-item"
        aria-busy={pendingOperation ? true : undefined}
        data-active={task.id === activeTaskId}
        data-group={groupKey}
        data-testid={virtualized ? "virtual-task-row" : undefined}
      >
        {editingTaskId === task.id ? (
          <form className="rename-form" onSubmit={(event) => submitRename(event, task.id)}>
            <label>
              <span className="sr-only">任务名称</span>
              <input
                aria-label="任务名称"
                autoFocus
                maxLength={200}
                required
                value={draftTitle}
                onChange={(event) => setDraftTitle(event.target.value)}
              />
            </label>
            <button type="submit" aria-label="保存名称">
              <Check aria-hidden="true" size={15} />
            </button>
            <button type="button" aria-label="取消重命名" onClick={() => setEditingTaskId(null)}>
              <X aria-hidden="true" size={15} />
            </button>
          </form>
        ) : (
          <>
            <button
              className="task-select"
              type="button"
              aria-label={`打开任务 ${task.title}`}
              aria-current={task.id === activeTaskId ? "page" : undefined}
              onClick={() => onSelect(task.id)}
            >
              <span className="task-title">{task.title}</span>
              <span className="task-meta">
                <span className={`task-state task-state-${task.state}`}>
                  {taskStateLabel(task.state)}
                </span>
                {activity ? (
                  <time className="task-activity" dateTime={activity.dateTime}>{activity.label}</time>
                ) : (
                  <span className="task-activity">时间未知</span>
                )}
              </span>
            </button>
            <div className="task-actions">
              {pendingOperation && (
                <span className="task-pending" role="status">
                  {pendingOperation === "rename" ? "保存中…" : "归档中…"}
                </span>
              )}
              <button
                type="button"
                aria-label={`重命名 ${task.title}`}
                disabled={pendingOperation === "archive"}
                onClick={() => beginRename(task)}
              >
                <Pencil aria-hidden="true" size={14} />
              </button>
              <button
                type="button"
                aria-label={`归档 ${task.title}`}
                disabled={Boolean(pendingOperation)}
                onClick={() => requestArchive(task)}
              >
                <Archive aria-hidden="true" size={14} />
              </button>
            </div>
          </>
        )}
      </article>
    );
  }

  const virtualRows = groups.flatMap((group) => [
    { kind: "group" as const, key: group.key, label: group.label },
    ...group.tasks.map((task) => ({ kind: "task" as const, key: group.key, task })),
  ]);
  const useVirtualList = virtualRows.length > 80;

  return (
    <div className="task-list">
      <button className="primary-button new-task-button" type="button" onClick={onNewTask}>
        新任务
      </button>
      <button className="secondary-button feedback-button" type="button" onClick={onProcessFeedback}>
        处理反馈 · {pendingFeedbackCount}
      </button>
      <SearchField
        id="agent-task-search"
        label="搜索任务"
        placeholder="搜索任务"
        value={query}
        describedBy={hasMore ? "task-search-scope" : undefined}
        onChange={setQuery}
        onClear={() => setQuery("")}
      />
      {hasMore && <p className="search-scope" id="task-search-scope">仅搜索已加载的任务</p>}
      <div className="task-items">
        {useVirtualList ? (
          <Virtuoso
            className="task-virtuoso"
            data={virtualRows}
            initialItemCount={Math.min(30, virtualRows.length)}
            computeItemKey={(_index, row) => row.kind === "group" ? `group:${row.key}` : `task:${row.task.id}`}
            itemContent={(_index, row) =>
              row.kind === "group" ? (
                <h3 className="virtual-group-heading">{row.label}</h3>
              ) : (
                renderTask(row.task, row.key, true)
              )
            }
          />
        ) : (
          groups.map((group) => (
            <section className="task-group" aria-labelledby={`task-group-${group.key}`} key={group.key}>
              <h3 id={`task-group-${group.key}`}>{group.label}</h3>
              {group.tasks.map((task) => <div key={task.id}>{renderTask(task, group.key)}</div>)}
            </section>
          ))
        )}
        {groups.length === 0 && <p className="task-list-empty">没有匹配的任务</p>}
      </div>
      {hasMore && (
        <button
          className="load-more-button secondary-button"
          type="button"
          disabled={loadingMore}
          onClick={onLoadMore}
          aria-label="加载更多任务"
        >
          {loadingMore ? "正在加载…" : "加载更多"}
        </button>
      )}
    </div>
  );
}
