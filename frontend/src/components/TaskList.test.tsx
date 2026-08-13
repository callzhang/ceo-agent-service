import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TaskList } from "./TaskList";

function task(
  id: string,
  title: string,
  state: "idle" | "queued" | "running" | "waiting_confirmation" | "completed" | "stopped" | "failed",
  updatedAt: string,
) {
  return {
    id,
    title,
    runtime_kind: "codex",
    archived_at: "",
    state,
    created_at: updatedAt,
    updated_at: updatedAt,
  };
}

function localTimestamp(daysAgo: number) {
  const value = new Date();
  value.setDate(value.getDate() - daysAgo);
  const parts = [
    value.getFullYear(),
    String(value.getMonth() + 1).padStart(2, "0"),
    String(value.getDate()).padStart(2, "0"),
  ];
  return `${parts.join("-")} 10:00:00`;
}

describe("TaskList", () => {
  it("shows a persisted running state and exposes select and new-task actions", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const onNewTask = vi.fn();

    render(
      <TaskList
        tasks={[
          {
            id: "t1",
            title: "Sales",
            runtime_kind: "codex",
            archived_at: "",
            state: "running",
            created_at: "2026-08-13 09:00:00",
            updated_at: "2026-08-13 10:00:00",
          },
        ]}
        activeTaskId="t1"
        onSelect={onSelect}
        onNewTask={onNewTask}
        onRename={() => undefined}
        onArchive={() => undefined}
      />,
    );

    expect(screen.getByText("Sales")).toBeInTheDocument();
    expect(screen.getByText("执行中")).toBeInTheDocument();
    const newTask = screen.getByRole("button", { name: "新任务" });
    expect(newTask).toBeEnabled();

    await user.click(screen.getByRole("button", { name: /打开任务 Sales/ }));
    await user.click(newTask);
    expect(onSelect).toHaveBeenCalledWith("t1");
    expect(onNewTask).toHaveBeenCalledOnce();
  });

  it("groups parsed local dates and filters tasks by title", async () => {
    const user = userEvent.setup();

    render(
      <TaskList
        tasks={[
          task("today", "销售复盘", "queued", localTimestamp(0)),
          task("yesterday", "产品设计", "completed", localTimestamp(1)),
          task("earlier", "招聘计划", "failed", localTimestamp(7)),
        ]}
        activeTaskId={null}
        onSelect={() => undefined}
        onNewTask={() => undefined}
        onRename={() => undefined}
        onArchive={() => undefined}
      />,
    );

    expect(screen.getByRole("heading", { name: "今天" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "昨天" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "更早" })).toBeInTheDocument();
    await user.type(screen.getByRole("searchbox", { name: "搜索任务" }), "产品");
    expect(screen.getByText("产品设计")).toBeInTheDocument();
    expect(screen.queryByText("销售复盘")).not.toBeInTheDocument();
    expect(screen.getByText("已完成")).toBeInTheDocument();
  });

  it("treats backend store timestamps as UTC before grouping in the browser timezone", () => {
    vi.stubEnv("TZ", "Asia/Shanghai");
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-13T04:00:00Z"));

    try {
      render(
        <TaskList
          tasks={[
            task("backend", "后端 UTC", "idle", "2026-08-12 18:00:00"),
            task("iso-z", "ISO Z", "idle", "2026-08-11T18:00:00Z"),
            task("iso-offset", "ISO Offset", "idle", "2026-08-12T18:00:00-06:00"),
            task("invalid", "Invalid", "idle", "not-a-date"),
          ]}
          activeTaskId={null}
          onSelect={() => undefined}
          onNewTask={() => undefined}
          onRename={() => undefined}
          onArchive={() => undefined}
        />,
      );

      const today = screen.getByRole("heading", { name: "今天" }).closest("section");
      const yesterday = screen.getByRole("heading", { name: "昨天" }).closest("section");
      const earlier = screen.getByRole("heading", { name: "更早" }).closest("section");
      expect(today).not.toBeNull();
      expect(yesterday).not.toBeNull();
      expect(earlier).not.toBeNull();
      expect(within(today!).getByText("后端 UTC")).toBeInTheDocument();
      expect(within(today!).getByText("ISO Offset")).toBeInTheDocument();
      expect(within(yesterday!).getByText("ISO Z")).toBeInTheDocument();
      expect(within(earlier!).getByText("Invalid")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
      vi.unstubAllEnvs();
    }
  });

  it("falls back to Earlier for impossible backend calendar dates", () => {
    vi.stubEnv("TZ", "Asia/Shanghai");
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-02T12:00:00Z"));

    try {
      render(
        <TaskList
          tasks={[task("invalid-date", "Invalid Date", "idle", "2026-02-30 12:00:00")]}
          activeTaskId={null}
          onSelect={() => undefined}
          onNewTask={() => undefined}
          onRename={() => undefined}
          onArchive={() => undefined}
        />,
      );

      expect(screen.getByRole("heading", { name: "更早" })).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "今天" })).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
      vi.unstubAllEnvs();
    }
  });

  it("renders every public lifecycle state without inventing state", () => {
    render(
      <TaskList
        tasks={[
          task("idle", "Idle", "idle", "2026-08-13 10:00:00"),
          task("queued", "Queued", "queued", "2026-08-13 10:00:00"),
          task("running", "Running", "running", "2026-08-13 10:00:00"),
          task("confirm", "Confirm", "waiting_confirmation", "2026-08-13 10:00:00"),
          task("done", "Done", "completed", "2026-08-13 10:00:00"),
          task("stopped", "Stopped", "stopped", "2026-08-13 10:00:00"),
          task("failed", "Failed", "failed", "2026-08-13 10:00:00"),
        ]}
        activeTaskId={null}
        onSelect={() => undefined}
        onNewTask={() => undefined}
        onRename={() => undefined}
        onArchive={() => undefined}
      />,
    );

    for (const label of ["空闲", "等待中", "执行中", "等待确认", "已完成", "已停止", "失败"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("renames through a labeled form and confirms archive explicitly", async () => {
    const user = userEvent.setup();
    const onRename = vi.fn();
    const onArchive = vi.fn();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <TaskList
        tasks={[task("t1", "原任务", "idle", "2026-08-13 10:00:00")]}
        activeTaskId="t1"
        onSelect={() => undefined}
        onNewTask={() => undefined}
        onRename={onRename}
        onArchive={onArchive}
      />,
    );

    await user.click(screen.getByRole("button", { name: "重命名 原任务" }));
    const titleInput = screen.getByRole("textbox", { name: "任务名称" });
    await user.clear(titleInput);
    await user.type(titleInput, "新名称");
    await user.click(screen.getByRole("button", { name: "保存名称" }));
    expect(onRename).toHaveBeenCalledWith("t1", "新名称");

    await user.click(screen.getByRole("button", { name: "归档 原任务" }));
    expect(confirm).toHaveBeenCalled();
    expect(onArchive).toHaveBeenCalledWith("t1");
    confirm.mockRestore();
  });
});
