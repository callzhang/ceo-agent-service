import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listTasks: vi.fn(),
  createTask: vi.fn(),
  renameTask: vi.fn(),
  archiveTask: vi.fn(),
  getTimeline: vi.fn(),
  getStats: vi.fn(),
  runtimeCapabilities: vi.fn(),
  confirmAction: vi.fn(),
  cancelAction: vi.fn(),
  uploadAttachment: vi.fn(),
  createTurn: vi.fn(),
  stopTurn: vi.fn(),
}));

vi.mock("./api", () => api);

import { App } from "./app";
import styles from "./styles.css?raw";
import type { Task, Timeline, Turn } from "./types";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const first = {
  id: "11111111-1111-4111-8111-111111111111",
  title: "销售策略",
  runtime_kind: "codex",
  archived_at: "",
  state: "running" as const,
  created_at: "2026-08-13 09:00:00",
  updated_at: "2026-08-13 10:00:00",
};
const second = {
  ...first,
  id: "22222222-2222-4222-8222-222222222222",
  title: "产品规划",
  state: "idle" as const,
};
const deep = {
  ...first,
  id: "33333333-3333-4333-8333-333333333333",
  title: "深层任务",
  state: "completed" as const,
};

function emptyTimeline(task: Task = first, turns: Turn[] = []): Timeline {
  return {
    task,
    turns,
    events: [],
    attachments: [],
    artifacts: [],
    confirmations: [],
    next_cursor: "",
    has_more: false,
    events_has_more: false,
    events_next_cursor: 0,
    artifacts_has_more: false,
    artifacts_next_cursor: "",
    confirmations_has_more: false,
    confirmations_next_cursor: "",
    attachments_has_more: false,
    attachments_next_cursor: "",
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getTimeline.mockImplementation((taskId: string) => {
    const task = taskId === second.id ? second : taskId === deep.id ? deep : first;
    return Promise.resolve(emptyTimeline(task));
  });
  api.runtimeCapabilities.mockResolvedValue([{ kind: "codex", capabilities: {
    session_resume: true, streamed_text: true, structured_tools: true, image_input: true,
    model_selection: true, mcp_configuration: true, stoppable: true, recoverable: true,
  } }]);
  api.getStats.mockResolvedValue({
    tasks: { total: 2, active: 2, archived: 0 },
    turns: { queued: 0, running: 0, waiting_confirmation: 0, completed: 0, stopped: 0, failed: 0 },
    confirmations: { pending: 0, confirmed: 0, cancelled: 0, executed: 0, failed: 0 },
    events: {}, attachments: 0, artifacts: 0,
    duration: { completed_count: 0, total_seconds: 0, average_seconds: 0 },
  });
  window.history.replaceState({}, "", "/");
});

afterEach(() => vi.unstubAllGlobals());

function setCompactViewport(compact: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query === "(max-width: 939px)" ? compact : false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

describe("App", () => {
  it("switches to a flexible two-column layout below the 940px desktop minimum", () => {
    expect(styles).toContain("@media (max-width: 939px)");
    expect(styles).toContain("grid-template-columns: minmax(245px, 292px) minmax(0, 1fr)");
  });

  it("shows loading then restores a valid URL selection", async () => {
    let resolveTasks!: (value: { items: typeof first[]; nextCursor: string }) => void;
    api.listTasks.mockReturnValue(
      new Promise((resolve) => {
        resolveTasks = resolve;
      }),
    );
    window.history.replaceState({}, "", `/?task=${first.id}`);

    render(<App />);
    expect(screen.getByText("正在加载任务…")).toBeInTheDocument();

    await act(async () => {
      resolveTasks({ items: [first], nextCursor: "" });
    });
    expect(await screen.findByRole("heading", { name: "销售策略" })).toBeInTheDocument();
    expect(screen.getByText("执行中")).toBeInTheDocument();
  });

  it("pushes explicit selections and follows browser popstate", async () => {
    const user = userEvent.setup();
    api.listTasks.mockResolvedValue({ items: [first, second], nextCursor: "" });
    window.history.replaceState({}, "", `/?task=${first.id}`);
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "打开任务 产品规划" }));
    expect(new URL(window.location.href).searchParams.get("task")).toBe(second.id);
    expect(screen.getByRole("heading", { name: "产品规划" })).toBeInTheDocument();

    act(() => {
      window.history.pushState({}, "", `/?task=${first.id}`);
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(screen.getByRole("heading", { name: "销售策略" })).toBeInTheDocument();
  });

  it("paints the first page before manually requesting more", async () => {
    const user = userEvent.setup();
    api.listTasks
      .mockResolvedValueOnce({ items: [first], nextCursor: "page-2" })
      .mockResolvedValueOnce({ items: [second], nextCursor: "" });
    render(<App />);

    expect(await screen.findByText("销售策略")).toBeInTheDocument();
    expect(api.listTasks).toHaveBeenCalledOnce();
    await user.click(screen.getByRole("button", { name: "加载更多任务" }));
    expect(await screen.findByText("产品规划")).toBeInTheDocument();
    expect(api.listTasks).toHaveBeenNthCalledWith(2, {
      archived: "active",
      limit: 100,
      cursor: "page-2",
      signal: expect.any(AbortSignal),
    });
  });

  it("preserves a created task when an older refresh resolves afterward", async () => {
    const user = userEvent.setup();
    const pendingCreate = deferred<typeof deep>();
    const pendingRefresh = deferred<{ items: Array<typeof first | typeof second>; nextCursor: string }>();
    api.listTasks
      .mockResolvedValueOnce({ items: [first], nextCursor: "" })
      .mockReturnValueOnce(pendingRefresh.promise);
    api.createTask.mockReturnValue(pendingCreate.promise);
    render(<App />);

    await screen.findByText("销售策略");
    await user.click(screen.getByRole("button", { name: "新任务" }));
    await user.click(screen.getByRole("button", { name: "刷新任务" }));
    await act(async () => pendingCreate.resolve(deep));
    await act(async () => pendingRefresh.resolve({ items: [first], nextCursor: "" }));

    expect(screen.getByRole("heading", { name: "深层任务" })).toBeInTheDocument();
  });

  it("preserves a newer rename when an older refresh resolves afterward", async () => {
    const user = userEvent.setup();
    const pendingRename = deferred<typeof first>();
    const pendingRefresh = deferred<{ items: typeof first[]; nextCursor: string }>();
    api.listTasks
      .mockResolvedValueOnce({ items: [first], nextCursor: "" })
      .mockReturnValueOnce(pendingRefresh.promise);
    api.renameTask.mockReturnValue(pendingRename.promise);
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "重命名 销售策略" }));
    await user.clear(screen.getByRole("textbox", { name: "任务名称" }));
    await user.type(screen.getByRole("textbox", { name: "任务名称" }), "本地新名称");
    await user.click(screen.getByRole("button", { name: "保存名称" }));
    await user.click(screen.getByRole("button", { name: "刷新任务" }));
    await act(async () => pendingRename.resolve({ ...first, title: "本地新名称" }));
    await act(async () => pendingRefresh.resolve({ items: [first], nextCursor: "" }));

    expect(screen.getByText("本地新名称")).toBeInTheDocument();
    expect(screen.queryByText("销售策略")).not.toBeInTheDocument();
  });

  it("does not resurrect an archived task from an older refresh", async () => {
    const user = userEvent.setup();
    const pendingArchive = deferred<typeof first>();
    const pendingRefresh = deferred<{ items: Array<typeof first | typeof second>; nextCursor: string }>();
    api.listTasks
      .mockResolvedValueOnce({ items: [first, second], nextCursor: "" })
      .mockReturnValueOnce(pendingRefresh.promise);
    api.archiveTask.mockReturnValue(pendingArchive.promise);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "归档 销售策略" }));
    await user.click(screen.getByRole("button", { name: "刷新任务" }));
    await act(async () => pendingArchive.resolve({ ...first, archived_at: "2026-08-13 12:00:00" }));
    await act(async () => pendingRefresh.resolve({ items: [first, second], nextCursor: "" }));

    expect(screen.queryByText("销售策略")).not.toBeInTheDocument();
    expect(screen.getByText("产品规划")).toBeInTheDocument();
  });

  it("does not let a stale pagination duplicate overwrite a newer rename", async () => {
    const user = userEvent.setup();
    const pendingPage = deferred<{ items: typeof first[]; nextCursor: string }>();
    api.listTasks.mockResolvedValueOnce({ items: [first], nextCursor: "page-2" });
    api.listTasks.mockReturnValueOnce(pendingPage.promise);
    api.renameTask.mockResolvedValue({ ...first, title: "分页前新名称" });
    render(<App />);

    await screen.findByText("销售策略");
    await user.click(screen.getByRole("button", { name: "加载更多任务" }));
    await user.click(screen.getByRole("button", { name: "重命名 销售策略" }));
    await user.clear(screen.getByRole("textbox", { name: "任务名称" }));
    await user.type(screen.getByRole("textbox", { name: "任务名称" }), "分页前新名称");
    await user.click(screen.getByRole("button", { name: "保存名称" }));
    await act(async () => pendingPage.resolve({ items: [first], nextCursor: "" }));

    expect(screen.getByText("分页前新名称")).toBeInTheDocument();
    expect(screen.queryByText("销售策略")).not.toBeInTheDocument();
  });

  it("adopts authoritative lifecycle state when the server confirms a created task", async () => {
    const user = userEvent.setup();
    const serverQueued = {
      ...second,
      state: "queued" as const,
      updated_at: "2026-08-13 11:00:00",
    };
    api.listTasks
      .mockResolvedValueOnce({ items: [], nextCursor: "" })
      .mockResolvedValueOnce({ items: [serverQueued], nextCursor: "" });
    api.createTask.mockResolvedValue(second);
    render(<App />);

    await screen.findByText("还没有任务");
    await user.click(screen.getByRole("button", { name: "新任务" }));
    expect(await screen.findByText("空闲")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "刷新任务" }));

    expect(await screen.findByText("等待中")).toBeInTheDocument();
    expect(screen.queryByText("空闲")).not.toBeInTheDocument();
  });

  it("adopts authoritative lifecycle state when the server confirms a rename", async () => {
    const user = userEvent.setup();
    const renamed = { ...first, title: "确认后的名称" };
    const serverCompleted = {
      ...renamed,
      state: "completed" as const,
      updated_at: "2026-08-13 11:00:00",
    };
    api.listTasks
      .mockResolvedValueOnce({ items: [first], nextCursor: "" })
      .mockResolvedValueOnce({ items: [serverCompleted], nextCursor: "" });
    api.renameTask.mockResolvedValue(renamed);
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "重命名 销售策略" }));
    await user.clear(screen.getByRole("textbox", { name: "任务名称" }));
    await user.type(screen.getByRole("textbox", { name: "任务名称" }), "确认后的名称");
    await user.click(screen.getByRole("button", { name: "保存名称" }));
    await user.click(screen.getByRole("button", { name: "刷新任务" }));

    expect(await screen.findByText("已完成")).toBeInTheDocument();
    expect(screen.queryByText("执行中")).not.toBeInTheDocument();
  });

  it("keeps an unconfirmed rename title while adopting authoritative lifecycle state", async () => {
    const user = userEvent.setup();
    const renamed = { ...first, title: "仍待确认的名称" };
    const staleServer = {
      ...first,
      state: "completed" as const,
      updated_at: "2026-08-13 11:00:00",
    };
    api.listTasks
      .mockResolvedValueOnce({ items: [first], nextCursor: "" })
      .mockResolvedValueOnce({ items: [staleServer], nextCursor: "" });
    api.renameTask.mockResolvedValue(renamed);
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "重命名 销售策略" }));
    await user.clear(screen.getByRole("textbox", { name: "任务名称" }));
    await user.type(screen.getByRole("textbox", { name: "任务名称" }), "仍待确认的名称");
    await user.click(screen.getByRole("button", { name: "保存名称" }));
    await user.click(screen.getByRole("button", { name: "刷新任务" }));

    expect(await screen.findByText("仍待确认的名称")).toBeInTheDocument();
    expect(screen.queryByText("销售策略")).not.toBeInTheDocument();
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.queryByText("执行中")).not.toBeInTheDocument();
  });

  it("does not roll back a mutation snapshot with a refresh started before the rename", async () => {
    const user = userEvent.setup();
    const pendingRefresh = deferred<{ items: typeof first[]; nextCursor: string }>();
    const renamedCompleted = {
      ...first,
      title: "完成态新名称",
      state: "completed" as const,
      updated_at: "2026-08-13 12:00:00",
    };
    api.listTasks
      .mockResolvedValueOnce({ items: [first], nextCursor: "" })
      .mockReturnValueOnce(pendingRefresh.promise);
    api.renameTask.mockResolvedValue(renamedCompleted);
    window.history.replaceState({}, "", `/?task=${first.id}`);
    render(<App />);

    await screen.findByRole("heading", { name: "销售策略" });
    await user.click(screen.getByRole("button", { name: "刷新任务" }));
    await user.click(screen.getByRole("button", { name: "重命名 销售策略" }));
    await user.clear(screen.getByRole("textbox", { name: "任务名称" }));
    await user.type(screen.getByRole("textbox", { name: "任务名称" }), "完成态新名称");
    await user.click(screen.getByRole("button", { name: "保存名称" }));
    expect(await screen.findByText("已完成")).toBeInTheDocument();
    await act(async () => pendingRefresh.resolve({ items: [first], nextCursor: "" }));

    expect(screen.getByRole("heading", { name: "完成态新名称" })).toBeInTheDocument();
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.queryByText("执行中")).not.toBeInTheDocument();
    expect(screen.getByText("2026-08-13 12:00:00")).toBeInTheDocument();
  });

  it("does not roll back a mutation snapshot with pagination started before the rename", async () => {
    const user = userEvent.setup();
    const pendingPage = deferred<{ items: typeof first[]; nextCursor: string }>();
    const renamedCompleted = {
      ...first,
      title: "分页完成态",
      state: "completed" as const,
      updated_at: "2026-08-13 12:00:00",
    };
    api.listTasks.mockResolvedValueOnce({ items: [first], nextCursor: "page-2" });
    api.listTasks.mockReturnValueOnce(pendingPage.promise);
    api.renameTask.mockResolvedValue(renamedCompleted);
    render(<App />);

    await screen.findByText("销售策略");
    await user.click(screen.getByRole("button", { name: "加载更多任务" }));
    await user.click(screen.getByRole("button", { name: "重命名 销售策略" }));
    await user.clear(screen.getByRole("textbox", { name: "任务名称" }));
    await user.type(screen.getByRole("textbox", { name: "任务名称" }), "分页完成态");
    await user.click(screen.getByRole("button", { name: "保存名称" }));
    await act(async () => pendingPage.resolve({ items: [first], nextCursor: "" }));

    expect(screen.getByText("分页完成态")).toBeInTheDocument();
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.queryByText("执行中")).not.toBeInTheDocument();
  });

  it("preserves a renamed page-two task omitted by a page-one refresh started earlier", async () => {
    const user = userEvent.setup();
    const pendingRefresh = deferred<{ items: typeof first[]; nextCursor: string }>();
    const renamedSecond = {
      ...second,
      title: "第二页新名称",
      updated_at: "2026-08-13 12:00:00",
    };
    api.listTasks
      .mockResolvedValueOnce({ items: [first], nextCursor: "page-2" })
      .mockResolvedValueOnce({ items: [second], nextCursor: "" })
      .mockReturnValueOnce(pendingRefresh.promise);
    api.renameTask.mockResolvedValue(renamedSecond);
    render(<App />);

    await screen.findByText("销售策略");
    await user.click(screen.getByRole("button", { name: "加载更多任务" }));
    await user.click(await screen.findByRole("button", { name: "打开任务 产品规划" }));
    await user.click(screen.getByRole("button", { name: "刷新任务" }));
    await user.click(screen.getByRole("button", { name: "重命名 产品规划" }));
    await user.clear(screen.getByRole("textbox", { name: "任务名称" }));
    await user.type(screen.getByRole("textbox", { name: "任务名称" }), "第二页新名称");
    await user.click(screen.getByRole("button", { name: "保存名称" }));
    await act(async () => pendingRefresh.resolve({ items: [first], nextCursor: "page-2" }));

    expect(screen.getByRole("heading", { name: "第二页新名称" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "打开任务 第二页新名称" })).toHaveAttribute("aria-current", "page");
    expect(new URL(window.location.href).searchParams.get("task")).toBe(second.id);
    expect(api.listTasks).toHaveBeenCalledTimes(3);
  });

  it("paints page one while chasing a deep-link target in the background", async () => {
    const pendingDeepPage = deferred<{ items: typeof deep[]; nextCursor: string }>();
    api.listTasks
      .mockResolvedValueOnce({ items: [first], nextCursor: "page-2" })
      .mockReturnValueOnce(pendingDeepPage.promise);
    window.history.replaceState({}, "", `/?task=${deep.id}`);

    render(<App />);

    expect(await screen.findByText("销售策略")).toBeInTheDocument();
    expect(screen.queryByText("正在加载任务…")).not.toBeInTheDocument();
    expect(api.listTasks).toHaveBeenCalledTimes(2);
    await act(async () => pendingDeepPage.resolve({ items: [deep], nextCursor: "" }));
    expect(await screen.findByRole("heading", { name: "深层任务" })).toBeInTheDocument();
  });

  it("shares an in-flight cursor request between deep-link chase and load more", async () => {
    const user = userEvent.setup();
    const pendingPage = deferred<{ items: typeof deep[]; nextCursor: string }>();
    api.listTasks
      .mockResolvedValueOnce({ items: [first], nextCursor: "page-2" })
      .mockReturnValueOnce(pendingPage.promise);
    window.history.replaceState({}, "", `/?task=${deep.id}`);
    render(<App />);

    await screen.findByText("销售策略");
    await waitFor(() => expect(api.listTasks).toHaveBeenCalledTimes(2));
    await user.click(screen.getByRole("button", { name: "加载更多任务" }));
    expect(api.listTasks).toHaveBeenCalledTimes(2);
    await act(async () => pendingPage.resolve({ items: [deep], nextCursor: "" }));

    expect(await screen.findByRole("heading", { name: "深层任务" })).toBeInTheDocument();
  });

  it("aborts a deep-link chase when the user explicitly selects another task", async () => {
    const user = userEvent.setup();
    const pendingPage = deferred<{ items: typeof deep[]; nextCursor: string }>();
    api.listTasks
      .mockResolvedValueOnce({ items: [first], nextCursor: "page-2" })
      .mockReturnValueOnce(pendingPage.promise);
    window.history.replaceState({}, "", `/?task=${deep.id}`);
    render(<App />);

    const firstTask = await screen.findByRole("button", { name: "打开任务 销售策略" });
    await waitFor(() => expect(api.listTasks).toHaveBeenCalledTimes(2));
    const chaseSignal = api.listTasks.mock.calls[1][0].signal as AbortSignal;
    await user.click(firstTask);

    expect(chaseSignal.aborted).toBe(true);
    expect(screen.getByRole("heading", { name: "销售策略" })).toBeInTheDocument();
  });

  it("switches popstate chases and never selects the stale target", async () => {
    const oldTarget = { ...deep, id: "44444444-4444-4444-8444-444444444444", title: "旧目标" };
    const newTarget = { ...deep, id: "55555555-5555-4555-8555-555555555555", title: "新目标" };
    const oldPage = deferred<{ items: typeof oldTarget[]; nextCursor: string }>();
    const newPage = deferred<{ items: typeof newTarget[]; nextCursor: string }>();
    api.listTasks
      .mockResolvedValueOnce({ items: [first], nextCursor: "page-2" })
      .mockReturnValueOnce(oldPage.promise)
      .mockReturnValueOnce(newPage.promise);
    render(<App />);
    await screen.findByText("销售策略");

    act(() => {
      window.history.pushState({}, "", `/?task=${oldTarget.id}`);
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await waitFor(() => expect(api.listTasks).toHaveBeenCalledTimes(2));
    const oldSignal = api.listTasks.mock.calls[1][0].signal as AbortSignal;
    act(() => {
      window.history.pushState({}, "", `/?task=${newTarget.id}`);
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await waitFor(() => expect(api.listTasks).toHaveBeenCalledTimes(3));
    expect(oldSignal.aborted).toBe(true);
    await act(async () => oldPage.resolve({ items: [oldTarget], nextCursor: "" }));
    expect(screen.queryByRole("heading", { name: "旧目标" })).not.toBeInTheDocument();
    await act(async () => newPage.resolve({ items: [newTarget], nextCursor: "" }));
    expect(await screen.findByRole("heading", { name: "新目标" })).toBeInTheDocument();
  });

  it("aborts stale pagination so a refreshed first page cannot be contaminated", async () => {
    const user = userEvent.setup();
    const pendingPage = deferred<{ items: typeof first[]; nextCursor: string }>();
    api.listTasks
      .mockResolvedValueOnce({ items: [first], nextCursor: "page-2" })
      .mockReturnValueOnce(pendingPage.promise)
      .mockResolvedValueOnce({ items: [second], nextCursor: "" });

    render(<App />);

    await screen.findByText("销售策略");
    await user.click(screen.getByRole("button", { name: "加载更多任务" }));
    await user.click(screen.getByRole("button", { name: "刷新任务" }));
    expect(await screen.findByText("产品规划")).toBeInTheDocument();
    await act(async () => pendingPage.resolve({ items: [first], nextCursor: "" }));

    expect(screen.queryByText("销售策略")).not.toBeInTheDocument();
    expect(screen.getByText("产品规划")).toBeInTheDocument();
    expect(api.listTasks.mock.calls[1][0].signal.aborted).toBe(true);
  });

  it("deduplicates task IDs and stops a repeating pagination cursor", async () => {
    const user = userEvent.setup();
    api.listTasks
      .mockResolvedValueOnce({ items: [first], nextCursor: "page-2" })
      .mockResolvedValueOnce({ items: [first, second], nextCursor: "page-2" });

    render(<App />);

    await screen.findByText("销售策略");
    await user.click(screen.getByRole("button", { name: "加载更多任务" }));
    expect(await screen.findByText("任务分页游标重复，已停止继续加载")).toBeInTheDocument();
    expect(screen.getAllByText("销售策略")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "加载更多任务" })).not.toBeInTheDocument();
  });

  it("allows a failed page to retry the same cursor", async () => {
    const user = userEvent.setup();
    api.listTasks
      .mockResolvedValueOnce({ items: [first], nextCursor: "page-2" })
      .mockRejectedValueOnce(new Error("temporary"))
      .mockResolvedValueOnce({ items: [second], nextCursor: "" });

    render(<App />);

    await screen.findByText("销售策略");
    await user.click(screen.getByRole("button", { name: "加载更多任务" }));
    expect(await screen.findByText("更多任务加载失败，请重试")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "加载更多任务" }));

    expect(await screen.findByText("产品规划")).toBeInTheDocument();
    expect(api.listTasks).toHaveBeenCalledTimes(3);
  });

  it("rejects stale URL selections and creates a usable default task", async () => {
    const user = userEvent.setup();
    api.listTasks.mockResolvedValue({ items: [], nextCursor: "" });
    api.createTask.mockResolvedValue(second);
    window.history.replaceState({}, "", "/?task=missing");
    render(<App />);

    expect(await screen.findByText("还没有任务")).toBeInTheDocument();
    expect(new URL(window.location.href).searchParams.has("task")).toBe(false);
    await user.click(screen.getByRole("button", { name: "新任务" }));
    expect(api.createTask).toHaveBeenCalledWith("新任务", "codex", {
      signal: expect.any(AbortSignal),
    });
    expect(await screen.findByRole("heading", { name: "产品规划" })).toBeInTheDocument();
    expect(new URL(window.location.href).searchParams.get("task")).toBe(second.id);
  });

  it("offers retry after load failure", async () => {
    const user = userEvent.setup();
    api.listTasks
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({ items: [], nextCursor: "" });
    render(<App />);

    expect(await screen.findByText("任务加载失败")).toBeInTheDocument();
    expect(screen.queryByText("还没有任务")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("还没有任务")).toBeInTheDocument();
    expect(api.listTasks).toHaveBeenCalledTimes(2);
  });

  it("does not let a completed create steal a newer explicit selection", async () => {
    const user = userEvent.setup();
    const pendingCreate = deferred<typeof second>();
    const created = { ...second, id: "33333333-3333-4333-8333-333333333333", title: "新任务" };
    api.listTasks.mockResolvedValue({ items: [first, second], nextCursor: "" });
    api.createTask.mockReturnValue(pendingCreate.promise);
    window.history.replaceState({}, "", `/?task=${first.id}`);
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "新任务" }));
    await user.click(screen.getByRole("button", { name: "打开任务 产品规划" }));
    await act(async () => pendingCreate.resolve(created));

    expect(screen.getByRole("heading", { name: "产品规划" })).toBeInTheDocument();
    expect(new URL(window.location.href).searchParams.get("task")).toBe(second.id);
  });

  it("does not clear a newer selection when an archive response arrives", async () => {
    const user = userEvent.setup();
    const pendingArchive = deferred<typeof first>();
    api.listTasks.mockResolvedValue({ items: [first, second], nextCursor: "" });
    api.archiveTask.mockReturnValue(pendingArchive.promise);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    window.history.replaceState({}, "", `/?task=${first.id}`);
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "归档 销售策略" }));
    await user.click(screen.getByRole("button", { name: "打开任务 产品规划" }));
    await act(async () => pendingArchive.resolve({ ...first, archived_at: "2026-08-13 12:00:00" }));

    expect(screen.getByRole("heading", { name: "产品规划" })).toBeInTheDocument();
    expect(new URL(window.location.href).searchParams.get("task")).toBe(second.id);
  });

  it("ignores an older rename response that arrives after a newer rename", async () => {
    const user = userEvent.setup();
    const older = deferred<typeof first>();
    const newer = deferred<typeof first>();
    api.listTasks.mockResolvedValue({ items: [first], nextCursor: "" });
    api.renameTask.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
    window.history.replaceState({}, "", `/?task=${first.id}`);
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "重命名 销售策略" }));
    await user.clear(screen.getByRole("textbox", { name: "任务名称" }));
    await user.type(screen.getByRole("textbox", { name: "任务名称" }), "名称一");
    await user.click(screen.getByRole("button", { name: "保存名称" }));
    await user.click(screen.getByRole("button", { name: "重命名 销售策略" }));
    await user.clear(screen.getByRole("textbox", { name: "任务名称" }));
    await user.type(screen.getByRole("textbox", { name: "任务名称" }), "名称二");
    await user.click(screen.getByRole("button", { name: "保存名称" }));

    await act(async () => newer.resolve({ ...first, title: "名称二" }));
    expect(screen.getByRole("heading", { name: "名称二" })).toBeInTheDocument();
    await act(async () => older.resolve({ ...first, title: "名称一" }));
    expect(screen.getByRole("heading", { name: "名称二" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "名称一" })).not.toBeInTheDocument();
  });

  it("aborts an in-flight mutation when the workbench unmounts", async () => {
    const user = userEvent.setup();
    const pendingCreate = deferred<typeof second>();
    api.listTasks.mockResolvedValue({ items: [], nextCursor: "" });
    api.createTask.mockReturnValue(pendingCreate.promise);
    const view = render(<App />);

    await screen.findByText("还没有任务");
    await user.click(screen.getByRole("button", { name: "新任务" }));
    const signal = api.createTask.mock.calls[0][2].signal as AbortSignal;
    expect(signal.aborted).toBe(false);

    view.unmount();
    expect(signal.aborted).toBe(true);
  });

  it("keeps the inspector as a normal aside on desktop", async () => {
    setCompactViewport(false);
    api.listTasks.mockResolvedValue({ items: [first], nextCursor: "" });
    window.history.replaceState({}, "", `/?task=${first.id}`);
    render(<App />);

    expect(await screen.findByRole("complementary", { name: "任务详情" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "打开详情" })).not.toBeInTheDocument();
  });

  it("traps focus in the responsive inspector and restores it after Escape", async () => {
    const user = userEvent.setup();
    setCompactViewport(true);
    api.listTasks.mockResolvedValue({ items: [first, second], nextCursor: "" });
    window.history.replaceState({}, "", `/?task=${first.id}`);
    render(<App />);

    const inspectorToggle = await screen.findByRole("button", { name: "打开详情" });
    expect(screen.getByRole("button", { name: "返回任务列表" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "任务详情" })).not.toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "任务详情" })).not.toBeInTheDocument();
    expect(inspectorToggle).toHaveAttribute("aria-expanded", "false");
    await user.click(inspectorToggle);
    const dialog = screen.getByRole("dialog", { name: "任务详情" });
    const close = within(dialog).getByRole("button", { name: "关闭详情" });
    expect(inspectorToggle).toHaveAttribute("aria-expanded", "true");
    expect(close).toHaveFocus();
    expect(document.querySelector(".task-panel")).toHaveAttribute("aria-hidden", "true");
    expect(document.querySelector(".conversation-panel")).toHaveAttribute("aria-hidden", "true");
    await user.tab();
    expect(close).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "任务详情" })).not.toBeInTheDocument();
    expect(inspectorToggle).toHaveFocus();
    expect(screen.getByRole("heading", { name: "销售策略" })).toBeInTheDocument();
    await waitFor(() => expect(api.listTasks).toHaveBeenCalledOnce());
  });

  it("loads and renders the selected persisted timeline with runtime details", async () => {
    const completedTurn: Turn = {
      id: "turn-completed", task_id: first.id, client_request_id: "request", user_text: "生成周报", status: "completed",
      stop_requested: false, final_text: "", error_code: "", error_detail: "", started_at: "2026-08-13 10:00:00",
      completed_at: "2026-08-13 10:00:02", created_at: "2026-08-13 10:00:00", updated_at: "2026-08-13 10:00:02",
    };
    api.listTasks.mockResolvedValue({ items: [first], nextCursor: "" });
    api.getTimeline.mockResolvedValue({
      ...emptyTimeline({ ...first, state: "completed" }, [completedTurn]),
      events: [{ id: 8, turn_id: completedTurn.id, sequence: 2, event_type: "text_delta", payload: { text: "周报已完成" }, created_at: "" }],
    });
    window.history.replaceState({}, "", `/?task=${first.id}`);
    render(<App />);

    expect(await screen.findByText("生成周报")).toBeInTheDocument();
    expect(screen.getByText("周报已完成")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
    expect(api.getTimeline).toHaveBeenCalledWith(first.id, expect.objectContaining({ turnLimit: 100, eventLimit: 1000, signal: expect.any(AbortSignal) }));
    expect(api.runtimeCapabilities).toHaveBeenCalled();
    expect(api.getStats).toHaveBeenCalled();
  });

  it("loads older turns without duplicating the selected timeline", async () => {
    const user = userEvent.setup();
    const recent: Turn = { id: "recent", task_id: first.id, client_request_id: "r", user_text: "最近", status: "completed", stop_requested: false, final_text: "", error_code: "", error_detail: "", started_at: "", completed_at: "", created_at: "", updated_at: "" };
    const older: Turn = { ...recent, id: "older", client_request_id: "o", user_text: "更早" };
    api.listTasks.mockResolvedValue({ items: [first], nextCursor: "" });
    api.getTimeline
      .mockResolvedValueOnce({ ...emptyTimeline(first, [recent]), next_cursor: "older-page", has_more: true })
      .mockResolvedValueOnce(emptyTimeline(first, [older]));
    window.history.replaceState({}, "", `/?task=${first.id}`);
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "加载更早对话" }));
    expect(await screen.findByText("更早")).toBeInTheDocument();
    expect(screen.getAllByText("最近")).toHaveLength(1);
    expect(api.getTimeline).toHaveBeenNthCalledWith(2, first.id, expect.objectContaining({ before: "older-page", signal: expect.any(AbortSignal) }));
  });

  it("keeps a resource cursor bound to the older turn window that produced it", async () => {
    const user = userEvent.setup();
    const recent: Turn = { id: "recent", task_id: first.id, client_request_id: "r", user_text: "最近", status: "completed", stop_requested: false, final_text: "", error_code: "", error_detail: "", started_at: "", completed_at: "", created_at: "", updated_at: "" };
    const older: Turn = { ...recent, id: "older", client_request_id: "o", user_text: "更早" };
    api.listTasks.mockResolvedValue({ items: [first], nextCursor: "" });
    api.getTimeline
      .mockResolvedValueOnce({ ...emptyTimeline(first, [recent]), next_cursor: "older-page", has_more: true })
      .mockResolvedValueOnce({ ...emptyTimeline(first, [older]), artifacts_has_more: true, artifacts_next_cursor: "older-artifacts" })
      .mockResolvedValueOnce(emptyTimeline(first, [older]));
    window.history.replaceState({}, "", `/?task=${first.id}`);
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "加载更早对话" }));
    await user.click(await screen.findByRole("button", { name: "加载更多产物" }));

    expect(api.getTimeline).toHaveBeenNthCalledWith(3, first.id, expect.objectContaining({
      before: "older-page",
      artifactAfter: "older-artifacts",
    }));
  });

  it("does not enqueue the same task-wide attachment cursor for every turn window", async () => {
    const user = userEvent.setup();
    const recent: Turn = { id: "recent", task_id: first.id, client_request_id: "r", user_text: "最近", status: "completed", stop_requested: false, final_text: "", error_code: "", error_detail: "", started_at: "", completed_at: "", created_at: "", updated_at: "" };
    const older: Turn = { ...recent, id: "older", client_request_id: "o", user_text: "更早" };
    api.listTasks.mockResolvedValue({ items: [first], nextCursor: "" });
    api.getTimeline
      .mockResolvedValueOnce({
        ...emptyTimeline(first, [recent]),
        next_cursor: "older-page",
        has_more: true,
        attachments_has_more: true,
        attachments_next_cursor: "shared-attachments",
      })
      .mockResolvedValueOnce({
        ...emptyTimeline(first, [older]),
        attachments_has_more: true,
        attachments_next_cursor: "shared-attachments",
      })
      .mockResolvedValueOnce(emptyTimeline(first, [recent, older]));
    window.history.replaceState({}, "", `/?task=${first.id}`);
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "加载更早对话" }));
    await user.click(await screen.findByRole("button", { name: "加载更多附件" }));

    await waitFor(() => expect(screen.queryByRole("button", { name: "加载更多附件" })).not.toBeInTheDocument());
    expect(api.getTimeline).toHaveBeenNthCalledWith(3, first.id, expect.objectContaining({
      attachmentAfter: "shared-attachments",
    }));
  });

  it("progressively loads every truncated timeline resource with its public cursor", async () => {
    const user = userEvent.setup();
    const stopped: Turn = {
      id: "turn-paged", task_id: first.id, client_request_id: "paged", user_text: "分页资源", status: "stopped",
      stop_requested: true, final_text: "", error_code: "", error_detail: "", started_at: "", completed_at: "", created_at: "", updated_at: "",
    };
    const initial: Timeline = {
      ...emptyTimeline(first, [stopped]),
      events: [{ id: 100, turn_id: stopped.id, sequence: 100, event_type: "thinking_summary", payload: { summary: "最新事件" }, created_at: "" }],
      events_has_more: true,
      events_next_cursor: 90,
      artifacts_has_more: true,
      artifacts_next_cursor: "artifact-cursor",
      confirmations_has_more: true,
      confirmations_next_cursor: "confirmation-cursor",
      attachments_has_more: true,
      attachments_next_cursor: "attachment-cursor",
    };
    api.listTasks.mockResolvedValue({ items: [first], nextCursor: "" });
    api.getTimeline
      .mockResolvedValueOnce(initial)
      .mockResolvedValueOnce({
        ...emptyTimeline(first, [stopped]),
        events: [{ id: 80, turn_id: stopped.id, sequence: 80, event_type: "thinking_summary", payload: { summary: "更早事件" }, created_at: "" }],
      })
      .mockResolvedValueOnce({
        ...emptyTimeline(first, [stopped]),
        artifacts: [{ id: "artifact-page-2", turn_id: stopped.id, label: "第二页产物", media_type: "text/plain", created_at: "", download_url: "/ignored" }],
      })
      .mockResolvedValueOnce({
        ...emptyTimeline(first, [stopped]),
        confirmations: [{
          id: "confirmation-page-2", turn_id: stopped.id, action_kind: "send", target: "群", summary: "发送", risk: "外部可见",
          canonical_capability: "chat", canonical_operation: "发送消息", canonical_targets: ["群"], status: "executed",
          decision_requested: "confirm", decision_requested_at: "", proposer_quiesced: true, created_at: "", decided_at: "",
        }],
      })
      .mockResolvedValueOnce({
        ...emptyTimeline(first, [stopped]),
        attachments: [{ id: "attachment-page-2", task_id: first.id, filename: "image.png", media_type: "image/png", size_bytes: 10, created_at: "" }],
      });
    window.history.replaceState({}, "", `/?task=${first.id}`);
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "加载更多事件" }));
    expect(await screen.findByText("更早事件")).toBeInTheDocument();
    expect(api.getTimeline).toHaveBeenNthCalledWith(2, first.id, expect.objectContaining({ eventBefore: 90 }));

    await user.click(screen.getByRole("button", { name: "加载更多产物" }));
    await waitFor(() => expect(api.getTimeline).toHaveBeenNthCalledWith(3, first.id, expect.objectContaining({ artifactAfter: "artifact-cursor" })));

    await user.click(screen.getByRole("button", { name: "加载更多确认" }));
    await waitFor(() => expect(api.getTimeline).toHaveBeenNthCalledWith(4, first.id, expect.objectContaining({ confirmationAfter: "confirmation-cursor" })));

    await user.click(screen.getByRole("button", { name: "加载更多附件" }));
    await waitFor(() => expect(api.getTimeline).toHaveBeenNthCalledWith(5, first.id, expect.objectContaining({ attachmentAfter: "attachment-cursor" })));
    expect(await screen.findByText("任务已有 1 个附件")).toBeInTheDocument();
  });

  it("applies the confirmation response immediately without requiring a manual refresh", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("EventSource", class {
      onopen = null;
      onerror = null;
      addEventListener() {}
      close() {}
    });
    const waiting: Turn = {
      id: "turn-confirm", task_id: first.id, client_request_id: "confirm", user_text: "发送消息", status: "waiting_confirmation",
      stop_requested: false, final_text: "", error_code: "", error_detail: "", started_at: "", completed_at: "", created_at: "", updated_at: "",
    };
    const confirmation = {
      id: "confirmation-1", turn_id: waiting.id, action_kind: "send", target: "群", summary: "发送", risk: "外部可见",
      canonical_capability: "chat", canonical_operation: "发送消息", canonical_targets: ["群"], status: "pending" as const,
      decision_requested: "", decision_requested_at: "", proposer_quiesced: false, created_at: "", decided_at: "",
    };
    const refresh = deferred<Timeline>();
    api.listTasks.mockResolvedValue({ items: [first], nextCursor: "" });
    api.getTimeline
      .mockResolvedValueOnce({
        ...emptyTimeline(first, [waiting]),
        confirmations: [confirmation],
        events: [{ id: 1, turn_id: waiting.id, sequence: 1, event_type: "confirmation_required", payload: { confirmation_id: confirmation.id }, created_at: "" }],
      })
      .mockReturnValueOnce(refresh.promise);
    api.confirmAction.mockResolvedValue({ ...confirmation, decision_requested: "confirm" });
    window.history.replaceState({}, "", `/?task=${first.id}`);
    render(<App />);

    const confirm = await screen.findByRole("button", { name: "确认执行" });
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    await user.click(confirm);

    expect(api.confirmAction).toHaveBeenCalledOnce();
    expect(await screen.findByText("等待执行器安全停稳")).toBeInTheDocument();
    expect(confirm).toBeDisabled();
  });

  it("aborts a stale selected-task timeline and never paints it after switching", async () => {
    const user = userEvent.setup();
    const stale = deferred<Timeline>();
    api.listTasks.mockResolvedValue({ items: [first, second], nextCursor: "" });
    api.getTimeline
      .mockReturnValueOnce(stale.promise)
      .mockResolvedValueOnce(emptyTimeline(second, [{
        id: "second-turn", task_id: second.id, client_request_id: "second", user_text: "第二任务内容", status: "completed",
        stop_requested: false, final_text: "", error_code: "", error_detail: "", started_at: "", completed_at: "", created_at: "", updated_at: "",
      }]));
    window.history.replaceState({}, "", `/?task=${first.id}`);
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "打开任务 产品规划" }));
    expect(await screen.findByText("第二任务内容")).toBeInTheDocument();
    expect((api.getTimeline.mock.calls[0][1].signal as AbortSignal).aborted).toBe(true);
    await act(async () => stale.resolve(emptyTimeline(first, [{
      id: "stale", task_id: first.id, client_request_id: "stale", user_text: "不应出现", status: "completed",
      stop_requested: false, final_text: "", error_code: "", error_detail: "", started_at: "", completed_at: "", created_at: "", updated_at: "",
    }])));
    expect(screen.queryByText("不应出现")).not.toBeInTheDocument();
  });

  it("closes the active task event source when switching tasks", async () => {
    const user = userEvent.setup();
    const sources: Array<{ url: string; closed: boolean }> = [];
    class FakeSource {
      onopen: ((event: Event) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      closed = false;
      constructor(readonly url: string) { sources.push(this); }
      addEventListener() {}
      close() { this.closed = true; }
    }
    vi.stubGlobal("EventSource", FakeSource);
    const running: Turn = {
      id: "running-turn", task_id: first.id, client_request_id: "running", user_text: "持续执行", status: "running",
      stop_requested: false, final_text: "", error_code: "", error_detail: "", started_at: "", completed_at: "", created_at: "", updated_at: "",
    };
    api.listTasks.mockResolvedValue({ items: [first, second], nextCursor: "" });
    api.getTimeline
      .mockResolvedValueOnce(emptyTimeline(first, [running]))
      .mockResolvedValueOnce(emptyTimeline(second));
    window.history.replaceState({}, "", `/?task=${first.id}`);
    render(<App />);

    await screen.findByRole("button", { name: "停止执行" });
    expect(sources[0].url).toContain("/running-turn/events/stream?after=0");
    await user.click(screen.getByRole("button", { name: "打开任务 产品规划" }));
    await screen.findByText("开始新的对话");
    expect(sources[0].closed).toBe(true);
    vi.unstubAllGlobals();
  });

  it("keeps tool status scoped to its step and applies terminal SSE state to the task", async () => {
    class ControllableSource {
      onopen: ((event: Event) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      closed = false;
      private listeners = new Map<string, Array<(event: MessageEvent) => void>>();

      constructor(readonly url: string) { sources.push(this); }
      addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
        this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener as (event: MessageEvent) => void]);
      }
      close() { this.closed = true; }
      emit(type: string, id: number, payload: Record<string, unknown>) {
        const event = new MessageEvent(type, {
          data: JSON.stringify({ id, turn_id: "running-turn", sequence: id, event_type: type, payload, created_at: "" }),
          lastEventId: String(id),
        });
        for (const listener of this.listeners.get(type) ?? []) listener(event);
      }
    }
    const sources: ControllableSource[] = [];
    vi.stubGlobal("EventSource", ControllableSource);
    const running: Turn = {
      id: "running-turn", task_id: first.id, client_request_id: "running", user_text: "持续执行", status: "running",
      stop_requested: false, final_text: "", error_code: "", error_detail: "", started_at: "", completed_at: "", created_at: "", updated_at: "",
    };
    const terminalRefresh = deferred<Timeline>();
    api.listTasks.mockResolvedValue({ items: [first], nextCursor: "" });
    api.getTimeline
      .mockResolvedValueOnce(emptyTimeline(first, [running]))
      .mockReturnValueOnce(terminalRefresh.promise);
    window.history.replaceState({}, "", `/?task=${first.id}`);
    render(<App />);

    await screen.findByRole("button", { name: "停止执行" });
    sources[0].emit("tool_completed", 1, {
      tool_call_id: "tool-1",
      tool: "search",
      status: "completed",
      summary: "检索完成",
    });

    await waitFor(() => expect(screen.getByText("检索完成")).toBeInTheDocument());
    expect(sources[0].closed).toBe(false);
    expect(screen.getByRole("button", { name: "停止执行" })).toBeEnabled();

    sources[0].emit("status_changed", 2, { status: "completed" });
    await waitFor(() => expect(within(screen.getByRole("button", { name: "打开任务 销售策略" })).getByText("已完成")).toBeInTheDocument());
    expect(sources[0].closed).toBe(true);
  });
});
