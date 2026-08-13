import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, createTask, getTimeline, listTasks } from "./api";

const publicTask = {
  id: "1b8a4717-07fe-45e7-950c-df5bbc56d8aa",
  title: "销售分析",
  runtime_kind: "codex",
  archived_at: "",
  state: "running",
  created_at: "2026-08-13 09:00:00",
  updated_at: "2026-08-13 10:00:00",
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("workbench API", () => {
  it("returns validated task pages and the server cursor", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify([publicTask]), {
          status: 200,
          headers: {
            "Content-Type": "application/json",
            "X-Next-Cursor": "next-page",
          },
        }),
      ),
    );

    const page = await listTasks({ limit: 20, archived: "active" });

    expect(page).toEqual({ items: [publicTask], nextCursor: "next-page" });
    expect(fetch).toHaveBeenCalledWith(
      "/api/workbench/tasks?archived=active&limit=20",
      expect.objectContaining({ credentials: "same-origin" }),
    );
  });

  it("sends JSON mutations with safe headers and forwards abort signals", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ...publicTask, state: "idle" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await createTask("新任务", "codex", { signal: controller.signal });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/workbench/tasks",
      expect.objectContaining({
        method: "POST",
        credentials: "same-origin",
        signal: controller.signal,
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ title: "新任务", runtime_kind: "codex" }),
      }),
    );
  });

  it("exposes only structured safe error fields", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            code: "task_has_active_turn",
            detail: "Tasks with active turns cannot be archived",
            internal_trace: "/Users/private/worker.py",
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(createTask("新任务", "codex")).rejects.toMatchObject({
      status: 409,
      code: "task_has_active_turn",
      detail: "Tasks with active turns cannot be archived",
    });
  });

  it("does not leak HTML error responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<html>secret stack trace</html>", {
          status: 500,
          headers: { "Content-Type": "text/html" },
        }),
      ),
    );

    try {
      await listTasks();
      throw new Error("expected request to fail");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).detail).toBe("请求失败，请稍后重试");
      expect((error as Error).message).not.toContain("secret stack trace");
    }
  });

  it("fails closed when successful JSON has the wrong public shape", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify([{ ...publicTask, state: "probably-running" }]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(listTasks()).rejects.toMatchObject({
      status: 502,
      code: "invalid_response",
      detail: "服务返回的数据格式无效",
    });
  });

  it("rejects non-positive persisted event IDs", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({
        task: publicTask,
        turns: [],
        events: [{ id: 0, turn_id: "turn", sequence: 1, event_type: "text_delta", payload: { text: "bad" }, created_at: "" }],
        attachments: [], artifacts: [], confirmations: [], next_cursor: "", has_more: false,
        events_has_more: false, events_next_cursor: 0, artifacts_has_more: false, artifacts_next_cursor: "",
        confirmations_has_more: false, confirmations_next_cursor: "", attachments_has_more: false, attachments_next_cursor: "",
      }), { status: 200, headers: { "Content-Type": "application/json" } })),
    );

    await expect(getTimeline(publicTask.id)).rejects.toMatchObject({ code: "invalid_response" });
  });
});
