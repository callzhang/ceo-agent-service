import { describe, expect, it } from "vitest";

import { displayValue, parseConsoleList } from "./console";

describe("console API helpers", () => {
  it("normalizes arbitrary values before display", () => {
    expect(displayValue("plain")).toBe("plain");
    expect(displayValue({ title: "优先标题", text: "正文" })).toBe("优先标题");
    expect(displayValue({ text: "正文" })).toBe("正文");
    expect(displayValue(["a", "b"])).toBe("a；b");
    expect(displayValue({ nested: { value: true } })).toContain("nested");
    expect(displayValue(null)).toBe("未提供");
    expect(displayValue(undefined)).toBe("未提供");
    expect(displayValue({})).not.toBe("[object Object]");
  });

  it("rejects malformed list envelopes instead of treating them as empty data", () => {
    expect(() => parseConsoleList({ items: [], meta: { total: 0 } })).toThrow("invalid console response");
    expect(parseConsoleList({ items: [{ id: "1" }], meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" } })).toEqual({
      items: [{ id: "1" }],
      meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T00:00:00Z" },
    });
  });

  it("uses server-provided Attention detail URLs instead of assuming every record is an Attempt", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({
      items: [{
        category: "Service error",
        root_cause: "database is locked",
        context: "producer_loop_error",
        severity: "error",
        count: 1,
        summary: "database is locked",
        error: "database is locked",
        updated_at: "2026-08-29 18:27:11",
        records: [{
          id: "12830",
          category: "Service error",
          status: "failed",
          context: "producer_loop_error",
          root_cause: "database is locked",
          summary: "database is locked",
          updated_at: "2026-08-29 18:27:11",
          error: "database is locked",
          detail_url: "/history/errors/12830",
        }],
      }],
      meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T18:27:11Z" },
    }), { status: 200, headers: { "Content-Type": "application/json" } });
    try {
      const { listAttention } = await import("./console");
      const page = await listAttention();
      expect(page.items[0].links).toEqual([{ label: "查看错误详情", href: "/history/errors/12830" }]);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("prefers the server-provided task owner field when present", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({
      items: [{
        id: 836,
        title: "客户项目",
        status: "active",
        category: "projects",
        priority: "high",
        risk_level: "low",
        owner: "Mina",
        owner_name: "",
        owner_user_id: "",
        progress_count: 3,
        progress_total: 5,
        progress_ratio: 60,
        todo_count: 5,
        current_state: "等待客户确认",
        next_step: "准备下一次同步",
      }],
      meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-08-29T18:27:11Z" },
    }), { status: 200, headers: { "Content-Type": "application/json" } });
    try {
      const { listTasks } = await import("./console");
      const page = await listTasks();
      expect(page.items[0].owner).toBe("Mina");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("summarizes multiple TODO owners on task details when project owner is empty", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({
      item: {
        project: {
          id: 836,
          title: "客户项目",
          status: "active",
          category: "projects",
          priority: "P1",
          risk_level: "medium",
          owner: "",
          owner_name: "",
          owner_user_id: "",
          current_state: "等待客户确认",
          next_step: "准备下一次同步",
          facts: [],
          tags: [],
        },
        todos: [
          { id: 1, owner_name: "周俊杰", owner_user_id: "owner-1" },
          { id: 2, owner_name: "张晓民", owner_user_id: "owner-2" },
          { id: 3, owner_name: "Mina", owner_user_id: "owner-3" },
          { id: 4, owner_name: "ET", owner_user_id: "owner-4" },
        ],
        updates: [],
      },
      meta: { snapshot_at: "2026-08-29T18:27:11Z" },
    }), { status: 200, headers: { "Content-Type": "application/json" } });
    try {
      const { getTaskDetail } = await import("./console");
      const response = await getTaskDetail("836");
      expect(response.item.owner).toBe("多人：周俊杰、张晓民、Mina 等 4 人");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("computes task detail progress from completed TODOs", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({
      item: {
        project: {
          id: 836,
          title: "客户项目",
          status: "active",
          category: "projects",
          priority: "P1",
          risk_level: "medium",
          owner_name: "Mina",
          current_state: "执行中",
          next_step: "继续推进",
          facts: [],
          tags: [],
        },
        todos: [
          { id: 1, status: "done" },
          { id: 2, status: "open" },
        ],
        updates: [],
      },
      meta: { snapshot_at: "2026-08-29T18:27:11Z" },
    }), { status: 200, headers: { "Content-Type": "application/json" } });
    try {
      const { getTaskDetail } = await import("./console");
      const response = await getTaskDetail("836");
      expect(response.item.progress).toBe("1/2（50%）");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
