import { ChevronLeft, PanelRight, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { archiveTask, createTask, listTasks, renameTask } from "./api";
import { TaskList } from "./components/TaskList";
import type { Task } from "./types";

function taskIdFromUrl(): string | null {
  return new URL(window.location.href).searchParams.get("task");
}

function updateTaskUrl(taskId: string | null, mode: "push" | "replace") {
  const url = new URL(window.location.href);
  if (taskId) {
    url.searchParams.set("task", taskId);
  } else {
    url.searchParams.delete("task");
  }
  const method = mode === "push" ? "pushState" : "replaceState";
  window.history[method]({}, "", `${url.pathname}${url.search}${url.hash}`);
}

export function App() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [mutationError, setMutationError] = useState("");
  const [creating, setCreating] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);

  const applyLoadedTasks = useCallback((loadedTasks: Task[]) => {
    setTasks(loadedTasks);
    const requestedTaskId = taskIdFromUrl();
    if (requestedTaskId && loadedTasks.some((task) => task.id === requestedTaskId)) {
      setSelectedTaskId(requestedTaskId);
    } else {
      setSelectedTaskId(null);
      if (requestedTaskId) updateTaskUrl(null, "replace");
    }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const loadedTasks: Task[] = [];
      const seenCursors = new Set<string>();
      let cursor = "";
      do {
        const page = await listTasks({
          archived: "active",
          limit: 100,
          ...(cursor ? { cursor } : {}),
        });
        loadedTasks.push(...page.items);
        if (page.nextCursor && seenCursors.has(page.nextCursor)) {
          throw new Error("Task pagination cursor repeated");
        }
        if (page.nextCursor) seenCursors.add(page.nextCursor);
        cursor = page.nextCursor;
      } while (cursor);
      applyLoadedTasks(loadedTasks);
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [applyLoadedTasks]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    function handlePopState() {
      const requestedTaskId = taskIdFromUrl();
      setSelectedTaskId(
        requestedTaskId && tasks.some((task) => task.id === requestedTaskId)
          ? requestedTaskId
          : null,
      );
      setInspectorOpen(false);
    }
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, [tasks]);

  const selectedTask = useMemo(
    () => tasks.find((task) => task.id === selectedTaskId) ?? null,
    [selectedTaskId, tasks],
  );

  function selectTask(taskId: string) {
    setSelectedTaskId(taskId);
    setInspectorOpen(false);
    updateTaskUrl(taskId, "push");
  }

  function returnToTaskList() {
    setSelectedTaskId(null);
    setInspectorOpen(false);
    updateTaskUrl(null, "push");
  }

  async function newTask() {
    if (creating) return;
    setCreating(true);
    setMutationError("");
    try {
      const created = await createTask("新任务", "codex");
      setTasks((current) => [created, ...current]);
      selectTask(created.id);
    } catch {
      setMutationError("新任务创建失败，请重试");
    } finally {
      setCreating(false);
    }
  }

  async function rename(taskId: string, title: string) {
    setMutationError("");
    try {
      const renamed = await renameTask(taskId, title);
      setTasks((current) => current.map((task) => (task.id === taskId ? renamed : task)));
    } catch {
      setMutationError("重命名失败，请重试");
    }
  }

  async function archive(taskId: string) {
    setMutationError("");
    try {
      await archiveTask(taskId);
      setTasks((current) => current.filter((task) => task.id !== taskId));
      if (selectedTaskId === taskId) {
        setSelectedTaskId(null);
        setInspectorOpen(false);
        updateTaskUrl(null, "replace");
      }
    } catch {
      setMutationError("归档失败；执行中的任务需要先停止");
    }
  }

  return (
    <div className={`workbench-shell${selectedTask ? " has-selection" : ""}`}>
      <aside className="task-panel" aria-label="任务列表">
        <header className="brand-header">
          <div className="brand-mark" aria-hidden="true">
            <Sparkles size={18} />
          </div>
          <div>
            <p className="eyebrow">FRIDAY</p>
            <h1>Agent 工作台</h1>
          </div>
        </header>
        {loading ? (
          <div className="panel-state" role="status">正在加载任务…</div>
        ) : loadError ? (
          <div className="panel-state panel-error" role="alert">
            <strong>任务加载失败</strong>
            <span>请检查本地服务后重试。</span>
            <button type="button" className="secondary-button" onClick={() => void load()}>
              重试
            </button>
          </div>
        ) : (
          <>
            {mutationError && <p className="inline-alert" role="alert">{mutationError}</p>}
            <TaskList
              tasks={tasks}
              activeTaskId={selectedTaskId}
              onSelect={selectTask}
              onNewTask={() => void newTask()}
              onRename={rename}
              onArchive={archive}
            />
          </>
        )}
      </aside>

      <main className="conversation-panel">
        {selectedTask ? (
          <section className="conversation-placeholder" aria-labelledby="conversation-title">
            <header className="conversation-header">
              <button className="mobile-back" type="button" onClick={returnToTaskList} aria-label="返回任务列表">
                <ChevronLeft aria-hidden="true" size={19} />
                任务
              </button>
              <div className="conversation-heading">
                <p className="eyebrow">{selectedTask.runtime_kind}</p>
                <h2 id="conversation-title">{selectedTask.title}</h2>
              </div>
              <button
                className="inspector-toggle secondary-button"
                type="button"
                aria-controls="task-inspector"
                aria-expanded={inspectorOpen}
                aria-label={inspectorOpen ? "关闭详情" : "打开详情"}
                onClick={() => setInspectorOpen((open) => !open)}
              >
                <PanelRight aria-hidden="true" size={18} />
              </button>
            </header>
            <div className="conversation-empty">
              <Sparkles aria-hidden="true" size={26} />
              <h3>对话即将就绪</h3>
              <p>任务已安全载入。消息、执行进度和附件将在下一阶段接入。</p>
            </div>
          </section>
        ) : (
          <section className="welcome-panel" aria-labelledby="welcome-title">
            <div className="welcome-glyph" aria-hidden="true"><Sparkles size={28} /></div>
            <h2 id="welcome-title">
              {loading
                ? "正在准备工作台"
                : loadError
                  ? "无法载入任务"
                  : tasks.length === 0
                    ? "还没有任务"
                    : "选择一个任务开始"}
            </h2>
            <p>
              {loadError
                ? "本地任务状态尚未成功读取，请从左侧重试。"
                : tasks.length === 0 && !loading
                  ? "创建一个任务，开始与 Agent 协作。"
                  : "从左侧打开已有任务，或创建一个新任务。"}
            </p>
          </section>
        )}
      </main>

      <aside
        className="inspector-panel"
        id="task-inspector"
        aria-label="任务详情"
        data-open={inspectorOpen}
      >
        <div className="inspector-header">
          <p className="eyebrow">INSPECTOR</p>
          <h2>任务详情</h2>
        </div>
        {selectedTask ? (
          <dl className="detail-list">
            <div><dt>运行时</dt><dd>{selectedTask.runtime_kind}</dd></div>
            <div><dt>最近更新</dt><dd>{selectedTask.updated_at}</dd></div>
          </dl>
        ) : (
          <p className="inspector-empty">选择任务后，这里会显示执行信息。</p>
        )}
      </aside>
      {inspectorOpen && (
        <button className="drawer-scrim" type="button" aria-label="关闭详情" onClick={() => setInspectorOpen(false)} />
      )}
    </div>
  );
}
