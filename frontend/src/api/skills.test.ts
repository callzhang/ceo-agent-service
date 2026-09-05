import { describe, expect, it } from "vitest";
import { listRuntimeSkillLoadReceipts } from "./skills";

describe("managed Skills API", () => {
  it("preserves the server receipt JSON encoding", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({ items: [{ id: 1, config_id: 2, pid: 3, loaded_json: '{"1":"digest"}', error: "", created_at: "2026-09-05T00:00:00Z" }] }), { headers: { "Content-Type": "application/json" } });
    try {
      const result = await listRuntimeSkillLoadReceipts(2);
      expect(result.items[0].loaded_json).toBe('{"1":"digest"}');
    } finally { globalThis.fetch = originalFetch; }
  });
});
