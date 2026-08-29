import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConsoleRouter } from "./router";

describe("console router", () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each([
    ["/", "Agent 工作台"],
    ["/history", "History"],
    ["/tasks", "Tasks"],
    ["/tasks/836", "Task 836"],
    ["/settings?tab=status", "Settings"],
    ["/user-feedback", "用户反馈"],
    ["/tutorial", "Tutorial"],
    ["/notifications", "Notifications"],
    ["/codex/session-1", "Codex Session"],
    ["/wechat/review", "WeChat 待发审核"],
  ])("renders a deep link for %s", async (path, heading) => {
    if (path.startsWith("/settings")) {
      vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ item: { service: { state: "running" }, summary: {}, components: [], queues: [], connectors: {}, wechat: {} }, meta: { snapshot_at: "" } }), { status: 200, headers: { "Content-Type": "application/json" } })));
    }
    window.history.replaceState({}, "", path);
    render(<ConsoleRouter />);

    expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
  });

  it("renders a not-found page for an unknown business path", async () => {
    window.history.replaceState({}, "", "/does-not-exist");
    render(<ConsoleRouter />);

    expect(await screen.findByRole("heading", { name: "页面不存在" })).toBeInTheDocument();
  });
});
