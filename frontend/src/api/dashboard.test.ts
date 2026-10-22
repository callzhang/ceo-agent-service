import { afterEach, describe, expect, it, vi } from "vitest";
import { getDashboardQueue } from "./dashboard";
const meta = {
  page: 1,
  page_size: 20,
  total: 0,
  next_cursor: "",
  has_more: false,
  snapshot_at: "now",
};
afterEach(() => vi.unstubAllGlobals());
describe("Dashboard queue API", () => {
  it("sends the tab/page and abort signal", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ items: [], counts: { active: 2, queued: 3 }, meta }),
        ),
      );
    vi.stubGlobal("fetch", fetch);
    const signal = new AbortController().signal;
    const result = await getDashboardQueue("queued", 2, signal);
    expect(fetch).toHaveBeenCalledWith(
      "/api/console/dashboard/queues?state=queued&page=2&page_size=20",
      expect.objectContaining({ signal }),
    );
    expect(result.counts.queued).toBe(3);
  });
  it("rejects a missing count instead of fabricating an empty queue", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ items: [], counts: { active: 0 }, meta }),
          ),
        ),
    );
    await expect(getDashboardQueue("active")).rejects.toThrow(
      "invalid Dashboard queue",
    );
  });
});
