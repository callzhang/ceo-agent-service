import { act, render, screen, waitFor } from "@testing-library/react";
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

describe("App", () => {
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

  it("loads subsequent task pages before validating the URL selection", async () => {
    api.listTasks
      .mockResolvedValueOnce({ items: [first], nextCursor: "page-2" })
      .mockResolvedValueOnce({ items: [second], nextCursor: "" });
    window.history.replaceState({}, "", `/?task=${second.id}`);

    render(<App />);

    expect(await screen.findByRole("heading", { name: "产品规划" })).toBeInTheDocument();
    expect(api.listTasks).toHaveBeenNthCalledWith(2, {
      archived: "active",
      limit: 100,
      cursor: "page-2",
    });
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
    expect(api.createTask).toHaveBeenCalledWith("新任务", "codex");
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

  it("provides mobile back and inspector drawer controls without losing selection", async () => {
    const user = userEvent.setup();
    api.listTasks.mockResolvedValue({ items: [first, second], nextCursor: "" });
    window.history.replaceState({}, "", `/?task=${first.id}`);
    render(<App />);

    const inspectorToggle = await screen.findByRole("button", { name: "打开详情" });
    expect(screen.getByRole("button", { name: "返回任务列表" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "任务详情" })).toBeInTheDocument();
    expect(inspectorToggle).toHaveAttribute("aria-expanded", "false");
    await user.click(inspectorToggle);
    expect(inspectorToggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("heading", { name: "销售策略" })).toBeInTheDocument();
    await waitFor(() => expect(api.listTasks).toHaveBeenCalledOnce());
  });
});
