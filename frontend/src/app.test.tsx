import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listTasks: vi.fn(),
  createTask: vi.fn(),
  renameTask: vi.fn(),
  archiveTask: vi.fn(),
}));

vi.mock("./api", () => api);

import { App } from "./app";
import styles from "./styles.css?raw";

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

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/");
});

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
});
