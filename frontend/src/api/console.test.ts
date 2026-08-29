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
});
