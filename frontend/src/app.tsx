import { ChevronLeft, PanelRight, RefreshCw, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  archiveTask,
  cancelAction,
  confirmAction,
  createTask,
  createTurn,
  getStats,
  getTimeline,
  listTasks,
  renameTask,
  runtimeCapabilities,
} from "./api";
import {
  associateFeedbackTurn,
  claimFeedbackBatch,
  listPendingFeedback,
  type FeedbackBatch,
} from "./api/feedback";
import type { FeedbackItem } from "./api/console";
import { Composer } from "./components/Composer";
import { ConversationTimeline } from "./components/ConversationTimeline";
import { FeedbackDrawer } from "./components/FeedbackDrawer";
import { GlobalNav } from "./components/GlobalNav";
import { TaskList } from "./components/TaskList";
import { TurnInspector } from "./components/TurnInspector";
import { applyWorkbenchEvent, createEventState, EventStreamConnection } from "./events";
import type {
  Confirmation,
  ConfirmationStatus,
  RuntimeCapabilities,
  Task,
  TaskPage,
  Timeline,
  Turn,
  TurnStatus,
  WorkbenchEvent,
  WorkbenchStats,
} from "./types";

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

function feedbackKey(item: FeedbackItem): string {
  return item.feedback_key || item.id;
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

function mergeById<T extends { id: string }>(first: T[], second: T[]) {
  return Array.from(new Map([...first, ...second].map((item) => [item.id, item])).values());
}

function mergeTimeline(current: Timeline, incoming: Timeline, mode: "recent" | "older"): Timeline {
  const turns = mode === "older"
    ? mergeById(current.turns, incoming.turns)
    : [...incoming.turns, ...current.turns.filter((turn) => !incoming.turns.some((candidate) => candidate.id === turn.id))];
  const events = Array.from(new Map([...current.events, ...incoming.events].map((event) => [event.id, event])).values())
    .sort((left, right) => left.id - right.id);
  return {
    ...incoming,
    turns,
    events,
    attachments: mergeById(current.attachments, incoming.attachments),
    artifacts: mergeById(current.artifacts, incoming.artifacts),
    confirmations: mergeById(current.confirmations, incoming.confirmations),
    events_has_more: current.events_has_more || incoming.events_has_more,
    artifacts_has_more: current.artifacts_has_more || incoming.artifacts_has_more,
    confirmations_has_more: current.confirmations_has_more || incoming.confirmations_has_more,
    attachments_has_more: current.attachments_has_more || incoming.attachments_has_more,
  };
}

type ResourcePageKind = "events" | "artifacts" | "confirmations" | "attachments";
type TimelineRequestChannel = "turns" | "refresh" | ResourcePageKind;
interface ResourcePageCursor {
  before: string;
  cursor: string | number;
}
type ResourcePageQueues = Record<ResourcePageKind, ResourcePageCursor[]>;

function emptyResourcePageQueues(): ResourcePageQueues {
  return { events: [], artifacts: [], confirmations: [], attachments: [] };
}

function resourceCursor(timeline: Timeline, kind: ResourcePageKind): string | number {
  if (kind === "events") return timeline.events_next_cursor;
  if (kind === "artifacts") return timeline.artifacts_next_cursor;
  if (kind === "confirmations") return timeline.confirmations_next_cursor;
  return timeline.attachments_next_cursor;
}

function resourceHasMore(timeline: Timeline, kind: ResourcePageKind): boolean {
  if (kind === "events") return timeline.events_has_more;
  if (kind === "artifacts") return timeline.artifacts_has_more;
  if (kind === "confirmations") return timeline.confirmations_has_more;
  return timeline.attachments_has_more;
}

function applyResourceQueue(timeline: Timeline, kind: ResourcePageKind, queue: ResourcePageCursor[]): Timeline {
  const next = queue[0]?.cursor;
  if (kind === "events") return { ...timeline, events_has_more: queue.length > 0, events_next_cursor: typeof next === "number" ? next : 0 };
  if (kind === "artifacts") return { ...timeline, artifacts_has_more: queue.length > 0, artifacts_next_cursor: typeof next === "string" ? next : "" };
  if (kind === "confirmations") return { ...timeline, confirmations_has_more: queue.length > 0, confirmations_next_cursor: typeof next === "string" ? next : "" };
  return { ...timeline, attachments_has_more: queue.length > 0, attachments_next_cursor: typeof next === "string" ? next : "" };
}

function mergeResourcePage(current: Timeline, incoming: Timeline, kind: ResourcePageKind): Timeline {
  if (kind === "events") return {
    ...current,
    task: incoming.task,
    turns: mergeById(current.turns, incoming.turns),
    events: Array.from(new Map([...current.events, ...incoming.events].map((event) => [event.id, event])).values())
      .sort((left, right) => left.id - right.id),
    events_has_more: incoming.events_has_more,
    events_next_cursor: incoming.events_next_cursor,
  };
  if (kind === "artifacts") return {
    ...current,
    artifacts: mergeById(current.artifacts, incoming.artifacts),
    artifacts_has_more: incoming.artifacts_has_more,
    artifacts_next_cursor: incoming.artifacts_next_cursor,
  };
  if (kind === "confirmations") return {
    ...current,
    confirmations: mergeById(current.confirmations, incoming.confirmations),
    confirmations_has_more: incoming.confirmations_has_more,
    confirmations_next_cursor: incoming.confirmations_next_cursor,
  };
  return {
    ...current,
    attachments: mergeById(current.attachments, incoming.attachments),
    attachments_has_more: incoming.attachments_has_more,
    attachments_next_cursor: incoming.attachments_next_cursor,
  };
}

const turnStatuses: readonly TurnStatus[] = ["queued", "running", "waiting_confirmation", "completed", "stopped", "failed"];
const activeTurnStatuses: readonly TurnStatus[] = ["queued", "running", "waiting_confirmation"];
const confirmationStatuses: readonly ConfirmationStatus[] = ["pending", "confirmed", "cancelled", "executed", "failed"];

function statusFromEvent(event: WorkbenchEvent): TurnStatus | null {
  if (event.event_type === "turn_completed") return "completed";
  if (event.event_type === "turn_failed") return "failed";
  if (event.event_type !== "status_changed") return null;
  const value = event.payload.status;
  return typeof value === "string" && turnStatuses.includes(value as TurnStatus) ? value as TurnStatus : null;
}

function confirmationProgressFromEvent(event: WorkbenchEvent): { id: string; status: ConfirmationStatus | null } | null {
  if (event.event_type !== "status_changed") return null;
  const rawId = event.payload.confirmation_id;
  const id = typeof rawId === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(rawId) ? rawId : "";
  const rawStatus = event.payload.confirmation_status;
  const status = typeof rawStatus === "string" && confirmationStatuses.includes(rawStatus as ConfirmationStatus)
    ? rawStatus as ConfirmationStatus
    : null;
  return id || status ? { id, status } : null;
}

export interface AppProps {
  showGlobalNav?: boolean;
}

export function App({ showGlobalNav = true }: AppProps = {}) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [nextCursor, setNextCursor] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [mutationError, setMutationError] = useState("");
  const [creating, setCreating] = useState(false);
  const [pendingOperations, setPendingOperations] = useState<Record<string, "rename" | "archive">>({});
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState("");
  const [connectionError, setConnectionError] = useState("");
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [loadingResources, setLoadingResources] = useState<ReadonlySet<ResourcePageKind>>(() => new Set());
  const [resourceQueues, setResourceQueues] = useState<ResourcePageQueues>(emptyResourcePageQueues);
  const [capabilities, setCapabilities] = useState<RuntimeCapabilities[] | null>(null);
  const [stats, setStats] = useState<WorkbenchStats | null>(null);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackPending, setFeedbackPending] = useState<FeedbackItem[]>([]);
  const [feedbackLoading, setFeedbackLoading] = useState(false);
  const [feedbackError, setFeedbackError] = useState("");
  const [feedbackSelected, setFeedbackSelected] = useState<ReadonlySet<string>>(() => new Set());
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);
  const [pendingFeedbackCount, setPendingFeedbackCount] = useState(0);
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
  const timelineRef = useRef<Timeline | null>(null);
  const timelineRequestsRef = useRef(new Map<TimelineRequestChannel, { id: number; controller: AbortController }>());
  const timelineRefreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const timelineRefreshTaskRef = useRef<string | null>(null);
  const statsRequestRef = useRef<{ id: number; controller: AbortController } | null>(null);
  const statsRefreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const resourceQueuesRef = useRef<ResourcePageQueues>(emptyResourcePageQueues());
  const loadedOlderTimelineRef = useRef(false);
  const streamRef = useRef<EventStreamConnection | null>(null);
  const confirmationMutationsRef = useRef(new Set<string>());
  const feedbackLoadRef = useRef<{ id: number; controller: AbortController } | null>(null);
  const feedbackPreloadRef = useRef<{ generation: number; controller: AbortController } | null>(null);
  const feedbackPreloadGenerationRef = useRef(0);
  const feedbackPendingCacheRef = useRef<{ items: FeedbackItem[]; count: number } | null>(null);
  const feedbackSubmitRef = useRef<{ controller: AbortController; batch: FeedbackBatch | null; batchId: string; task: Task | null; turn: Turn | null; associated: boolean; keys: string[] }>({
    controller: new AbortController(), batch: null, batchId: "", task: null, turn: null, associated: false, keys: [],
  });

  const closeInspector = useCallback(() => setInspectorOpen(false), []);

  const writeTasks = useCallback((update: Task[] | ((current: Task[]) => Task[])) => {
    const next = typeof update === "function" ? update(tasksRef.current) : update;
    tasksRef.current = next;
    setTasks(next);
    return next;
  }, []);

  const writeTimeline = useCallback((update: Timeline | null | ((current: Timeline | null) => Timeline | null)) => {
    const next = typeof update === "function" ? update(timelineRef.current) : update;
    timelineRef.current = next;
    setTimeline(next);
    return next;
  }, []);

  const writeResourceQueues = useCallback((update: ResourcePageQueues | ((current: ResourcePageQueues) => ResourcePageQueues)) => {
    const next = typeof update === "function" ? update(resourceQueuesRef.current) : update;
    resourceQueuesRef.current = next;
    setResourceQueues(next);
    return next;
  }, []);

  const registerResourcePages = useCallback((loaded: Timeline, before: string, replace: boolean) => {
    writeResourceQueues((current) => {
      const next = replace
        ? emptyResourcePageQueues()
        : Object.fromEntries(Object.entries(current).map(([kind, pages]) => [kind, [...pages]])) as ResourcePageQueues;
      for (const kind of ["events", "artifacts", "confirmations", "attachments"] as const) {
        if (!resourceHasMore(loaded, kind)) continue;
        // Attachments belong to the task, not an individual turn window. The API
        // therefore ignores `before` for this cursor; keep one global queue entry.
        const entry = { before: kind === "attachments" ? "" : before, cursor: resourceCursor(loaded, kind) };
        if (!next[kind].some((page) => page.before === entry.before && page.cursor === entry.cursor)) next[kind].push(entry);
      }
      return next;
    });
  }, [writeResourceQueues]);

  function nextRequest(controller: AbortController) {
    const id = ++requestSequenceRef.current;
    controllersRef.current.add(controller);
    return id;
  }

  function finishRequest(controller: AbortController) {
    controllersRef.current.delete(controller);
  }

  function startTimelineRequest(channel: TimelineRequestChannel, controller: AbortController) {
    timelineRequestsRef.current.get(channel)?.controller.abort();
    const id = ++requestSequenceRef.current;
    timelineRequestsRef.current.set(channel, { id, controller });
    controllersRef.current.add(controller);
    return id;
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

  const applyTaskSnapshot = useCallback((snapshot: Task) => {
    writeTasks((current) => sortTasks(current.map((task) => {
      if (task.id !== snapshot.id || taskTimestamp(snapshot.updated_at) < taskTimestamp(task.updated_at)) return task;
      const ownership = taskMutationOwnershipRef.current.get(task.id);
      if (ownership?.kind === "create") {
        ownership.protectsExistence = false;
        ownership.revision = ++dataRevisionRef.current;
        return snapshot;
      }
      return ownership ? {
        ...snapshot,
        title: ownership.title,
        runtime_kind: ownership.runtimeKind ?? snapshot.runtime_kind,
      } : snapshot;
    })));
  }, [writeTasks]);

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
        if (ownership.kind === "create") {
          if (taskTimestamp(incoming.updated_at) < taskTimestamp(local.updated_at)) {
            nextById.set(incoming.id, local);
          } else {
            if (ownership.protectsExistence) {
              ownership.protectsExistence = false;
              ownership.revision = ++dataRevisionRef.current;
            }
            nextById.set(incoming.id, incoming);
          }
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

  const loadSelectedTimeline = useCallback(async (
    taskId: string,
    mode: "initial" | "recent" | "older" = "recent",
    before = "",
  ) => {
    const channel: TimelineRequestChannel = mode === "recent" ? "refresh" : "turns";
    const controller = new AbortController();
    const requestId = startTimelineRequest(channel, controller);
    if (mode === "initial") setTimelineLoading(true);
    if (mode === "older") setLoadingOlder(true);
    setTimelineError("");
    try {
      const loaded = await getTimeline(taskId, {
        turnLimit: 100,
        eventLimit: 1000,
        ...(before ? { before } : {}),
        signal: controller.signal,
      });
      if (
        !mountedRef.current
        || timelineRequestsRef.current.get(channel)?.id !== requestId
        || selectedTaskIdRef.current !== taskId
      ) return;
      registerResourcePages(loaded, before, mode === "initial");
      writeTimeline((current) => {
        if (!current || current.task.id !== taskId || mode === "initial") return loaded;
        const merged = mergeTimeline(current, loaded, mode === "older" ? "older" : "recent");
        return mode === "recent" && loadedOlderTimelineRef.current
          ? { ...merged, next_cursor: current.next_cursor, has_more: current.has_more }
          : merged;
      });
      if (mode === "older") loadedOlderTimelineRef.current = true;
      applyTaskSnapshot(loaded.task);
    } catch (error) {
      if (
        mountedRef.current
        && timelineRequestsRef.current.get(channel)?.id === requestId
        && selectedTaskIdRef.current === taskId
        && !(error instanceof DOMException && error.name === "AbortError")
      ) setTimelineError(mode === "older" ? "更早对话加载失败，请重试" : "对话加载失败，请重试");
    } finally {
      finishRequest(controller);
      const ownsChannel = timelineRequestsRef.current.get(channel)?.id === requestId;
      if (ownsChannel) {
        timelineRequestsRef.current.delete(channel);
      }
      if (mountedRef.current && ownsChannel) {
        if (mode === "initial") setTimelineLoading(false);
        if (mode === "older") setLoadingOlder(false);
      }
    }
  }, [applyTaskSnapshot, registerResourcePages, writeTimeline]);

  const loadTimelineResource = useCallback(async (taskId: string, kind: ResourcePageKind) => {
    if (timelineRequestsRef.current.has(kind) || selectedTaskIdRef.current !== taskId) return;
    const current = timelineRef.current;
    if (!current || current.task.id !== taskId) return;
    const page = resourceQueuesRef.current[kind][0];
    if (!page) return;
    const options = kind === "events"
      ? { eventBefore: page.cursor as number }
      : kind === "artifacts"
        ? { artifactAfter: page.cursor as string }
        : kind === "confirmations"
          ? { confirmationAfter: page.cursor as string }
          : { attachmentAfter: page.cursor as string };
    const controller = new AbortController();
    const requestId = startTimelineRequest(kind, controller);
    setLoadingResources((current) => new Set(current).add(kind));
    setTimelineError("");
    try {
      const loaded = await getTimeline(taskId, { turnLimit: 100, eventLimit: 1000, ...(page.before ? { before: page.before } : {}), ...options, signal: controller.signal });
      if (!mountedRef.current || selectedTaskIdRef.current !== taskId || timelineRequestsRef.current.get(kind)?.id !== requestId) return;
      const remaining = resourceQueuesRef.current[kind].slice(1);
      const nextQueue = resourceHasMore(loaded, kind)
        ? [{ before: page.before, cursor: resourceCursor(loaded, kind) }, ...remaining]
        : remaining;
      writeResourceQueues((queues) => ({ ...queues, [kind]: nextQueue }));
      writeTimeline((value) => value && value.task.id === taskId
        ? applyResourceQueue(mergeResourcePage(value, loaded, kind), kind, nextQueue)
        : value);
    } catch (error) {
      if (mountedRef.current && selectedTaskIdRef.current === taskId && !(error instanceof DOMException && error.name === "AbortError")) {
        setTimelineError("资源加载失败，请重试");
      }
    } finally {
      finishRequest(controller);
      if (timelineRequestsRef.current.get(kind)?.id === requestId) {
        timelineRequestsRef.current.delete(kind);
        if (mountedRef.current) setLoadingResources((current) => {
          const next = new Set(current);
          next.delete(kind);
          return next;
        });
      }
    }
  }, [writeResourceQueues, writeTimeline]);

  const cancelScheduledTimelineRefresh = useCallback(() => {
    if (timelineRefreshTimerRef.current) clearTimeout(timelineRefreshTimerRef.current);
    timelineRefreshTimerRef.current = null;
    timelineRefreshTaskRef.current = null;
  }, []);

  const scheduleSelectedTimelineRefresh = useCallback((taskId: string) => {
    if (!mountedRef.current || selectedTaskIdRef.current !== taskId) return;
    if (timelineRefreshTimerRef.current && timelineRefreshTaskRef.current !== taskId) {
      clearTimeout(timelineRefreshTimerRef.current);
      timelineRefreshTimerRef.current = null;
    }
    timelineRefreshTaskRef.current = taskId;
    if (timelineRefreshTimerRef.current) return;
    timelineRefreshTimerRef.current = setTimeout(() => {
      timelineRefreshTimerRef.current = null;
      const scheduledTaskId = timelineRefreshTaskRef.current;
      timelineRefreshTaskRef.current = null;
      if (!mountedRef.current || scheduledTaskId !== taskId || selectedTaskIdRef.current !== taskId) return;
      void loadSelectedTimeline(taskId, "recent");
    }, 40);
  }, [loadSelectedTimeline]);

  const refreshStats = useCallback(async () => {
    statsRequestRef.current?.controller.abort();
    const controller = new AbortController();
    const requestId = ++requestSequenceRef.current;
    statsRequestRef.current = { id: requestId, controller };
    controllersRef.current.add(controller);
    try {
      const value = await getStats({ signal: controller.signal });
      if (mountedRef.current && statsRequestRef.current?.id === requestId) setStats(value);
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        // Global statistics are supplementary; the task workflow remains usable.
      }
    } finally {
      controllersRef.current.delete(controller);
      if (statsRequestRef.current?.id === requestId) statsRequestRef.current = null;
    }
  }, []);

  const cancelScheduledStatsRefresh = useCallback(() => {
    if (statsRefreshTimerRef.current) clearTimeout(statsRefreshTimerRef.current);
    statsRefreshTimerRef.current = null;
  }, []);

  const scheduleStatsRefresh = useCallback(() => {
    if (!mountedRef.current || statsRefreshTimerRef.current) return;
    statsRefreshTimerRef.current = setTimeout(() => {
      statsRefreshTimerRef.current = null;
      if (mountedRef.current) void refreshStats();
    }, 40);
  }, [refreshStats]);

  useEffect(() => {
    mountedRef.current = true;
    void load();
    const controller = new AbortController();
    controllersRef.current.add(controller);
    void runtimeCapabilities({ signal: controller.signal }).then((value) => {
      if (mountedRef.current && !controller.signal.aborted) setCapabilities(value);
    }).catch((error) => {
      if (mountedRef.current && !(error instanceof DOMException && error.name === "AbortError")) setCapabilities([]);
    }).finally(() => finishRequest(controller));
    const feedbackController = new AbortController();
    const feedbackGeneration = ++feedbackPreloadGenerationRef.current;
    feedbackPreloadRef.current = { generation: feedbackGeneration, controller: feedbackController };
    controllersRef.current.add(feedbackController);
    void Promise.resolve(listPendingFeedback({ page_size: 50 }, feedbackController.signal)).then((page) => {
      if (!mountedRef.current || feedbackController.signal.aborted || feedbackLoadRef.current || feedbackPreloadGenerationRef.current !== feedbackGeneration) return;
      setFeedbackPending(page.items);
      setPendingFeedbackCount(page.meta.total);
      feedbackPendingCacheRef.current = { items: page.items, count: page.meta.total };
    }).catch(() => undefined).finally(() => finishRequest(feedbackController));
    void refreshStats();
    return () => {
      mountedRef.current = false;
      cancelScheduledTimelineRefresh();
      cancelScheduledStatsRefresh();
      streamRef.current?.close();
      streamRef.current = null;
      cancelActiveChase();
      cancelPageRequests();
      feedbackLoadRef.current?.controller.abort();
      feedbackPreloadRef.current?.controller.abort();
      feedbackSubmitRef.current.controller.abort();
      for (const controller of controllersRef.current) controller.abort();
      controllersRef.current.clear();
    };
  }, [cancelActiveChase, cancelPageRequests, cancelScheduledStatsRefresh, cancelScheduledTimelineRefresh, load, refreshStats]);

  useEffect(() => {
    cancelScheduledTimelineRefresh();
    streamRef.current?.close();
    streamRef.current = null;
    for (const request of timelineRequestsRef.current.values()) request.controller.abort();
    timelineRequestsRef.current.clear();
    setConnectionError("");
    setTimelineError("");
    setLoadingOlder(false);
    setLoadingResources(new Set());
    writeResourceQueues(emptyResourcePageQueues());
    loadedOlderTimelineRef.current = false;
    if (!selectedTaskId) {
      writeTimeline(null);
      setTimelineLoading(false);
      return;
    }
    writeTimeline(null);
    void loadSelectedTimeline(selectedTaskId, "initial");
    return () => {
      for (const request of timelineRequestsRef.current.values()) request.controller.abort();
      timelineRequestsRef.current.clear();
      streamRef.current?.close();
      streamRef.current = null;
    };
  }, [cancelScheduledTimelineRefresh, loadSelectedTimeline, selectedTaskId, writeResourceQueues, writeTimeline]);

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
  const activeTurn = useMemo(
    () => timeline?.turns.find((turn) => activeTurnStatuses.includes(turn.status)) ?? null,
    [timeline],
  );
  const selectedRuntimeCapabilities = useMemo(
    () => capabilities === null
      ? undefined
      : capabilities.find((runtime) => runtime.kind === selectedTask?.runtime_kind)?.capabilities ?? null,
    [capabilities, selectedTask?.runtime_kind],
  );

  const closeFeedback = useCallback(() => {
    if (feedbackSubmitting) return;
    feedbackLoadRef.current?.controller.abort();
    feedbackLoadRef.current = null;
    setFeedbackOpen(false);
    setFeedbackError("");
  }, [feedbackSubmitting]);

  const openFeedback = useCallback(() => {
    feedbackPreloadGenerationRef.current += 1;
    feedbackPreloadRef.current?.controller.abort();
    feedbackPreloadRef.current = null;
    feedbackLoadRef.current?.controller.abort();
    const controller = new AbortController();
    const requestId = nextRequest(controller);
    feedbackLoadRef.current = { id: requestId, controller };
    setFeedbackOpen(true);
    const cached = feedbackPendingCacheRef.current;
    setFeedbackLoading(!cached);
    setFeedbackError("");
    const resumable = feedbackSubmitRef.current.keys.length > 0 && Boolean(
      feedbackSubmitRef.current.batchId || feedbackSubmitRef.current.batch || feedbackSubmitRef.current.turn,
    );
    setFeedbackSelected(new Set(resumable ? feedbackSubmitRef.current.keys : []));
    if (!resumable) {
      feedbackSubmitRef.current.batch = null;
      feedbackSubmitRef.current.batchId = "";
      feedbackSubmitRef.current.task = null;
      feedbackSubmitRef.current.turn = null;
      feedbackSubmitRef.current.associated = false;
      feedbackSubmitRef.current.keys = [];
    }
    if (cached) {
      setFeedbackPending(cached.items);
      setPendingFeedbackCount(cached.count);
      finishRequest(controller);
      feedbackLoadRef.current = null;
      return;
    }
    void listPendingFeedback({ page_size: 50 }, controller.signal).then((page) => {
      if (!mountedRef.current || feedbackLoadRef.current?.id !== requestId || controller.signal.aborted) return;
      setFeedbackPending(page.items);
      setPendingFeedbackCount(page.meta.total);
      feedbackPendingCacheRef.current = { items: page.items, count: page.meta.total };
    }).catch((error) => {
      if (mountedRef.current && feedbackLoadRef.current?.id === requestId && !(error instanceof DOMException && error.name === "AbortError")) {
        setFeedbackError("反馈加载失败，请重试");
      }
    }).finally(() => {
      finishRequest(controller);
      if (feedbackLoadRef.current?.id === requestId && mountedRef.current) {
        feedbackLoadRef.current = null;
        setFeedbackLoading(false);
      }
    });
  }, []);

  const toggleFeedback = useCallback((key: string) => {
    if (feedbackSubmitRef.current.batch || feedbackSubmitRef.current.turn) return;
    setFeedbackSelected((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }, []);

  const selectAllFeedback = useCallback(() => {
    if (feedbackSubmitRef.current.batch || feedbackSubmitRef.current.turn) return;
    setFeedbackSelected((current) => {
      const keys = feedbackPending.map(feedbackKey);
      const allSelected = keys.length > 0 && keys.every((key) => current.has(key));
      return allSelected ? new Set() : new Set(keys);
    });
  }, [feedbackPending]);

  const importFeedback = useCallback(async () => {
    if (feedbackSubmitting) return;
    const keys = feedbackPending.map(feedbackKey).filter((key) => feedbackSelected.has(key));
    if (keys.length === 0) return;
    const workflow = feedbackSubmitRef.current;
    if (workflow.keys.join("\u0000") !== keys.join("\u0000")) {
      workflow.batch = null;
      workflow.batchId = "";
      workflow.task = null;
      workflow.turn = null;
      workflow.associated = false;
      workflow.keys = keys;
    }
    if (!workflow.batchId) workflow.batchId = `feedback-import:${keys.join(",")}`;
    workflow.controller.abort();
    workflow.controller = new AbortController();
    const controller = workflow.controller;
    setFeedbackSubmitting(true);
    setFeedbackError("");
    try {
      let task = workflow.task;
      if (!task) {
        task = selectedTaskIdRef.current
          ? tasksRef.current.find((candidate) => candidate.id === selectedTaskIdRef.current) ?? null
          : null;
        if (!task) {
          task = await createTask("处理反馈", "codex", { signal: controller.signal });
          if (!mountedRef.current || controller.signal.aborted) return;
          recordCreatedTask(task);
          writeTasks((current) => [task!, ...current.filter((candidate) => candidate.id !== task!.id)]);
          scheduleStatsRefresh();
        }
        workflow.task = task;
      }
      if (!workflow.batch) {
        const claimed = await claimFeedbackBatch(keys, task.id, "", workflow.batchId, { signal: controller.signal });
        if (!claimed.ok) throw new Error(claimed.message);
        workflow.batch = claimed.item;
      }
      const batch = workflow.batch;
      if (!batch.start_message) throw new Error("反馈批次缺少启动消息");
      if (!workflow.turn) {
        workflow.turn = await createTurn(task.id, batch.start_message, `feedback-import:${batch.batch_id}`, { signal: controller.signal });
      }
      if (!workflow.associated) {
        const associated = await associateFeedbackTurn(batch.batch_id, task.id, workflow.turn.id, { signal: controller.signal });
        if (!associated.ok) throw new Error(associated.message);
        workflow.associated = true;
      }
      if (!mountedRef.current || controller.signal.aborted) return;
      selectTask(task.id);
      setFeedbackOpen(false);
      setFeedbackError("");
      setFeedbackSelected(new Set());
      const remainingFeedback = feedbackPending.filter((item) => !keys.includes(feedbackKey(item)));
      const remainingCount = Math.max(0, pendingFeedbackCount - keys.length);
      setFeedbackPending(remainingFeedback);
      setPendingFeedbackCount(remainingCount);
      feedbackPreloadGenerationRef.current += 1;
      feedbackPreloadRef.current?.controller.abort();
      feedbackPreloadRef.current = null;
      feedbackPendingCacheRef.current = { items: remainingFeedback, count: remainingCount };
      workflow.batch = null;
      workflow.batchId = "";
      workflow.task = null;
      workflow.turn = null;
      workflow.associated = false;
      workflow.keys = [];
    } catch (error) {
      if (mountedRef.current && !(error instanceof DOMException && error.name === "AbortError")) {
        setFeedbackError("反馈导入未完成；可重试继续处理");
      }
    } finally {
      if (mountedRef.current && workflow.controller === controller) setFeedbackSubmitting(false);
    }
  }, [feedbackPending, feedbackSelected, feedbackSubmitting, pendingFeedbackCount, scheduleStatsRefresh, selectTask, writeTasks]);

  useEffect(() => {
    streamRef.current?.close();
    streamRef.current = null;
    if (!selectedTaskId || !activeTurn) return;
    const taskId = selectedTaskId;
    const turnId = activeTurn.id;
    const after = timeline?.events.reduce((maximum, event) => Math.max(maximum, event.id), 0) ?? 0;
    const connection = new EventStreamConnection({
      turnId,
      after,
      onOpen: () => {
        if (selectedTaskIdRef.current === taskId) setConnectionError("");
      },
      onConnectionError: (message) => {
        if (selectedTaskIdRef.current === taskId) setConnectionError(message);
      },
      onEvent: (event) => {
        if (selectedTaskIdRef.current !== taskId || event.turn_id !== turnId) return;
        const nextStatus = statusFromEvent(event);
        const confirmationProgress = confirmationProgressFromEvent(event);
        const terminal = nextStatus !== null && ["completed", "stopped", "failed"].includes(nextStatus);
        writeTimeline((current) => {
          if (!current || current.task.id !== taskId) return current;
          const currentEventState = createEventState(current.events);
          if (event.id <= currentEventState.lastEventId) return current;
          const eventState = applyWorkbenchEvent(currentEventState, event);
          const turns = nextStatus
            ? current.turns.map((turn) => turn.id === turnId ? { ...turn, status: nextStatus } : turn)
            : current.turns;
          const confirmations = confirmationProgress?.id && confirmationProgress.status
            ? current.confirmations.map((confirmation) => confirmation.id === confirmationProgress.id
              ? { ...confirmation, status: confirmationProgress.status as ConfirmationStatus }
              : confirmation)
            : current.confirmations;
          return {
            ...current,
            events: eventState.events,
            turns,
            confirmations,
            task: nextStatus ? { ...current.task, state: nextStatus } : current.task,
          };
        });
        if (nextStatus) {
          writeTasks((current) => current.map((task) => task.id === taskId ? { ...task, state: nextStatus } : task));
        }
        if (event.event_type === "confirmation_required" || event.event_type === "artifact_created" || confirmationProgress || terminal) {
          scheduleSelectedTimelineRefresh(taskId);
        }
        if (event.event_type === "artifact_created" || terminal) scheduleStatsRefresh();
      },
    });
    streamRef.current = connection;
    connection.start();
    return () => {
      connection.close();
      if (streamRef.current === connection) streamRef.current = null;
    };
  }, [activeTurn?.id, scheduleSelectedTimelineRefresh, scheduleStatsRefresh, selectedTaskId, writeTasks, writeTimeline]);

  async function decideConfirmation(confirmation: Confirmation, decision: "confirm" | "cancel") {
    const taskId = selectedTaskIdRef.current;
    if (!taskId || confirmationMutationsRef.current.has(confirmation.id)) return;
    confirmationMutationsRef.current.add(confirmation.id);
    try {
      const decided = await (decision === "confirm" ? confirmAction : cancelAction)(
        taskId,
        confirmation.turn_id,
        confirmation.id,
      );
      if (selectedTaskIdRef.current !== taskId) return;
      writeTimeline((current) => current && current.task.id === taskId ? {
        ...current,
        confirmations: current.confirmations.map((item) => item.id === decided.id ? decided : item),
      } : current);
      scheduleStatsRefresh();
      await loadSelectedTimeline(taskId, "recent");
    } catch (error) {
      if (selectedTaskIdRef.current === taskId) await loadSelectedTimeline(taskId, "recent");
      throw error;
    } finally {
      confirmationMutationsRef.current.delete(confirmation.id);
    }
  }

  async function handleTurnCreated(turn: Turn) {
    const taskId = selectedTaskIdRef.current;
    if (!taskId || turn.task_id !== taskId) return;
    writeTimeline((current) => current && current.task.id === taskId ? {
      ...current,
      task: { ...current.task, state: turn.status },
      turns: [turn, ...current.turns.filter((item) => item.id !== turn.id)],
    } : current);
    writeTasks((current) => current.map((task) => task.id === taskId ? { ...task, state: turn.status } : task));
    scheduleStatsRefresh();
    await loadSelectedTimeline(taskId, "recent");
  }

  async function handleTurnStopped(turn: Turn) {
    const taskId = selectedTaskIdRef.current;
    if (!taskId || turn.task_id !== taskId) return;
    writeTimeline((current) => current && current.task.id === taskId ? {
      ...current,
      turns: current.turns.map((item) => item.id === turn.id ? turn : item),
      task: { ...current.task, state: turn.status },
    } : current);
    writeTasks((current) => current.map((task) => task.id === taskId ? { ...task, state: turn.status } : task));
    scheduleStatsRefresh();
  }

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
      scheduleStatsRefresh();
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
      scheduleStatsRefresh();
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
    <div className={`workbench-root${showGlobalNav ? "" : " workbench-embedded"}`}>
      {showGlobalNav && <GlobalNav activePath="/" />}
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
              pendingFeedbackCount={pendingFeedbackCount}
              onProcessFeedback={openFeedback}
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
          <section className="conversation-workspace" aria-labelledby="conversation-title">
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
            <div className="stream-slot">
              {connectionError && <p className="stream-alert" role="alert">{connectionError}</p>}
            </div>
            {timelineLoading ? (
              <div className="conversation-empty" role="status"><p>正在加载对话…</p></div>
            ) : timelineError && !timeline ? (
              <div className="conversation-empty" role="alert">
                <h3>对话加载失败</h3>
                <p>{timelineError}</p>
                <button type="button" className="secondary-button" onClick={() => void loadSelectedTimeline(selectedTask.id, "initial")}>重试</button>
              </div>
            ) : timeline ? (
              <>
                <div className="conversation-body">
                  <div className="resource-pagination" aria-label="对话资源分页">
                    {resourceQueues.events.length > 0 && <button type="button" className="secondary-button" disabled={loadingResources.has("events")} onClick={() => void loadTimelineResource(selectedTask.id, "events")}>加载更多事件</button>}
                    {resourceQueues.artifacts.length > 0 && <button type="button" className="secondary-button" disabled={loadingResources.has("artifacts")} onClick={() => void loadTimelineResource(selectedTask.id, "artifacts")}>加载更多产物</button>}
                    {resourceQueues.confirmations.length > 0 && <button type="button" className="secondary-button" disabled={loadingResources.has("confirmations")} onClick={() => void loadTimelineResource(selectedTask.id, "confirmations")}>加载更多确认</button>}
                    {resourceQueues.attachments.length > 0 && <button type="button" className="secondary-button" disabled={loadingResources.has("attachments")} onClick={() => void loadTimelineResource(selectedTask.id, "attachments")}>加载更多附件</button>}
                  </div>
                  {timeline.has_more && (
                    <button
                      type="button"
                      className="load-older-button secondary-button"
                      disabled={loadingOlder}
                      aria-label="加载更早对话"
                      onClick={() => void loadSelectedTimeline(selectedTask.id, "older", timeline.next_cursor)}
                    >{loadingOlder ? "正在加载…" : "加载更早对话"}</button>
                  )}
                  {timelineError && <p className="inline-alert" role="alert">{timelineError}</p>}
                  {timeline.turns.length ? (
                    <ConversationTimeline
                      timeline={timeline}
                      activeTurnId={activeTurn?.id ?? null}
                      onConfirm={(confirmation) => decideConfirmation(confirmation, "confirm")}
                      onCancel={(confirmation) => decideConfirmation(confirmation, "cancel")}
                    />
                  ) : (
                    <div className="conversation-empty">
                      <Sparkles aria-hidden="true" size={26} />
                      <h3>开始新的对话</h3>
                      <p>发送消息后，回复、执行步骤和产物会显示在这里。</p>
                    </div>
                  )}
                </div>
                <Composer
                  taskId={selectedTask.id}
                  activeTurn={activeTurn}
                  attachments={timeline.attachments}
                  capabilities={selectedRuntimeCapabilities}
                  onAttachmentUploaded={scheduleStatsRefresh}
                  onTurnCreated={handleTurnCreated}
                  onTurnStopped={handleTurnStopped}
                />
              </>
            ) : null}
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
          <TurnInspector task={selectedTask} timeline={timeline} capabilities={capabilities} stats={stats} />
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
            <TurnInspector task={selectedTask} timeline={timeline} capabilities={capabilities} stats={stats} />
          </div>
        </>
      )}
      <FeedbackDrawer
        open={feedbackOpen}
        pending={feedbackPending}
        loading={feedbackLoading}
        error={feedbackError}
        selected={feedbackSelected}
        submitting={feedbackSubmitting}
        onToggle={toggleFeedback}
        onSelectAll={selectAllFeedback}
        onImport={importFeedback}
        onClose={closeFeedback}
      />
      </div>
    </div>
  );
}
