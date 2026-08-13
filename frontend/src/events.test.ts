import { describe, expect, it, vi } from "vitest";

import {
  EventStreamConnection,
  applyWorkbenchEvent,
  createEventState,
  parseStreamEvent,
  timelineBlocks,
} from "./events";
import type { WorkbenchEvent } from "./types";

function event(
  id: number,
  event_type: WorkbenchEvent["event_type"],
  payload: Record<string, unknown>,
  turn_id = "turn-1",
): WorkbenchEvent {
  return { id, turn_id, sequence: id, event_type, payload, created_at: "2026-08-13 10:00:00" };
}

class FakeEventSource {
  readonly listeners = new Map<string, Array<(event: MessageEvent) => void>>();
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(readonly url: string) {}

  addEventListener(name: string, listener: EventListenerOrEventListenerObject) {
    const callback = listener as (event: MessageEvent) => void;
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), callback]);
  }

  emit(value: WorkbenchEvent, name = value.event_type) {
    const message = new MessageEvent(name, { data: JSON.stringify(value), lastEventId: String(value.id) });
    for (const listener of this.listeners.get(name) ?? []) listener(message);
  }

  close() {
    this.closed = true;
  }
}

describe("workbench event reducer", () => {
  it("drops invalid and globally duplicate IDs while coalescing only adjacent text", () => {
    let state = createEventState([event(2, "text_delta", { text: "A" })]);
    state = applyWorkbenchEvent(state, event(2, "text_delta", { text: "duplicate" }));
    state = applyWorkbenchEvent(state, event(3, "text_delta", { text: "B" }));
    state = applyWorkbenchEvent(state, event(4, "tool_started", { tool: "read", tool_call_id: "call-1" }));
    state = applyWorkbenchEvent(state, event(5, "text_delta", { text: "C" }));
    state = applyWorkbenchEvent(state, event(-1, "text_delta", { text: "invalid" }));

    expect(state.lastEventId).toBe(5);
    expect(timelineBlocks("turn-1", state.events).map((block) => [block.kind, block.key, block.text])).toEqual([
      ["markdown", "event:2", "AB"],
      ["tool", "event:4", undefined],
      ["markdown", "event:5", "C"],
    ]);
  });

  it("correlates interleaved tool completion without changing first-seen ordering", () => {
    const state = createEventState([
      event(1, "tool_started", { tool: "shell", tool_call_id: "a", summary: "first" }),
      event(2, "tool_started", { tool: "read", tool_call_id: "b", summary: "second" }),
      event(3, "tool_completed", { tool: "read", tool_call_id: "b", status: "completed" }),
      event(4, "tool_completed", { tool: "shell", tool_call_id: "a", status: "failed" }),
    ]);

    const blocks = timelineBlocks("turn-1", state.events);
    expect(blocks.map((block) => block.key)).toEqual(["event:1", "event:2"]);
    expect(blocks.map((block) => block.status)).toEqual(["failed", "completed"]);
  });

  it("does not coalesce text deltas separated by an in-place tool completion", () => {
    const blocks = timelineBlocks("turn-1", createEventState([
      event(1, "tool_started", { tool: "read", tool_call_id: "call-1" }),
      event(2, "text_delta", { text: "before" }),
      event(3, "tool_completed", { tool: "read", tool_call_id: "call-1", status: "completed" }),
      event(4, "text_delta", { text: "after" }),
    ]).events);

    expect(blocks.map((block) => [block.kind, block.text])).toEqual([
      ["tool", undefined],
      ["markdown", "before"],
      ["markdown", "after"],
    ]);
  });

  it("uses exact event IDs for stable rendered block keys", () => {
    const blocks = timelineBlocks("turn-1", createEventState([
      event(10, "text_delta", { text: "A" }),
      event(11, "text_delta", { text: "B" }),
      event(12, "tool_started", { tool: "read", tool_call_id: "call-1" }),
      event(13, "tool_completed", { tool: "read", tool_call_id: "call-1", status: "completed" }),
      event(14, "artifact_created", { artifact_id: "artifact-1" }),
      event(15, "confirmation_required", { confirmation_id: "confirmation-1" }),
    ]).events);

    expect(blocks.map((block) => block.key)).toEqual([
      "event:10",
      "event:12",
      "event:14",
      "confirmation:confirmation-1",
    ]);
  });

  it("rejects fields and value types outside the public event payload schema", () => {
    expect(parseStreamEvent(JSON.stringify(event(1, "text_delta", { text: 42 })), "text_delta")).toBeNull();
    expect(parseStreamEvent(JSON.stringify(event(1, "tool_started", { tool: "read", env: "secret" })), "tool_started")).toBeNull();
    expect(parseStreamEvent(JSON.stringify(event(1, "text_delta", { text: "safe" })), "text_delta")).toEqual(event(1, "text_delta", { text: "safe" }));
  });
});

describe("EventStreamConnection", () => {
  it("reconnects with the latest cursor and drops duplicate and malformed events", () => {
    vi.useFakeTimers();
    const sources: FakeEventSource[] = [];
    const received: WorkbenchEvent[] = [];
    const errors: string[] = [];
    const connection = new EventStreamConnection({
      turnId: "turn / one",
      after: 4,
      eventSourceFactory: (url) => {
        const source = new FakeEventSource(url);
        sources.push(source);
        return source;
      },
      onEvent: (value) => received.push(value),
      onConnectionError: (message) => errors.push(message),
    });

    connection.start();
    expect(sources[0].url).toBe("/api/workbench/turns/turn%20%2F%20one/events/stream?after=4");
    sources[0].emit(event(5, "text_delta", { text: "hello" }, "turn / one"));
    sources[0].emit(event(5, "text_delta", { text: "duplicate" }, "turn / one"));
    for (const listener of sources[0].listeners.get("text_delta") ?? []) {
      listener(new MessageEvent("text_delta", { data: "{bad json" }));
    }
    sources[0].onerror?.();
    expect(sources[0].closed).toBe(true);
    expect(errors.at(-1)).toContain("连接中断");

    vi.advanceTimersByTime(500);
    expect(sources[1].url).toBe("/api/workbench/turns/turn%20%2F%20one/events/stream?after=5");
    expect(received.map((value) => value.id)).toEqual([5]);
    connection.close();
    vi.useRealTimers();
  });

  it("closes on terminal events and cancels pending reconnects", () => {
    vi.useFakeTimers();
    const sources: FakeEventSource[] = [];
    const connection = new EventStreamConnection({
      turnId: "turn-1",
      after: 0,
      eventSourceFactory: (url) => {
        const source = new FakeEventSource(url);
        sources.push(source);
        return source;
      },
      onEvent: vi.fn(),
      onConnectionError: vi.fn(),
    });
    connection.start();
    sources[0].emit(event(1, "turn_completed", { status: "completed" }));
    sources[0].onerror?.();
    vi.runAllTimers();

    expect(sources[0].closed).toBe(true);
    expect(sources).toHaveLength(1);
    vi.useRealTimers();
  });

  it("backs off across open-error flaps and resets only after a valid event", () => {
    vi.useFakeTimers();
    const sources: FakeEventSource[] = [];
    const connection = new EventStreamConnection({
      turnId: "turn-1",
      after: 0,
      eventSourceFactory: (url) => {
        const source = new FakeEventSource(url);
        sources.push(source);
        return source;
      },
      onEvent: vi.fn(),
      onConnectionError: vi.fn(),
    });

    connection.start();
    sources[0].onopen?.();
    sources[0].onerror?.();
    vi.advanceTimersByTime(500);
    expect(sources).toHaveLength(2);

    sources[1].onopen?.();
    sources[1].onerror?.();
    vi.advanceTimersByTime(500);
    expect(sources).toHaveLength(2);
    vi.advanceTimersByTime(500);
    expect(sources).toHaveLength(3);

    sources[2].onopen?.();
    sources[2].onerror?.();
    vi.advanceTimersByTime(1_000);
    expect(sources).toHaveLength(3);
    vi.advanceTimersByTime(1_000);
    expect(sources).toHaveLength(4);

    sources[3].emit(event(1, "text_delta", { text: "stable" }));
    sources[3].onerror?.();
    vi.advanceTimersByTime(500);
    expect(sources).toHaveLength(5);

    connection.close();
    vi.useRealTimers();
  });
});
