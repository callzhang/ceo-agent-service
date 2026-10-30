import { describe, expect, it } from "vitest";

import { ConsoleApiError, listAttention, displayValue, parseConsoleList, request, listEmailClassifications, confirmEmailClassification, getStatus, listBusinessProjects, listBusinessTasks, getBusinessProjectDetail, getBusinessTaskDetail, listHistoryTypes, renameRuntimeRoute, getLegacyProjectDetail } from "./console";

function statusEnvelope() {
  return {
    item: {
      service: { label: "main", target: "gui/1/main", ok: true, state: "running", detail: "running", pid: "42", runs: "1", initialized: "1", last_terminating_signal: "", returncode: 0 },
      system_health: { state: "healthy", detail: "healthy", checked_at: "now", violations: 0, components: [] },
      components: [{ name: "agent-cron-scheduler", role: "business trigger scheduling", cadence: "task configured", status: "running", latest_tick_at: "now", latest_error: "", latest_error_at: "" }],
      connectors: {},
      email: {
        status: "ready",
        updated_at: "now",
        process: { status: "ready", accounts: 1, runtime_loops: 3, readiness_ready: 2, readiness_total: 2, updated_at: "now" },
        runtime_loops: [],
        accounts: [],
        checks: [],
      },
      wechat: { reader: { status: "ready", enabled: true, error: "" }, sender: { status: "ready", enabled: true, error: "" }, preflight: { status: "on_send", error: "" }, account: { ready: true, account_id: "account" } },
      queues: [{ name: "Reply tasks", table: "reply_tasks", counts: {}, pending: 0, processing: 0, failed: 0, retryable: 0, latest_updated_at: "", latest_error: "" }],
      dispatcher_queues: [{ name: "scheduled", pending: 0, due: 0, oldest_available_at: null, running: 0, latest_error: "" }],
      attention_rows: [],
      database: { path: "/tmp/worker.sqlite3" },
      summary: { queue_count: 1, pending: 0, processing: 0, failed: 0, retryable: 0, attention: 0 },
    },
    meta: { snapshot_at: "now" },
  };
}

describe("console API helpers", () => {
  it.each(["origin", "suggested_owner", "suggestion_reason", "deadline_type"])("requires Task %s instead of accepting the obsolete Task contract", async (field) => {
    const task: Record<string, unknown> = { id: "1", title: "客户回款", origin: "agent_suggestion", suggested_owner: "王五", suggestion_reason: "需要确认付款日期", stage: "candidate", status: "open", commitment_status: "none", owner: "", deadline_at: "", deadline_type: "", business_relevance: "relevant", anchor_labels: [], updated_at: "now", detail_url: "/tasks/item/1" };
    delete task[field];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({ items: [task], meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "now" } }), { status: 200 });
    try { await expect(listBusinessTasks()).rejects.toThrow("invalid business task response"); }
    finally { globalThis.fetch = originalFetch; }
  });

  it.each(["overall_owner", "overall_responsibility", "attention_reason", "updated_at"])("requires persisted project %s", async (field) => {
    const project: Record<string, unknown> = { id: "2", title: "客户项目", registry_source: "经营会", canonical_anchor_id: 3, confirmed_task_count: 0, detail_url: "/tasks/project/2", overall_owner: "", overall_responsibility: "", attention_reason: "", updated_at: "now" };
    delete project[field];
    const meta = { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "now" };
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({ items: [project], candidates: [], candidate_meta: { ...meta, total: 0 }, meta }), { status: 200 });
    try { await expect(listBusinessProjects()).rejects.toThrow("invalid business project response"); }
    finally { globalThis.fetch = originalFetch; }
  });

  it("requires the explicit nullable project context, suggestions and bounded evidence/history", async () => {
    const summary = { id: "2", title: "客户项目", registry_source: "经营会", canonical_anchor_id: 3, confirmed_task_count: 0, detail_url: "/tasks/project/2", overall_owner: "", overall_responsibility: "", attention_reason: "", updated_at: "now" };
    const pageMeta = { page: 1, page_size: 20, total: 0, next_cursor: "", has_more: false, snapshot_at: "now" };
    const item: Record<string, unknown> = { summary, confirmed_tasks: [], context: null, suggestions: [], evidence_signals: [], context_revisions: [], evidence_meta: pageMeta, context_revision_meta: pageMeta };
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({ item, meta: { snapshot_at: "now" } }), { status: 200 });
    try {
      expect((await getBusinessProjectDetail("2")).item.context).toBeNull();
      for (const field of ["context", "suggestions", "evidence_signals", "context_revisions", "evidence_meta", "context_revision_meta"]) {
        const saved = item[field];
        delete item[field];
        await expect(getBusinessProjectDetail("2")).rejects.toThrow("invalid business project detail");
        item[field] = saved;
      }
    } finally { globalThis.fetch = originalFetch; }
  });
  it("accepts unavailable project originals only with a null body and reason", async () => {
    const summary = { id: "2", title: "客户项目", registry_source: "经营会", canonical_anchor_id: 3, confirmed_task_count: 0, detail_url: "/tasks/project/2", overall_owner: "", overall_responsibility: "", attention_reason: "", updated_at: "now" };
    const pageMeta = { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "now" };
    const signal: Record<string, unknown> = { id: 7, source_type: "ai_minutes", source_ref: "meeting-7", source_time: "now", conversation_id: "", conversation_title: "", author_user_id: "", author_name: "", author_kind: "unknown", context_json: "{}", dedupe_key: "signal-7", created_at: "now", evidence_text: null, source_available: false, source_unavailable_reason: "source_missing" };
    const item = { summary, confirmed_tasks: [], context: null, suggestions: [], evidence_signals: [signal], context_revisions: [], evidence_meta: pageMeta, context_revision_meta: pageMeta };
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({ item, meta: { snapshot_at: "now" } }), { status: 200 });
    try {
      expect((await getBusinessProjectDetail("2")).item.evidence_signals[0].evidence_text).toBeNull();
      signal.source_unavailable_reason = null;
      await expect(getBusinessProjectDetail("2")).rejects.toThrow("invalid business project detail");
      signal.source_unavailable_reason = "source_missing";
      signal.evidence_text = "fabricated";
      await expect(getBusinessProjectDetail("2")).rejects.toThrow("invalid business project detail");
    } finally { globalThis.fetch = originalFetch; }
  });
  it("preserves 64-bit email IDs from JSON through the feedback URL", async () => {
    const originalFetch = globalThis.fetch;
    const id = "8423079112545370123";
    const calls: string[] = [];
    globalThis.fetch = async (input) => {
      calls.push(String(input));
      return new Response(JSON.stringify(calls.length === 1 ? {
        items: [{ id }], meta: { page: 1, page_size: 20, total: 1, next_cursor: "", has_more: false, snapshot_at: "2026-09-06T00:00:00Z" },
      } : { ok: true, item: { id }, message: "已保存" }), {status:200});
    };
    try {
      const result = await listEmailClassifications("pending_feedback");
      expect(result.items[0].id).toBe(id);
      const saved = await confirmEmailClassification(result.items[0].id, "notification", `email-feedback:${id}`, null);
      expect(calls[1]).toBe(`/api/console/email/classifications/${id}/feedback`);
      expect(saved.item.id).toBe(id);
    } finally { globalThis.fetch = originalFetch; }
  });
  it("asks the service to rename a runtime route", async () => {
    const calls: Array<{ url: string; body: string }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(url), body: String(init?.body ?? "") });
      return new Response(JSON.stringify({ ok: true, item: {}, message: "已改名", meta: { updated_at: "now" } }), { status: 200 });
    }) as typeof fetch;
    try {
      await renameRuntimeRoute("codex_api", "kksj");
      expect(calls).toEqual([{ url: "/api/console/settings/agent-runtime/routes/codex_api/rename", body: JSON.stringify({ new_name: "kksj" }) }]);
    } finally { globalThis.fetch = originalFetch; }
  });
  it("reads the History types the service lists, and rejects a malformed one", async () => {
    const originalFetch = globalThis.fetch;
    const meta = { page: 1, page_size: 2, total: 2, next_cursor: "", has_more: false, snapshot_at: "now" };
    const urls: string[] = [];
    let items: unknown[] = [{ value: "dingtalk", label: "钉钉消息" }, { value: "calendar", label: "日历邀请" }];
    globalThis.fetch = (async (url: RequestInfo | URL) => {
      urls.push(String(url));
      return new Response(JSON.stringify({ items, meta }), { status: 200 });
    }) as typeof fetch;
    try {
      await expect(listHistoryTypes()).resolves.toEqual([{ value: "dingtalk", label: "钉钉消息" }, { value: "calendar", label: "日历邀请" }]);
      expect(urls).toEqual(["/api/console/history/types"]);
      items = [{ value: "dingtalk" }];
      await expect(listHistoryTypes()).rejects.toThrow("invalid History type");
    } finally { globalThis.fetch = originalFetch; }
  });
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

  it.each([
    ["missing field", (payload: ReturnType<typeof statusEnvelope>) => { delete (payload.item.summary as Partial<typeof payload.item.summary>).failed; }],
    ["wrong nested type", (payload: ReturnType<typeof statusEnvelope>) => { (payload.item.dispatcher_queues[0] as { pending: unknown }).pending = "0"; }],
    ["extra nested field", (payload: ReturnType<typeof statusEnvelope>) => { Object.assign(payload.item.components[0], { unexpected: true }); }],
  ])("rejects a status response with %s", async (_label, mutate) => {
    const originalFetch = globalThis.fetch;
    const payload = statusEnvelope();
    mutate(payload);
    globalThis.fetch = async () => new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
    try {
      await expect(getStatus()).rejects.toThrow("invalid status response");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("accepts the complete status response contract", async () => {
    const originalFetch = globalThis.fetch;
    const payload = statusEnvelope();
    globalThis.fetch = async () => new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
    try {
      await expect(getStatus()).resolves.toEqual(payload);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it.each([null, { checked: 2, unavailable: 3 }])("accepts existing native delivery coverage %j", async (coverage) => {
    const originalFetch = globalThis.fetch;
    const payload = statusEnvelope();
    Object.assign(payload.item.system_health, { native_delivery_coverage: coverage });
    globalThis.fetch = async () => new Response(JSON.stringify(payload), { status: 200 });
    try { await expect(getStatus()).resolves.toEqual(payload); }
    finally { globalThis.fetch = originalFetch; }
  });

  it.each([
    { checked: -1, unavailable: 0 },
    { checked: 1, unavailable: "3" },
    { checked: 1 },
    { checked: 1, unavailable: 3, unexpected: true },
  ])("rejects malformed native delivery coverage %j", async (coverage) => {
    const originalFetch = globalThis.fetch;
    const payload = statusEnvelope();
    Object.assign(payload.item.system_health, { native_delivery_coverage: coverage });
    globalThis.fetch = async () => new Response(JSON.stringify(payload), { status: 200 });
    try { await expect(getStatus()).rejects.toThrow("invalid status response"); }
    finally { globalThis.fetch = originalFetch; }
  });

  it("accepts explicit nulls for optional status fields", async () => {
    const originalFetch = globalThis.fetch;
    const payload = statusEnvelope();
    (payload.item.email.accounts as unknown[]).push({
      scope: "account:dingtalk_primary",
      status: "ready",
      error_code: null,
      failures: null,
      persisted_count: 0,
      task_count: null,
      isolated_count: null,
      superseded_count: null,
      unresolved_count: null,
      updated_at: "now",
    });
    (payload.item.attention_rows as unknown[]).push({
      category: "Service error",
      id: "1",
      status: "failed",
      context: "worker",
      summary: "failed",
      updated_at: "now",
      error: "failed",
      root_cause: null,
      detail_url: null,
    });
    globalThis.fetch = async () => new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
    try {
      await expect(getStatus()).resolves.toEqual(payload);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it.each(["provider_risk_rejected", null])("preserves Attention diagnostic error_code %s in Status", async (errorCode) => {
    const originalFetch = globalThis.fetch;
    const payload = statusEnvelope();
    (payload.item.attention_rows as unknown[]).push({
      category: "Reply task", id: "1", status: "failed", context: "Diagnostic",
      summary: "Review this request.", updated_at: "now", error: "Tool call rejected",
      error_code: errorCode,
    });
    globalThis.fetch = async () => new Response(JSON.stringify(payload), { status: 200 });
    try {
      await expect(getStatus()).resolves.toEqual(payload);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it.each([{ error_code: 123 }, { error_code: "provider_failure", undeclared_diagnostic: true }])(
    "rejects malformed or undeclared Attention diagnostics %j", async (diagnostics) => {
      const originalFetch = globalThis.fetch;
      const payload = statusEnvelope();
      (payload.item.attention_rows as unknown[]).push({
        category: "Reply task", id: "1", status: "failed", context: "Diagnostic",
        summary: "Review this request.", updated_at: "now", error: "Tool call rejected",
        ...diagnostics,
      });
      globalThis.fetch = async () => new Response(JSON.stringify(payload), { status: 200 });
      try {
        await expect(getStatus()).rejects.toThrow("invalid status response");
      } finally {
        globalThis.fetch = originalFetch;
      }
    },
  );

  it("accepts the separately typed nonempty human decision Status rows", async () => {
    const originalFetch = globalThis.fetch;
    const payload = statusEnvelope();
    Object.assign(payload.item, { human_decision_rows: [{
      category: "Reply task", id: "2", status: "needs_human", context: "Decision",
      summary: "Current instance decision", updated_at: "now", error: "",
      error_code: null, root_cause: null, detail_url: "/history/2",
      detail_label: "Decision basis", detail: "Verified facts and available choices.",
    }] });
    globalThis.fetch = async () => new Response(JSON.stringify(payload), { status: 200 });
    try {
      await expect(getStatus()).resolves.toEqual(payload);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it.each([
    { detail: "Verified facts" }, { detail_label: "Decision basis" },
    { detail_label: "Decision basis", detail: 123 },
    { detail_label: "Decision basis", detail: "Verified facts", undeclared_detail: true },
  ])("rejects invalid human decision Status details %j", async (details) => {
    const originalFetch = globalThis.fetch;
    const payload = statusEnvelope();
    Object.assign(payload.item, { human_decision_rows: [{
      category: "Reply task", id: "2", status: "needs_human", context: "Decision",
      summary: "Current instance decision", updated_at: "now", error: "", ...details,
    }] });
    globalThis.fetch = async () => new Response(JSON.stringify(payload), { status: 200 });
    try {
      await expect(getStatus()).rejects.toThrow("invalid status response");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it.each([null, {}, "not an array"])("rejects human decision Status rows that are not an array: %j", async (rows) => {
    const originalFetch = globalThis.fetch;
    const payload = statusEnvelope();
    Object.assign(payload.item, { human_decision_rows: rows });
    globalThis.fetch = async () => new Response(JSON.stringify(payload), { status: 200 });
    try {
      await expect(getStatus()).rejects.toThrow("invalid status response");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("requires the separately paged project candidate metadata", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (input) => {
      expect(String(input)).toBe("/api/console/tasks/projects?page=1&candidate_page=2&candidate_page_size=20");
      return new Response(JSON.stringify({
        items: [],
        candidates: [],
        meta: { page: 1, page_size: 20, total: 0, next_cursor: "", has_more: false, snapshot_at: "now" },
        candidate_meta: { page: 2, page_size: 20, total: 21, next_cursor: "next", has_more: true, snapshot_at: "now" },
      }), { status: 200 });
    };
    try {
      const result = await listBusinessProjects({ page: 1, candidate_page: 2, candidate_page_size: 20 });
      expect(result.candidate_meta).toMatchObject({ page: 2, total: 21, has_more: true });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("surfaces FastAPI detail messages for actionable validation errors", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(
      JSON.stringify({ detail: "archive, move, and trash are mutually exclusive" }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
    try {
      await expect(request("/api/console/email/config/junk")).rejects.toMatchObject({
        status: 400,
        message: "archive, move, and trash are mutually exclusive",
      } satisfies Partial<ConsoleApiError>);
    } finally {
      globalThis.fetch = originalFetch;
    }
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

  it("exposes only the exact legacy project detail reader for historical projects", async () => {
    const originalFetch = globalThis.fetch;
    let requestedUrl = "";
    globalThis.fetch = async (input) => {
      requestedUrl = String(input);
      return new Response(JSON.stringify({ item: {
        project: {
          id: 17, title: "历史项目", status: "active", owner: "", owner_name: "",
          owner_user_id: "", goal: "历史目标", facts: [{ id: 1, description: "来源事实", source: "meeting-71", created_at: "2026-09-01" }],
          source_conversations: [{ id: 2, title: "来源会话" }],
        },
        todos: [{ id: 21, title: "历史待办", owner_name: "王五", status: "done" }],
        updates: [{ id: 31, summary: "历史更新" }],
        evidence_candidates: [{ id: 41, evidence_text: "依据候选" }],
        unlinked_follow_ups: [{ id: 51, question_text: "历史催办" }],
      }, meta: { snapshot_at: "2026-09-04" } }), { status: 200 });
    };
    try {
      const api = await import("./console");
      expect("listTasks" in api).toBe(false);
      expect("getTaskDetail" in api).toBe(false);
      const response = await getLegacyProjectDetail("17/with space");
      expect(requestedUrl).toContain("/api/console/tasks/legacy-projects/17%2Fwith%20space");
      expect(response.item).toEqual({
        id: "17", title: "历史项目", status: "active", owner: "王五", description: "历史目标",
        facts: [{ id: "1", description: "来源事实", source: "meeting-71", created: "2026-09-01", updated: "未提供" }],
        source_conversations: [{ id: 2, title: "来源会话" }],
        todos: [{ id: 21, title: "历史待办", owner_name: "王五", status: "done" }],
        updates: [{ id: 31, summary: "历史更新" }],
        evidence_candidates: [{ id: 41, evidence_text: "依据候选" }],
        unlinked_follow_ups: [{ id: 51, question_text: "历史催办" }],
      });
      expect(response.item).not.toHaveProperty("progress");
      expect(response.item).not.toHaveProperty("next_summary");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("rejects a legacy Project shape from the semantic Business Task detail endpoint", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({
      item: { project: { id: 836, title: "旧项目" } },
      meta: { snapshot_at: "2026-09-24" },
    }), { status: 200 });
    try {
      await expect(getBusinessTaskDetail("836")).rejects.toThrow("invalid business task detail");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

});

 it("labels grouped Failed links with each actual Attempt identity", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({items: [{category: "Reply task", records: [
      {category: "Reply task", id: "4100", detail_url: "/attempts/12"},
      {category: "Meeting", id: "4200", detail_url: "/meeting-attempts/35"},
      {category: "Reply task", id: "4300", detail_url: ""},
    ]}], meta: {total: 3}}), {status: 200});
    try {
      const page = await listAttention();
      expect(page.items[0].links).toEqual([
        {label: "查看 Attempt #12", href: "/attempts/12"},
        {label: "查看 Meeting Attempt #35", href: "/meeting-attempts/35"},
      ]);
    } finally { globalThis.fetch = originalFetch; }
  });
