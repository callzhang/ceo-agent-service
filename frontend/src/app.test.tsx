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

  it("paints the first page before requesting more and restores a later URL selection", async () => {
    const user = userEvent.setup();
    api.listTasks
      .mockResolvedValueOnce({ items: [first], nextCursor: "page-2" })
      .mockResolvedValueOnce({ items: [second], nextCursor: "" });
    window.history.replaceState({}, "", `/?task=${second.id}`);

    render(<App />);

    expect(await screen.findByText("销售策略")).toBeInTheDocument();
    expect(api.listTasks).toHaveBeenCalledOnce();
    expect(new URL(window.location.href).searchParams.get("task")).toBe(second.id);
    await user.click(screen.getByRole("button", { name: "加载更多任务" }));
    expect(await screen.findByRole("heading", { name: "产品规划" })).toBeInTheDocument();
    expect(api.listTasks).toHaveBeenNthCalledWith(2, {
      archived: "active",
      limit: 100,
      cursor: "page-2",
      signal: expect.any(AbortSignal),
    });
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
