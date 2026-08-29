import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConsoleApiError } from "./console";
import { associateFeedbackTurn, claimFeedbackBatch, getFeedbackBatch, listPendingFeedback, resolveFeedbackBatch } from "./feedback";

const validItem = {
  feedback_key: "fb-1", batch_id: "batch-1", status: "processing",
  workbench_task_id: "task-1", workbench_turn_id: "turn-1", attempt_id: 1, agent_run_id: 2,
  commit_sha: "", test_evidence: {}, restart_evidence: {}, health_evidence: {}, note: "", resolved_at: "",
};

beforeEach(() => vi.restoreAllMocks());

describe("feedback API", () => {
  it("strictly parses batch detail envelopes", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ item: { batch_id: "batch-1", status: "processing", requested_count: 1, items: [validItem] }, meta: { snapshot_at: "2026-08-29T00:00:00Z" } }), { status: 200, headers: { "content-type": "application/json" } })));
    const response = await getFeedbackBatch("batch-1");
    expect(response.item.items[0].feedback_key).toBe("fb-1");
  });

  it("rejects malformed envelopes and preserves ConsoleApiError for non-2xx", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ item: {}, meta: {} }), { status: 200, headers: { "content-type": "application/json" } })));
    await expect(getFeedbackBatch("batch-1")).rejects.toThrow("invalid feedback response");

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: "feedback_batch_required", message: "batch required" }), { status: 409, headers: { "content-type": "application/json" } })));
    await expect(resolveFeedbackBatch("batch-1", { commit_sha: "", test_evidence: {}, restart_evidence: {}, health_evidence: {} })).rejects.toMatchObject({ status: 409, code: "feedback_batch_required" } satisfies Partial<ConsoleApiError>);
  });

  it("sends a stable batch id and forwards abort signals for mutations", async () => {
    const responseBody = JSON.stringify({
      ok: true,
      item: { batch_id: "feedback-import:fb-1", status: "processing", requested_count: 1, items: [validItem], start_message: "start" },
      message: "ok",
      meta: { updated_at: "now" },
    });
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(responseBody, { status: 200, headers: { "content-type": "application/json" } })));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    await claimFeedbackBatch(["fb-1"], "task-1", "", "feedback-import:fb-1", { signal: controller.signal });
    const claimInit = fetchMock.mock.calls[0][1] as RequestInit;
    expect(claimInit.signal).toBe(controller.signal);
    expect(JSON.parse(String(claimInit.body))).toMatchObject({ feedback_keys: ["fb-1"], batch_id: "feedback-import:fb-1" });
    await associateFeedbackTurn("feedback-import:fb-1", "task-1", "turn-1", { signal: controller.signal });
    expect((fetchMock.mock.calls[1][1] as RequestInit).signal).toBe(controller.signal);
  });

  it("requests the drawer page-size limit explicitly", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], meta: { page: 1, page_size: 50, total: 0, next_cursor: "", has_more: false, snapshot_at: "now" } }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await listPendingFeedback({ page_size: 50 });
    expect(String(fetchMock.mock.calls[0][0])).toContain("page_size=50");
  });
});
