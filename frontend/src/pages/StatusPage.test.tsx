import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getStatus = vi.hoisted(() => vi.fn());
vi.mock("../api/console", () => ({ getStatus, displayValue: (value: unknown) => typeof value === "string" ? value || "未提供" : JSON.stringify(value) }));

import { StatusPage } from "./StatusPage";

describe("StatusPage", () => {
  beforeEach(() => {
    getStatus.mockResolvedValue({ item: {
      service: { label: "main", target: "gui/1/main", state: "running", pid: "42", runs: "3", detail: "running", ok: true, initialized: "1", last_terminating_signal: "", returncode: 0 },
      system_health: { state: "healthy", detail: "healthy", checked_at: "now", violations: 0, components: [] },
      summary: { queue_count: 1, pending: 0, processing: 0, retryable: 0, failed: 0, attention: 0 },
      components: [{ name: "agent-cron-scheduler", role: "business trigger scheduling", cadence: "task configured", status: "running", latest_tick_at: "2026-09-08T12:01:00Z", latest_error: "previous scan failed", latest_error_at: "2026-09-08T12:00:00Z" }],
      connectors: { dingtalk: { state: "ready", reason_code: "ready" } },
      email: { status: "ready", updated_at: "now", entries: [{ scope: "component:email-provider-actions", status: "ready", updated_at: "now" }] },
      wechat: { reader: { status: "ready", enabled: true, error: "" }, sender: { status: "ready", enabled: true, error: "" }, preflight: { status: "ready", error: "" }, account: { ready: true, account_id: "" } },
      queues: [{ name: "Reply tasks", table: "reply_tasks", counts: { done: 1 }, pending: 0, processing: 0, retryable: 0, failed: 0, latest_updated_at: "now", latest_error: "" }],
      dispatcher_queues: [
        { name: "scheduled", pending: 1, due: 1, oldest_available_at: "2026-09-08T12:00:00Z", running: 0, latest_error: "runtime unavailable" },
        { name: "scheduled_execution", pending: 0, due: 0, oldest_available_at: null, running: 1, latest_error: "" },
      ],
      attention_rows: [],
      database: { path: "/tmp/worker.sqlite3" },
    }, meta: { snapshot_at: "2026-08-29T00:00:00Z" } });
  });

  it("renders status domains as readable sections", async () => {
    render(<MemoryRouter><StatusPage /></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "Runtime Monitor" })).toBeInTheDocument();
    expect(screen.getByText(/previous scan failed/)).toBeInTheDocument();
    expect(screen.getByText("2026-09-08T12:01:00Z")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Connector health" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Email worker" })).toBeInTheDocument();
    expect(screen.getByText("component:email-provider-actions")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Queues" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Dispatcher queues" })).toBeInTheDocument();
    expect(screen.getByText("scheduled_execution")).toBeInTheDocument();
    expect(screen.getByText("runtime unavailable")).toBeInTheDocument();
    expect(screen.queryByText("Polling interval")).not.toBeInTheDocument();
    expect(screen.queryByText("[object Object]")).not.toBeInTheDocument();
  });
});
