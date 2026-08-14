import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ExecutionStep } from "./ExecutionStep";

describe("ExecutionStep", () => {
  it("exposes its state label as a narrowly scoped status announcement", () => {
    render(<ExecutionStep kind="tool" status="aborted" payload={{ tool: "read", summary: "任务已结束，未收到工具完成事件。" }} />);

    expect(screen.getByRole("status")).toHaveTextContent("已中止");
  });

  it("renders exact command action, output, raw provider item and timing", () => {
    render(
      <ExecutionStep
        kind="tool"
        status="completed"
        startedAt="2026-08-13 10:00:00"
        completedAt="2026-08-13 10:00:02"
        payload={{
          tool_call_id: "tool-call-1",
          kind: "command",
          name: "rg",
          native_id: "native-1",
          status: "completed",
          command: "rg --files frontend/src",
          cwd: "/Users/derek/Documents/Projects/ceo-agent-service",
          output: "frontend/src/app.tsx\n",
          exit_code: 0,
          provider_item: { id: "native-1", type: "command_execution", exit_code: 0 },
        }}
      />,
    );

    expect(screen.getAllByText("rg --files frontend/src")).toHaveLength(2);
    expect(screen.getByText("/Users/derek/Documents/Projects/ceo-agent-service")).toBeInTheDocument();
    expect(screen.getByText(/frontend\/src\/app\.tsx/)).toBeInTheDocument();
    expect(screen.getByText(/"type": "command_execution"/)).toBeInTheDocument();
    expect(screen.getByText("2.0 秒")).toBeInTheDocument();
    expect(screen.getByText("tool-call-1")).toBeInTheDocument();
    expect(screen.getByText("native-1")).toBeInTheDocument();
  });

  it("renders exact MCP identity, arguments and result", () => {
    render(
      <ExecutionStep
        kind="tool"
        status="completed"
        payload={{
          tool_call_id: "tool-call-2",
          kind: "mcp",
          name: "codex_apps.google_calendar.search_events",
          native_id: "native-2",
          status: "completed",
          server: "codex_apps",
          tool: "google_calendar.search_events",
          arguments: { calendars: ["primary"] },
          result: { structuredContent: { events: [] } },
          provider_item: { id: "native-2", type: "mcp_tool_call" },
        }}
      />,
    );

    expect(screen.getByText("codex_apps.google_calendar.search_events")).toBeInTheDocument();
    expect(screen.getByText(/"calendars": \[/)).toBeInTheDocument();
    expect(screen.getByText(/"structuredContent"/)).toBeInTheDocument();
  });

  it("truthfully marks historical generic tool events as incomplete", () => {
    render(<ExecutionStep kind="tool" status="completed" payload={{ tool: "本地命令", summary: "已完成" }} />);

    expect(screen.getByText("历史事件未记录命令详情")).toBeInTheDocument();
  });

  it("renders credential-shaped local evidence unchanged", () => {
    render(
      <ExecutionStep
        kind="file"
        status="completed"
        payload={{
          filename: "/Users/derek/Documents/private.env",
          change: "api_key=local-workbench-value",
        }}
      />,
    );

    expect(screen.getByText("/Users/derek/Documents/private.env")).toBeInTheDocument();
    expect(screen.getByText("api_key=local-workbench-value")).toBeInTheDocument();
  });
});
