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

describe("repository Skill API", () => {
  it("sends the searched immutable identity rather than the current branch", async () => {
    const { installSkill, searchSkills } = await import("./skills");
    const originalFetch = globalThis.fetch;
    const requests: {path: string; body: unknown}[] = [];
    globalThis.fetch = async (path, init) => {
      requests.push({path: String(path), body: JSON.parse(String(init?.body))});
      return new Response(JSON.stringify({items: [], errors: []}), {headers: {"Content-Type": "application/json"}});
    };
    try {
      await searchSkills("example");
      await installSkill({repo_url: "https://github.com/example/skills", commit: "a".repeat(40), skill_path: "skills/example"});
      expect(requests).toEqual([
        {path: "/api/console/settings/skill-search", body: {query: "example"}},
        {path: "/api/console/settings/skill-installs", body: {repo_url: "https://github.com/example/skills", commit: "a".repeat(40), skill_path: "skills/example"}},
      ]);
    } finally { globalThis.fetch = originalFetch; }
  });
});
