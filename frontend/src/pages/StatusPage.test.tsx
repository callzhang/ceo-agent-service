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
      email: {
        status: "ready",
        updated_at: "now",
        process: { status: "ready", accounts: 1, runtime_loops: 3, readiness_ready: 2, readiness_total: 2, updated_at: "now" },
        runtime_loops: [
          { scope: "component:email-scan-actions", status: "ready", updated_at: "now" },
          { scope: "component:email-agent-consumer", status: "ready", updated_at: "now" },
          { scope: "component:email-training", status: "degraded", error_code: "training_runtime_error", error_stage: "active_model_tick", error_type: "RuntimeError", updated_at: "now" },
        ],
        accounts: [{ scope: "account:dingtalk_primary", status: "ready", updated_at: "now" }],
        checks: [{ scope: "component:email-provider-actions", status: "degraded", error_code: "provider_action_failed", updated_at: "now" }],
      },
      meeting_memory_health: {
        pending: 5,
        due: 3,
        delayed: 1,
        processing: 2,
        retryable: 1,
        failed: 0,
        oldest_due_at: "2026-09-15T10:00:00Z",
        oldest_due_seconds: 7200,
        completed_last_hour: 4,
        active_agents: 2,
        ghost_runtime_attempts: 1,
        delayed_after_seconds: 1800,
      },
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
    expect(screen.getByRole("heading", { name: "Runtime loops" })).toBeInTheDocument();
    expect(screen.getByText("3", { selector: ".email-worker-summary strong" })).toBeInTheDocument();
    expect(screen.getByText("2/2")).toBeInTheDocument();
    expect(screen.getByText("email-scan-actions")).toBeInTheDocument();
    expect(screen.getByText("dingtalk_primary")).toBeInTheDocument();
    expect(screen.getByText("email-provider-actions")).toBeInTheDocument();
    expect(screen.getByText("training_runtime_error · active_model_tick · RuntimeError")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "会议结论同步" })).toBeInTheDocument();
    expect(screen.getByText("最久等待")).toBeInTheDocument();
    expect(screen.getByText("近一小时完成")).toBeInTheDocument();
    expect(screen.getByText("真实活跃 Agent")).toBeInTheDocument();
    expect(screen.getByText("幽灵运行记录")).toBeInTheDocument();
    expect(screen.getByText("Internal checks (1)").closest("details")).toHaveAttribute("open");
    expect(screen.getByRole("heading", { name: "Queues" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Dispatcher queues" })).toBeInTheDocument();
    expect(screen.getByText("scheduled_execution")).toBeInTheDocument();
    expect(screen.getByText("runtime unavailable")).toBeInTheDocument();
    expect(screen.queryByText("Polling interval")).not.toBeInTheDocument();
    expect(screen.queryByText("[object Object]")).not.toBeInTheDocument();
  });

  it("keeps healthy internal checks collapsed by default", async () => {
    const response = await getStatus();
    response.item.email.checks = [{ scope: "component:email-scan-config", status: "ready", updated_at: "now" }];
    getStatus.mockResolvedValue(response);

    render(<MemoryRouter><StatusPage /></MemoryRouter>);

    const disclosure = (await screen.findByText("Internal checks (1)")).closest("details");
    expect(disclosure).not.toHaveAttribute("open");
  });
});
