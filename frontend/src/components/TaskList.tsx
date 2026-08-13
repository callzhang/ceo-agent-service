import { Archive, Check, Pencil, Search, X } from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";

import type { Task, TaskState } from "../types";

const stateLabels: Record<TaskState, string> = {
  idle: "空闲",
  queued: "等待中",
  running: "执行中",
  waiting_confirmation: "等待确认",
  completed: "已完成",
  stopped: "已停止",
  failed: "失败",
};

interface TaskListProps {
  tasks: Task[];
  activeTaskId: string | null;
  onSelect: (taskId: string) => void;
  onNewTask: () => void;
  onRename: (taskId: string, title: string) => void | Promise<void>;
  onArchive: (taskId: string) => void | Promise<void>;
}

interface TaskGroup {
  key: "today" | "yesterday" | "earlier";
  label: string;
  tasks: Task[];
}

function isBackendTimestamp(value: string): boolean {
  if (
    value.length !== 19 ||
    value[4] !== "-" ||
    value[7] !== "-" ||
    value[10] !== " " ||
    value[13] !== ":" ||
    value[16] !== ":"
  ) {
    return false;
  }
  for (const [index, character] of Array.from(value).entries()) {
    if ([4, 7, 10, 13, 16].includes(index)) continue;
    if (character < "0" || character > "9") return false;
  }
  return true;
}

function parseTimestamp(value: string): Date | null {
  if (isBackendTimestamp(value)) {
    const parts = [
      Number(value.slice(0, 4)),
      Number(value.slice(5, 7)),
      Number(value.slice(8, 10)),
      Number(value.slice(11, 13)),
      Number(value.slice(14, 16)),
      Number(value.slice(17, 19)),
    ];
    const parsed = new Date(`${value.slice(0, 10)}T${value.slice(11)}Z`);
    if (
      parsed.getUTCFullYear() !== parts[0] ||
      parsed.getUTCMonth() + 1 !== parts[1] ||
      parsed.getUTCDate() !== parts[2] ||
      parsed.getUTCHours() !== parts[3] ||
      parsed.getUTCMinutes() !== parts[4] ||
      parsed.getUTCSeconds() !== parts[5]
    ) {
      return null;
    }
    return parsed;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed;
}

function localDay(value: string): number | null {
  const parsed = parseTimestamp(value);
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

export function TaskList({
  tasks,
  activeTaskId,
  onSelect,
  onNewTask,
  onRename,
  onArchive,
}: TaskListProps) {
  const [query, setQuery] = useState("");
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const groups = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    const visibleTasks = normalizedQuery
      ? tasks.filter((task) => task.title.toLocaleLowerCase().includes(normalizedQuery))
      : tasks;
    return groupTasks(visibleTasks);
  }, [query, tasks]);

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

  return (
    <div className="task-list">
      <button className="primary-button new-task-button" type="button" onClick={onNewTask}>
        新任务
      </button>
      <label className="task-search">
        <Search aria-hidden="true" size={16} />
        <span className="sr-only">搜索任务</span>
        <input
          type="search"
          aria-label="搜索任务"
          placeholder="搜索任务"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      <div className="task-items">
        {groups.map((group) => (
          <section className="task-group" aria-labelledby={`task-group-${group.key}`} key={group.key}>
            <h3 id={`task-group-${group.key}`}>{group.label}</h3>
            {group.tasks.map((task) => (
              <article className="task-item" data-active={task.id === activeTaskId} key={task.id}>
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
                      <span className={`task-state task-state-${task.state}`}>
                        {stateLabels[task.state]}
                      </span>
                    </button>
                    <div className="task-actions">
                      <button type="button" aria-label={`重命名 ${task.title}`} onClick={() => beginRename(task)}>
                        <Pencil aria-hidden="true" size={14} />
                      </button>
                      <button type="button" aria-label={`归档 ${task.title}`} onClick={() => requestArchive(task)}>
                        <Archive aria-hidden="true" size={14} />
                      </button>
                    </div>
                  </>
                )}
              </article>
            ))}
          </section>
        ))}
        {groups.length === 0 && <p className="task-list-empty">没有匹配的任务</p>}
      </div>
    </div>
  );
}
