import type { EventType, TurnStatus, WorkbenchEvent } from "./types";

const eventTypes: readonly EventType[] = [
  "text_delta",
  "thinking_summary",
  "tool_started",
  "tool_completed",
  "file_changed",
  "artifact_created",
  "confirmation_required",
  "status_changed",
  "turn_completed",
  "turn_failed",
];

const payloadFields: Record<EventType, ReadonlySet<string>> = {
  text_delta: new Set(["text"]),
  thinking_summary: new Set(["text", "summary"]),
  tool_started: new Set([
    "tool_call_id", "kind", "name", "native_id", "status", "summary", "command", "cwd",
    "exit_code", "output", "server", "tool", "arguments", "result", "provider_item",
  ]),
  tool_completed: new Set([
    "tool_call_id", "kind", "name", "native_id", "status", "summary", "command", "cwd",
    "exit_code", "output", "server", "tool", "arguments", "result", "provider_item",
  ]),
  file_changed: new Set(["filename", "path", "change", "status"]),
  artifact_created: new Set(["artifact_id", "label", "filename", "path", "media_type"]),
  confirmation_required: new Set(["action_kind", "confirmation_id", "target", "summary", "risk"]),
  status_changed: new Set(["status", "code", "confirmation_id", "confirmation_status"]),
  turn_completed: new Set(["status"]),
  turn_failed: new Set(["status", "code", "confirmation_id"]),
};

export interface EventState {
  events: WorkbenchEvent[];
  lastEventId: number;
}

export interface TimelineBlock {
  kind: "markdown" | "thinking" | "tool" | "file" | "confirmation" | "artifact";
  key: string;
  eventId: number;
  text?: string;
  status?: string;
  payload?: Record<string, unknown>;
  confirmationId?: string;
  artifactId?: string;
  startedAt?: string;
  completedAt?: string;
}

const terminalTurnStatuses = new Set<TurnStatus>(["completed", "stopped", "failed"]);
const abortedToolSummary = "任务已结束，未收到工具完成事件。";
const stoppedCommandSummary = "任务已停止，命令未返回退出码。";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isPublicEventPayload(eventType: EventType, payload: Record<string, unknown>): boolean {
  const keys = Object.keys(payload);
  if (keys.some((key) => !payloadFields[eventType].has(key))) return false;
  if (eventType === "tool_started" || eventType === "tool_completed") {
    if (!keys.every((key) => isBoundedJson(payload[key]))) return false;
    if (typeof payload.tool_call_id !== "string") return false;
    if (payload.kind !== undefined && payload.kind !== "command" && payload.kind !== "mcp") return false;
    if (payload.status !== undefined && typeof payload.status !== "string") return false;
    return true;
  }
  if (keys.some((key) => typeof payload[key] !== "string")) return false;
  if (eventType === "text_delta") return typeof payload.text === "string";
  if (eventType === "thinking_summary") return typeof payload.text === "string" || typeof payload.summary === "string";
  if (eventType === "artifact_created") return typeof payload.artifact_id === "string";
  if (eventType === "confirmation_required") return typeof payload.confirmation_id === "string";
  if (["status_changed", "turn_completed", "turn_failed"].includes(eventType)) return typeof payload.status === "string";
  return true;
}

function isBoundedJson(value: unknown): boolean {
  let remaining = 2048;
  const visit = (candidate: unknown, depth: number): boolean => {
    remaining -= 1;
    if (remaining < 0 || depth > 12) return false;
    if (candidate === null || typeof candidate === "string" || typeof candidate === "boolean") return true;
    if (typeof candidate === "number") return Number.isFinite(candidate);
    if (Array.isArray(candidate)) return candidate.every((item) => visit(item, depth + 1));
    if (!isRecord(candidate)) return false;
    return Object.entries(candidate).every(([key, item]) => key.length <= 256 && visit(item, depth + 1));
  };
  return visit(value, 0);
}

function validEvent(value: unknown): value is WorkbenchEvent {
  if (!isRecord(value)) return false;
  return Number.isInteger(value.id)
    && (value.id as number) > 0
    && Number.isInteger(value.sequence)
    && (value.sequence as number) > 0
    && typeof value.turn_id === "string"
    && value.turn_id.length > 0
    && typeof value.event_type === "string"
    && eventTypes.includes(value.event_type as EventType)
    && isRecord(value.payload)
    && isPublicEventPayload(value.event_type as EventType, value.payload)
    && typeof value.created_at === "string";
}

export function parseStreamEvent(data: string, expectedType: EventType): WorkbenchEvent | null {
  try {
    const value: unknown = JSON.parse(data);
    return validEvent(value) && value.event_type === expectedType ? value : null;
  } catch {
    return null;
  }
}

export function createEventState(events: WorkbenchEvent[] = []): EventState {
  const accepted = new Map<number, WorkbenchEvent>();
  for (const event of events) {
    if (validEvent(event)) accepted.set(event.id, event);
  }
  const ordered = [...accepted.values()].sort((left, right) => left.id - right.id);
  return { events: ordered, lastEventId: ordered.at(-1)?.id ?? 0 };
}

export function applyWorkbenchEvent(state: EventState, event: WorkbenchEvent): EventState {
  if (!validEvent(event) || event.id <= state.lastEventId) return state;
  return { events: [...state.events, event], lastEventId: event.id };
}

function payloadText(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === "string" ? value : "";
}

export function timelineBlocks(turnId: string, events: WorkbenchEvent[], turnStatus?: TurnStatus): TimelineBlock[] {
  const blocks: TimelineBlock[] = [];
  const tools = new Map<string, number>();
  let adjacentTextBlock: number | null = null;
  for (const event of events) {
    if (event.turn_id !== turnId || !validEvent(event)) continue;
    if (event.event_type === "text_delta") {
      const text = payloadText(event.payload, "text");
      if (!text) continue;
      if (adjacentTextBlock !== null) {
        const previous = blocks[adjacentTextBlock];
        previous.text = `${previous.text ?? ""}${text}`;
      } else {
        blocks.push({ kind: "markdown", key: `event:${event.id}`, eventId: event.id, text });
        adjacentTextBlock = blocks.length - 1;
      }
      continue;
    }
    adjacentTextBlock = null;
    if (event.event_type === "thinking_summary") {
      const text = payloadText(event.payload, "summary") || payloadText(event.payload, "text");
      if (text) blocks.push({ kind: "thinking", key: `event:${event.id}`, eventId: event.id, text });
      continue;
    }
    if (event.event_type === "tool_started" || event.event_type === "tool_completed") {
      const callId = payloadText(event.payload, "tool_call_id") || `event-${event.id}`;
      const existing = tools.get(callId);
      const status = event.event_type === "tool_started"
        ? "running"
        : payloadText(event.payload, "status") || "completed";
      if (existing !== undefined) {
        blocks[existing] = {
          ...blocks[existing],
          status,
          payload: { ...blocks[existing].payload, ...event.payload },
          completedAt: event.event_type === "tool_completed" ? event.created_at : blocks[existing].completedAt,
        };
      } else {
        tools.set(callId, blocks.length);
        blocks.push({
          kind: "tool",
          key: `event:${event.id}`,
          eventId: event.id,
          status,
          payload: event.payload,
          startedAt: event.event_type === "tool_started" ? event.created_at : undefined,
          completedAt: event.event_type === "tool_completed" ? event.created_at : undefined,
        });
      }
      continue;
    }
    if (event.event_type === "file_changed") {
      blocks.push({ kind: "file", key: `event:${event.id}`, eventId: event.id, status: payloadText(event.payload, "status"), payload: event.payload });
      continue;
    }
    if (event.event_type === "confirmation_required") {
      const confirmationId = payloadText(event.payload, "confirmation_id");
      if (confirmationId) blocks.push({ kind: "confirmation", key: `confirmation:${confirmationId}`, eventId: event.id, confirmationId });
      continue;
    }
    if (event.event_type === "artifact_created") {
      const artifactId = payloadText(event.payload, "artifact_id");
      if (artifactId) blocks.push({ kind: "artifact", key: `event:${event.id}`, eventId: event.id, artifactId });
    }
  }
  if (!turnStatus || !terminalTurnStatuses.has(turnStatus)) return blocks;
  return blocks.map((block) => {
    if (block.kind !== "tool") return block;
    if (block.status === "running") {
      return { ...block, status: "aborted", payload: { ...block.payload, summary: abortedToolSummary } };
    }
    if (
      turnStatus === "stopped"
      && block.status === "failed"
      && block.payload?.kind === "command"
      && typeof block.payload?.exit_code !== "number"
    ) {
      return { ...block, status: "aborted", payload: { ...(block.payload ?? {}), summary: stoppedCommandSummary } };
    }
    return block;
  });
}

interface EventSourceLike {
  onopen: ((event: Event) => void) | null;
  onerror: ((event: Event) => void) | null;
  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void;
  close(): void;
}

export interface EventStreamOptions {
  turnId: string;
  after: number;
  onEvent: (event: WorkbenchEvent) => void;
  onConnectionError: (message: string) => void;
  onOpen?: () => void;
  eventSourceFactory?: (url: string) => EventSourceLike;
}

export class EventStreamConnection {
  private source: EventSourceLike | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stabilityTimer: ReturnType<typeof setTimeout> | null = null;
  private cursor: number;
  private retryCount = 0;
  private stopped = false;

  constructor(private readonly options: EventStreamOptions) {
    this.cursor = Math.max(0, Math.trunc(options.after));
  }

  start() {
    if (this.stopped || this.source) return;
    const url = `/api/workbench/turns/${encodeURIComponent(this.options.turnId)}/events/stream?after=${this.cursor}`;
    const source = this.options.eventSourceFactory?.(url) ?? new EventSource(url);
    this.source = source;
    source.onopen = () => {
      if (this.source !== source || this.stopped) return;
      if (this.stabilityTimer) clearTimeout(this.stabilityTimer);
      this.stabilityTimer = setTimeout(() => {
        this.stabilityTimer = null;
        if (this.source === source && !this.stopped) this.retryCount = 0;
      }, 10_000);
      this.options.onOpen?.();
    };
    for (const eventType of eventTypes) {
      source.addEventListener(eventType, ((message: MessageEvent) => {
        if (this.source !== source || this.stopped) return;
        const event = parseStreamEvent(message.data, eventType);
        if (!event || event.turn_id !== this.options.turnId || event.id <= this.cursor) {
          if (!event) this.options.onConnectionError("收到无法解析的更新，已保留现有内容");
          return;
        }
        this.cursor = event.id;
        this.retryCount = 0;
        if (this.stabilityTimer) clearTimeout(this.stabilityTimer);
        this.stabilityTimer = null;
        this.options.onEvent(event);
        const status = event.payload.status;
        if (
          event.event_type === "turn_completed"
          || event.event_type === "turn_failed"
          || (event.event_type === "status_changed" && ["completed", "stopped", "failed"].includes(String(status)))
        ) this.close();
      }) as EventListener);
    }
    source.onerror = () => {
      if (this.source !== source || this.stopped) return;
      source.close();
      this.source = null;
      if (this.stabilityTimer) clearTimeout(this.stabilityTimer);
      this.stabilityTimer = null;
      this.options.onConnectionError("实时连接中断，正在恢复");
      const delay = Math.min(8_000, 500 * (2 ** this.retryCount));
      this.retryCount += 1;
      if (this.timer) clearTimeout(this.timer);
      this.timer = setTimeout(() => {
        this.timer = null;
        this.start();
      }, delay);
    };
  }

  close() {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (this.stabilityTimer) clearTimeout(this.stabilityTimer);
    this.stabilityTimer = null;
    this.source?.close();
    this.source = null;
  }
}
