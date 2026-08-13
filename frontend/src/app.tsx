import { ChevronLeft, PanelRight, RefreshCw, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { archiveTask, createTask, listTasks, renameTask } from "./api";
import { TaskList } from "./components/TaskList";
import type { Task, TaskPage } from "./types";

interface SharedPageRequest {
  controller: AbortController;
  startRevision: number;
  promise: Promise<TaskPage>;
  owners: Set<string>;
  settled: boolean;
  cancelled: boolean;
  applied: boolean;
}

interface TaskMutationOwnership {
  revision: number;
  kind: "create" | "rename";
  title: string;
  runtimeKind?: string;
  protectsExistence: boolean;
}

function taskTimestamp(value: string) {
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)
    ? `${value.slice(0, 10)}T${value.slice(11)}Z`
    : value;
  const parsed = Date.parse(normalized);
  return Number.isNaN(parsed) ? 0 : parsed;
}

function sortTasks(tasks: Task[]) {
  return [...tasks].sort((left, right) => taskTimestamp(right.updated_at) - taskTimestamp(left.updated_at));
}

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

function useMediaQuery(query: string) {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setMatches(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [query]);
  return matches;
}

function InspectorDetails({ task }: { task: Task | null }) {
  return task ? (
    <dl className="detail-list">
      <div><dt>运行时</dt><dd>{task.runtime_kind}</dd></div>
      <div><dt>最近更新</dt><dd>{task.updated_at}</dd></div>
    </dl>
  ) : (
    <p className="inspector-empty">选择任务后，这里会显示执行信息。</p>
  );
}

export function App() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [nextCursor, setNextCursor] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [mutationError, setMutationError] = useState("");
  const [creating, setCreating] = useState(false);
  const [pendingOperations, setPendingOperations] = useState<Record<string, "rename" | "archive">>({});
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const inspectorIsDrawer = useMediaQuery("(max-width: 939px)");
  const inspectorToggleRef = useRef<HTMLButtonElement>(null);
  const inspectorDialogRef = useRef<HTMLDivElement>(null);
  const mountedRef = useRef(true);
  const requestSequenceRef = useRef(0);
  const selectionVersionRef = useRef(0);
  const selectedTaskIdRef = useRef<string | null>(null);
  const tasksRef = useRef<Task[]>([]);
  const nextCursorRef = useRef("");
  const dataRevisionRef = useRef(0);
  const taskMutationOwnershipRef = useRef(new Map<string, TaskMutationOwnership>());
  const archiveTombstonesRef = useRef(new Map<string, number>());
  const controllersRef = useRef(new Set<AbortController>());
  const loadRequestRef = useRef<{ id: number; controller: AbortController } | null>(null);
  const usedCursorsRef = useRef(new Set<string>());
  const pageRequestsRef = useRef(new Map<string, SharedPageRequest>());
  const chaseSequenceRef = useRef(0);
  const activeChaseRef = useRef<{
    id: number;
    target: string;
    owner: string;
    release: (() => void) | null;
  } | null>(null);
  const createRequestRef = useRef<{ id: number; controller: AbortController } | null>(null);
  const renameRequestsRef = useRef(new Map<string, { id: number; controller: AbortController }>());
  const archiveRequestsRef = useRef(new Map<string, { id: number; controller: AbortController }>());

  const closeInspector = useCallback(() => setInspectorOpen(false), []);

  const writeTasks = useCallback((update: Task[] | ((current: Task[]) => Task[])) => {
    const next = typeof update === "function" ? update(tasksRef.current) : update;
    tasksRef.current = next;
    setTasks(next);
    return next;
  }, []);

  function nextRequest(controller: AbortController) {
    const id = ++requestSequenceRef.current;
    controllersRef.current.add(controller);
    return id;
  }

  function finishRequest(controller: AbortController) {
    controllersRef.current.delete(controller);
  }

  function writeNextCursor(cursor: string) {
    nextCursorRef.current = cursor;
    setNextCursor(cursor);
  }

  function recordCreatedTask(task: Task) {
    const revision = ++dataRevisionRef.current;
    taskMutationOwnershipRef.current.set(task.id, {
      revision,
      kind: "create",
      title: task.title,
      runtimeKind: task.runtime_kind,
      protectsExistence: true,
    });
    return revision;
  }

  function recordRenamedTask(task: Task) {
    const revision = ++dataRevisionRef.current;
    const prior = taskMutationOwnershipRef.current.get(task.id);
    taskMutationOwnershipRef.current.set(task.id, {
      revision,
      kind: "rename",
      title: task.title,
      runtimeKind: prior?.runtimeKind,
      protectsExistence: prior?.protectsExistence ?? false,
    });
    return revision;
  }

  const commitSelection = useCallback(
    (taskId: string | null, mode?: "push" | "replace") => {
      selectedTaskIdRef.current = taskId;
      selectionVersionRef.current += 1;
      setSelectedTaskId(taskId);
      if (mode) updateTaskUrl(taskId, mode);
    },
    [],
  );

  const reconcileTasks = useCallback((
    incomingTasks: Task[],
    startRevision: number,
    mode: "replace" | "append",
    cursor: string,
  ) => {
    const currentById = new Map(tasksRef.current.map((task) => [task.id, task]));
    const incomingIds = new Set(incomingTasks.map((task) => task.id));
    const nextById = mode === "append" ? new Map(currentById) : new Map<string, Task>();

    for (const incoming of incomingTasks) {
      if (archiveTombstonesRef.current.has(incoming.id)) continue;
      const local = currentById.get(incoming.id);
      const ownership = taskMutationOwnershipRef.current.get(incoming.id);
      if (local && ownership) {
        if (ownership.revision > startRevision) {
          nextById.set(incoming.id, local);
          continue;
        }
        const titleConfirmed = incoming.title === ownership.title;
        const runtimeConfirmed = ownership.runtimeKind === undefined
          || incoming.runtime_kind === ownership.runtimeKind;
        if (titleConfirmed && runtimeConfirmed) {
          taskMutationOwnershipRef.current.delete(incoming.id);
          nextById.set(incoming.id, incoming);
        } else {
          nextById.set(incoming.id, {
            ...incoming,
            title: ownership.title,
            runtime_kind: ownership.runtimeKind ?? incoming.runtime_kind,
          });
        }
        continue;
      }
      nextById.set(incoming.id, incoming);
    }

    if (mode === "replace") {
      for (const [taskId, ownership] of taskMutationOwnershipRef.current) {
        const local = currentById.get(taskId);
        if (
          (ownership.protectsExistence || ownership.revision > startRevision)
          && local
          && !archiveTombstonesRef.current.has(taskId)
          && !incomingIds.has(taskId)
        ) {
          nextById.set(taskId, local);
        }
      }
    }

    if (!cursor) {
      for (const [taskId, revision] of archiveTombstonesRef.current) {
        if (revision <= startRevision && !incomingIds.has(taskId)) {
          archiveTombstonesRef.current.delete(taskId);
        }
      }
    }

    const reconciled = sortTasks(Array.from(nextById.values()));
    writeTasks(reconciled);
    return reconciled;
  }, [writeTasks]);

  const applyLoadedTasks = useCallback((loadedTasks: Task[], cursor: string, startRevision: number) => {
    const uniqueTasks = Array.from(new Map(loadedTasks.map((task) => [task.id, task])).values());
    const reconciled = reconcileTasks(uniqueTasks, startRevision, "replace", cursor);
    writeNextCursor(cursor);
    usedCursorsRef.current.clear();
    const requestedTaskId = taskIdFromUrl();
    if (requestedTaskId && reconciled.some((task) => task.id === requestedTaskId)) {
      commitSelection(requestedTaskId);
    } else {
      commitSelection(null);
      if (requestedTaskId && !cursor) updateTaskUrl(null, "replace");
    }
    return reconciled;
  }, [commitSelection, reconcileTasks]);

  const cancelActiveChase = useCallback(() => {
    const chase = activeChaseRef.current;
    activeChaseRef.current = null;
    chase?.release?.();
  }, []);

  const cancelPageRequests = useCallback(() => {
    for (const [cursor, request] of pageRequestsRef.current) {
      request.cancelled = true;
      request.controller.abort();
      pageRequestsRef.current.delete(cursor);
    }
  }, []);

  const acquirePage = useCallback((cursor: string, owner: string) => {
    let request = pageRequestsRef.current.get(cursor);
    if (!request || request.cancelled) {
      const controller = new AbortController();
      const startRevision = dataRevisionRef.current;
      nextRequest(controller);
      request = {
        controller,
        startRevision,
        owners: new Set(),
        settled: false,
        cancelled: false,
        applied: false,
        promise: Promise.resolve({ items: [], nextCursor: "" }),
      };
      const createdRequest = request;
      createdRequest.promise = listTasks({
        archived: "active",
        limit: 100,
        cursor,
        signal: controller.signal,
      }).finally(() => {
        createdRequest.settled = true;
        finishRequest(controller);
        if (pageRequestsRef.current.get(cursor) === createdRequest) {
          pageRequestsRef.current.delete(cursor);
        }
      });
      pageRequestsRef.current.set(cursor, createdRequest);
    }
    request.owners.add(owner);
    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      request!.owners.delete(owner);
      if (request!.owners.size === 0 && !request!.settled) {
        request!.cancelled = true;
        request!.controller.abort();
        if (pageRequestsRef.current.get(cursor) === request) pageRequestsRef.current.delete(cursor);
      }
    };
    return { request, release };
  }, []);

  const applyPage = useCallback((cursor: string, request: SharedPageRequest, page: TaskPage) => {
    if (request.cancelled || request.applied) return tasksRef.current;
    request.applied = true;
    usedCursorsRef.current.add(cursor);
    const merged = reconcileTasks(page.items, request.startRevision, "append", page.nextCursor);
    if (page.nextCursor && usedCursorsRef.current.has(page.nextCursor)) {
      writeNextCursor("");
      setMutationError("任务分页游标重复，已停止继续加载");
    } else {
      writeNextCursor(page.nextCursor);
    }
    const requestedTaskId = taskIdFromUrl();
    if (requestedTaskId && merged.some((task) => task.id === requestedTaskId)) {
      commitSelection(requestedTaskId);
    } else if (requestedTaskId && !page.nextCursor) {
      commitSelection(null);
      updateTaskUrl(null, "replace");
      setMutationError("未找到链接指定的任务");
    }
    return merged;
  }, [commitSelection, reconcileTasks]);

  const startDeepLinkChase = useCallback((target: string, initialCursor: string) => {
    cancelActiveChase();
    if (tasksRef.current.some((task) => task.id === target)) {
      commitSelection(target);
      return;
    }
    if (!initialCursor) {
      commitSelection(null);
      updateTaskUrl(null, "replace");
      setMutationError("未找到链接指定的任务");
      return;
    }
    const id = ++chaseSequenceRef.current;
    const chase = { id, target, owner: `chase:${id}`, release: null as (() => void) | null };
    activeChaseRef.current = chase;
    void (async () => {
      const seen = new Set<string>();
      let cursor = initialCursor;
      try {
        while (cursor && mountedRef.current && activeChaseRef.current?.id === id) {
          if (seen.has(cursor)) {
            writeNextCursor("");
            setMutationError("任务分页游标重复，未找到链接指定的任务");
            updateTaskUrl(null, "replace");
            return;
          }
          seen.add(cursor);
          const acquired = acquirePage(cursor, chase.owner);
          chase.release = acquired.release;
          const page = await acquired.request.promise;
          acquired.release();
          chase.release = null;
          if (
            !mountedRef.current
            || activeChaseRef.current?.id !== id
            || taskIdFromUrl() !== target
          ) return;
          const merged = applyPage(cursor, acquired.request, page);
          if (merged.some((task) => task.id === target)) {
            commitSelection(target);
            return;
          }
          cursor = page.nextCursor;
        }
      } catch (error) {
        if (
          activeChaseRef.current?.id === id
          && mountedRef.current
          && !(error instanceof DOMException && error.name === "AbortError")
        ) {
          setMutationError("链接任务加载失败，请重试");
        }
      } finally {
        chase.release?.();
        if (activeChaseRef.current?.id === id) activeChaseRef.current = null;
      }
    })();
  }, [acquirePage, applyPage, cancelActiveChase, commitSelection]);

  const load = useCallback(async () => {
    loadRequestRef.current?.controller.abort();
    cancelActiveChase();
    cancelPageRequests();
    const controller = new AbortController();
    const requestId = nextRequest(controller);
    const startRevision = dataRevisionRef.current;
    loadRequestRef.current = { id: requestId, controller };
    const isInitialLoad = tasksRef.current.length === 0;
    setLoading(isInitialLoad);
    setLoadingMore(false);
    setLoadError(false);
    setMutationError("");
    try {
      const page = await listTasks({
        archived: "active",
        limit: 100,
        signal: controller.signal,
      });
      if (loadRequestRef.current?.id === requestId && mountedRef.current) {
        const reconciled = applyLoadedTasks(page.items, page.nextCursor, startRevision);
        const requestedTaskId = taskIdFromUrl();
        if (requestedTaskId && !reconciled.some((task) => task.id === requestedTaskId) && page.nextCursor) {
          startDeepLinkChase(requestedTaskId, page.nextCursor);
        }
      }
    } catch (error) {
      if (
        loadRequestRef.current?.id === requestId &&
        mountedRef.current &&
        !(error instanceof DOMException && error.name === "AbortError")
      ) {
        setLoadError(true);
      }
    } finally {
      finishRequest(controller);
      if (loadRequestRef.current?.id === requestId && mountedRef.current) {
        setLoading(false);
      }
    }
  }, [applyLoadedTasks, cancelActiveChase, cancelPageRequests, startDeepLinkChase]);

  async function loadMoreTasks() {
    const cursor = nextCursorRef.current;
    if (!cursor || loadingMore) return;
    if (usedCursorsRef.current.has(cursor)) {
      writeNextCursor("");
      setMutationError("任务分页游标重复，已停止继续加载");
      return;
    }
    const owner = `manual:${++requestSequenceRef.current}`;
    const acquired = acquirePage(cursor, owner);
    setLoadingMore(true);
    setMutationError("");
    try {
      const page = await acquired.request.promise;
      if (!mountedRef.current) return;
      applyPage(cursor, acquired.request, page);
    } catch (error) {
      if (
        mountedRef.current &&
        !(error instanceof DOMException && error.name === "AbortError")
      ) {
        usedCursorsRef.current.delete(cursor);
        setMutationError("更多任务加载失败，请重试");
      }
    } finally {
      acquired.release();
      if (mountedRef.current) setLoadingMore(false);
    }
  }

  useEffect(() => {
    mountedRef.current = true;
    void load();
    return () => {
      mountedRef.current = false;
      cancelActiveChase();
      cancelPageRequests();
      for (const controller of controllersRef.current) controller.abort();
      controllersRef.current.clear();
    };
  }, [cancelActiveChase, cancelPageRequests, load]);

  useEffect(() => {
    function handlePopState() {
      const requestedTaskId = taskIdFromUrl();
      cancelActiveChase();
      if (requestedTaskId && tasksRef.current.some((task) => task.id === requestedTaskId)) {
        commitSelection(requestedTaskId);
      } else if (requestedTaskId && nextCursorRef.current) {
        commitSelection(null);
        startDeepLinkChase(requestedTaskId, nextCursorRef.current);
      } else {
        commitSelection(null);
        if (requestedTaskId) {
          updateTaskUrl(null, "replace");
          setMutationError("未找到链接指定的任务");
        }
      }
      setInspectorOpen(false);
    }
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, [cancelActiveChase, commitSelection, startDeepLinkChase]);

  useEffect(() => {
    if (!inspectorIsDrawer) setInspectorOpen(false);
  }, [inspectorIsDrawer]);

  useEffect(() => {
    if (!inspectorIsDrawer || !inspectorOpen) return;
    const dialog = inspectorDialogRef.current;
    const returnFocus = inspectorToggleRef.current;
    if (!dialog) return;
    const focusable = () =>
      Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
    focusable()[0]?.focus();
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeInspector();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (items.length === 0) {
        event.preventDefault();
        return;
      }
      const firstItem = items[0];
      const lastItem = items[items.length - 1];
      if (event.shiftKey && document.activeElement === firstItem) {
        event.preventDefault();
        lastItem.focus();
      } else if (!event.shiftKey && document.activeElement === lastItem) {
        event.preventDefault();
        firstItem.focus();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      returnFocus?.focus();
    };
  }, [closeInspector, inspectorIsDrawer, inspectorOpen]);

  const selectedTask = useMemo(
    () => tasks.find((task) => task.id === selectedTaskId) ?? null,
    [selectedTaskId, tasks],
  );

  function selectTask(taskId: string) {
    cancelActiveChase();
    commitSelection(taskId, "push");
    setInspectorOpen(false);
  }

  function returnToTaskList() {
    cancelActiveChase();
    commitSelection(null, "push");
    setInspectorOpen(false);
  }

  async function newTask() {
    if (creating) return;
    createRequestRef.current?.controller.abort();
    const controller = new AbortController();
    const requestId = nextRequest(controller);
    const selectionAtStart = selectedTaskIdRef.current;
    const selectionVersionAtStart = selectionVersionRef.current;
    createRequestRef.current = { id: requestId, controller };
    setCreating(true);
    setMutationError("");
    try {
      const created = await createTask("新任务", "codex", { signal: controller.signal });
      if (createRequestRef.current?.id !== requestId || !mountedRef.current) return;
      recordCreatedTask(created);
      writeTasks((current) => [created, ...current.filter((task) => task.id !== created.id)]);
      if (
        selectedTaskIdRef.current === selectionAtStart &&
        selectionVersionRef.current === selectionVersionAtStart
      ) {
        commitSelection(created.id, "push");
      }
    } catch (error) {
      if (
        createRequestRef.current?.id === requestId &&
        mountedRef.current &&
        !(error instanceof DOMException && error.name === "AbortError")
      ) {
        setMutationError("新任务创建失败，请重试");
      }
    } finally {
      finishRequest(controller);
      if (createRequestRef.current?.id === requestId && mountedRef.current) {
        setCreating(false);
      }
    }
  }

  async function rename(taskId: string, title: string) {
    renameRequestsRef.current.get(taskId)?.controller.abort();
    const controller = new AbortController();
    const requestId = nextRequest(controller);
    renameRequestsRef.current.set(taskId, { id: requestId, controller });
    setPendingOperations((current) => ({ ...current, [taskId]: "rename" }));
    setMutationError("");
    try {
      const renamed = await renameTask(taskId, title, { signal: controller.signal });
      if (renameRequestsRef.current.get(taskId)?.id !== requestId || !mountedRef.current) return;
      recordRenamedTask(renamed);
      writeTasks((current) => current.map((task) => (task.id === taskId ? renamed : task)));
    } catch (error) {
      if (
        renameRequestsRef.current.get(taskId)?.id === requestId &&
        mountedRef.current &&
        !(error instanceof DOMException && error.name === "AbortError")
      ) {
        setMutationError("重命名失败，请重试");
      }
    } finally {
      finishRequest(controller);
      if (renameRequestsRef.current.get(taskId)?.id === requestId) {
        renameRequestsRef.current.delete(taskId);
        if (mountedRef.current) {
          setPendingOperations((current) => {
            if (current[taskId] !== "rename") return current;
            const { [taskId]: _finished, ...remaining } = current;
            return remaining;
          });
        }
      }
    }
  }

  async function archive(taskId: string) {
    archiveRequestsRef.current.get(taskId)?.controller.abort();
    const controller = new AbortController();
    const requestId = nextRequest(controller);
    archiveRequestsRef.current.set(taskId, { id: requestId, controller });
    setPendingOperations((current) => ({ ...current, [taskId]: "archive" }));
    setMutationError("");
    try {
      await archiveTask(taskId, { signal: controller.signal });
      if (archiveRequestsRef.current.get(taskId)?.id !== requestId || !mountedRef.current) return;
      const revision = ++dataRevisionRef.current;
      archiveTombstonesRef.current.set(taskId, revision);
      taskMutationOwnershipRef.current.delete(taskId);
      writeTasks((current) => current.filter((task) => task.id !== taskId));
      if (selectedTaskIdRef.current === taskId) {
        commitSelection(null, "replace");
        setInspectorOpen(false);
      }
    } catch (error) {
      if (
        archiveRequestsRef.current.get(taskId)?.id === requestId &&
        mountedRef.current &&
        !(error instanceof DOMException && error.name === "AbortError")
      ) {
        setMutationError("归档失败；执行中的任务需要先停止");
      }
    } finally {
      finishRequest(controller);
      if (archiveRequestsRef.current.get(taskId)?.id === requestId) {
        archiveRequestsRef.current.delete(taskId);
        if (mountedRef.current) {
          setPendingOperations((current) => {
            if (current[taskId] !== "archive") return current;
            const { [taskId]: _finished, ...remaining } = current;
            return remaining;
          });
        }
      }
    }
  }

  return (
    <div className={`workbench-shell${selectedTask ? " has-selection" : ""}`}>
      <aside
        className="task-panel"
        aria-label="任务列表"
        aria-hidden={inspectorIsDrawer && inspectorOpen ? "true" : undefined}
        inert={inspectorIsDrawer && inspectorOpen ? true : undefined}
      >
        <header className="brand-header">
          <div className="brand-mark" aria-hidden="true">
            <Sparkles size={18} />
          </div>
          <div>
            <p className="eyebrow">FRIDAY</p>
            <h1>Agent 工作台</h1>
          </div>
          <button
            className="refresh-tasks"
            type="button"
            aria-label="刷新任务"
            disabled={loading}
            onClick={() => void load()}
          >
            <RefreshCw aria-hidden="true" size={16} />
          </button>
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
              hasMore={Boolean(nextCursor)}
              loadingMore={loadingMore}
              pendingOperations={pendingOperations}
              onLoadMore={() => void loadMoreTasks()}
              onSelect={selectTask}
              onNewTask={() => void newTask()}
              onRename={rename}
              onArchive={archive}
            />
          </>
        )}
      </aside>

      <main
        className="conversation-panel"
        aria-hidden={inspectorIsDrawer && inspectorOpen ? "true" : undefined}
        inert={inspectorIsDrawer && inspectorOpen ? true : undefined}
      >
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
              {inspectorIsDrawer && (
                <button
                  ref={inspectorToggleRef}
                  className="inspector-toggle secondary-button"
                  type="button"
                  aria-controls="task-inspector"
                  aria-expanded={inspectorOpen}
                  aria-label="打开详情"
                  onClick={() => setInspectorOpen(true)}
                >
                  <PanelRight aria-hidden="true" size={18} />
                </button>
              )}
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

      {!inspectorIsDrawer && (
        <aside className="inspector-panel" aria-label="任务详情">
          <div className="inspector-header">
            <p className="eyebrow">INSPECTOR</p>
            <h2>任务详情</h2>
          </div>
          <InspectorDetails task={selectedTask} />
        </aside>
      )}
      {inspectorIsDrawer && inspectorOpen && (
        <>
          <div className="drawer-scrim" aria-hidden="true" onClick={closeInspector} />
          <div
            ref={inspectorDialogRef}
            className="inspector-panel inspector-drawer"
            id="task-inspector"
            role="dialog"
            aria-modal="true"
            aria-labelledby="task-inspector-title"
          >
            <div className="inspector-header inspector-drawer-header">
              <div>
                <p className="eyebrow">INSPECTOR</p>
                <h2 id="task-inspector-title">任务详情</h2>
              </div>
              <button className="drawer-close" type="button" aria-label="关闭详情" onClick={closeInspector}>
                <X aria-hidden="true" size={18} />
              </button>
            </div>
            <InspectorDetails task={selectedTask} />
          </div>
        </>
      )}
    </div>
  );
}
