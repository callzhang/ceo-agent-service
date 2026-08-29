import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getStatus = vi.hoisted(() => vi.fn());
vi.mock("../api/console", () => ({ getStatus, displayValue: (value: unknown) => typeof value === "string" ? value || "未提供" : JSON.stringify(value) }));

import { StatusPage } from "./StatusPage";

describe("StatusPage", () => {
  beforeEach(() => {
    getStatus.mockResolvedValue({ item: {
      service: { state: "running", pid: 42, runs: 3, detail: "running", ok: true },
      summary: { processing: 0, retryable: 0, failed: 0 },
      components: [{ name: "producer", role: "message scan", cadence: "60s" }],
      connectors: { dingtalk: { state: "ready", reason_code: "ready" } },
      wechat: { reader: { status: "ready", enabled: true }, sender: { status: "ready", enabled: true }, preflight: { status: "ready" }, account: { ready: true } },
      queues: [{ name: "Reply tasks", table: "reply_tasks", counts: { done: 1 }, pending: 0, processing: 0, retryable: 0, failed: 0, latest_updated_at: "now", latest_error: "" }],
    }, meta: { snapshot_at: "2026-08-29T00:00:00Z" } });
  });

  it("renders status domains as readable sections", async () => {
    render(<MemoryRouter><StatusPage /></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "Runtime Monitor" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Connector health" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Queues" })).toBeInTheDocument();
    expect(screen.queryByText("[object Object]")).not.toBeInTheDocument();
  });
});
