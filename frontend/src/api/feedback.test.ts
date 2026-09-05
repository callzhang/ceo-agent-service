import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConsoleApiError } from "./console";
import { associateFeedbackTurn, claimFeedbackBatch, getFeedbackBatch, getFeedbackIterationCapability, listPendingFeedback, resolveFeedbackBatch } from "./feedback";

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

  it("reads the feedback iteration capability and typed decision history", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ enabled: false, config_id: 24 }), { status: 200, headers: { "content-type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ item: {
        batch_id: "batch-1", status: "resolved", requested_count: 1, items: [{ ...validItem, scope_receipt: { decision_id: 8, scope: "skill_only", evidence: { commit_sha: "full-sha", runtime_config_id: 24, previous_runtime_config_id: 23, load_receipt_id: 9, associations: { "fb-1": { workbench_task_id: "task-1", workbench_turn_id: "turn-1", attempt_id: 1, agent_run_id: 2 } } } } }], decisions: [{
          id: 8, batch_id: "batch-1", workbench_task_id: "task-1", workbench_turn_id: "turn-1", feedback_keys: ["fb-1"], round_ids: [3], created_at: "2026-09-05T00:00:00Z",
          decision: { scope: "skill_only", root_cause: "missing procedure", feedback_keys: ["fb-1"], source_references: ["attempt#8308", "run#445"], target_skill_revisions: [{ skill_id: 4, from_revision: 12, to_revision: 13 }], why_not_code: "The route exists.", acceptance: { scenario: "attempt#8308", expected_behavior: "uses the route", verification: ["focused regression"] } },
          receipt: { config_id: 24, revision_id: 13, sha256: "abc123", load_receipt_id: 9 },
        }],
      }, meta: { snapshot_at: "now" } }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getFeedbackIterationCapability()).resolves.toEqual({ enabled: false, config_id: 24 });
    const batch = await getFeedbackBatch("batch-1");
    expect(batch.item.decisions[0]).toMatchObject({
      decision: { scope: "skill_only", target_skill_revisions: [{ to_revision: 13 }] },
      workbench_task_id: "task-1",
    });
    expect(batch.item.items[0].scope_receipt).toMatchObject({
      decision_id: 8,
      evidence: { commit_sha: "full-sha", previous_runtime_config_id: 23, load_receipt_id: 9 },
    });
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/console/settings/feedback-iteration");
  });
});
