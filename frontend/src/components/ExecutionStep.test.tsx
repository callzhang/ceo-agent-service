import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ExecutionStep } from "./ExecutionStep";

describe("ExecutionStep", () => {
  it("shows native text-block return previews without changing exact JSON evidence", () => {
    const output = '[{"type":"input_text","text":"35 passed"}]';
    render(<ExecutionStep kind="tool" status="completed" payload={{ kind: "trace", name: "exec", input: "pytest", output }} />);
    expect(document.querySelector(".execution-step > summary")).toHaveTextContent("35 passed");
    expect(document.querySelector(".execution-result-preview")).not.toHaveTextContent("input_text");
    expect(screen.getByText(output, { selector: "pre" })).toBeInTheDocument();
  });
  it("bounds the closed preview while retaining exact full evidence", async () => {
    const output = "start\n" + "x".repeat(1000) + "\nend";
    render(<ExecutionStep kind="tool" status="completed" payload={{ kind: "trace", name: "shell", input: "pytest", output }} />);
    expect(document.querySelector(".execution-result-preview")?.textContent?.length).toBeLessThan(380);
    expect(document.querySelector(".execution-result-preview")).toHaveTextContent("start");
    expect(document.querySelector(".execution-result-preview")).toHaveTextContent("end");
    await userEvent.click(screen.getByText("pytest", { selector: "small" }));
    expect(screen.getByText(output, { selector: "pre", normalizer: (value) => value })).toBeVisible();
  });
  it("shows array commands and output in the closed summary", () => {
    render(<ExecutionStep kind="tool" status="completed" payload={{ kind: "command", command: ["/bin/zsh", "-lc", "pytest tests/example.py"], output: "3 passed" }} />);
    expect(document.querySelector(".execution-step")).not.toHaveAttribute("open");
    expect(document.querySelector(".execution-step > summary")).toHaveTextContent("pytest tests/example.py");
    expect(document.querySelector(".execution-step > summary")).toHaveTextContent("3 passed");
  });
  it("previews arbitrary tool arguments and results", () => {
    render(<ExecutionStep kind="tool" status="failed" payload={{ kind: "trace", name: "Read", input: '{"file_path":"/repo/app.py"}', output: "source not found" }} />);
    expect(document.querySelector(".execution-step > summary")).toHaveTextContent("/repo/app.py");
    expect(document.querySelector(".execution-step > summary")).toHaveTextContent("source not found");
  });
  it("keeps string tool evidence exact rather than reserializing JSON numbers", () => {
    render(<ExecutionStep kind="tool" status="completed" payload={{ kind: "trace", name: "shell", input: '{"message_id":9007199254740993}', output: "1e400" }} />);
    expect(screen.getByText('{"message_id":9007199254740993}', { selector: "pre" })).toBeInTheDocument();
    expect(screen.getByText("1e400", { selector: "pre" })).toBeInTheDocument();
  });
  it("previews the command and opens input/output before optional technical metadata", async () => {
    render(<ExecutionStep kind="tool" status="completed" payload={{ kind: "trace", name: "shell", input: { cmd: "pytest tests/test_example.py" }, output: "3 passed", tool_call_id: "exact-call" }} />);
    const preview = screen.getByText("pytest tests/test_example.py");
    expect(preview).toBeVisible();
    await userEvent.click(preview);
    expect(screen.getByText("3 passed", { selector: "pre" })).toBeVisible();
    const technical = screen.getByText("调用详情").closest("details");
    expect(technical).not.toHaveAttribute("open");
    await userEvent.click(screen.getByText("调用详情"));
    expect(screen.getByText("exact-call")).toBeVisible();
  });
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
    expect(screen.getByText(/frontend\/src\/app\.tsx/, { selector: "pre" })).toBeInTheDocument();
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
    expect(screen.getByText(/"calendars": \[/, { selector: "pre" })).toBeInTheDocument();
    expect(screen.getByText(/"structuredContent"/, { selector: "pre" })).toBeInTheDocument();
  });

  it("renders a session trace with separate tool identity, input and output", () => {
    render(
      <ExecutionStep
        kind="tool"
        status="completed"
        payload={{
          kind: "trace",
          name: "functions.exec_command",
          tool_call_id: "call-3",
          input: { cmd: "rg --files app" },
          output: "app/codex_history.py\n",
        }}
      />,
    );

    expect(screen.getAllByText("functions.exec_command")).toHaveLength(2);
    expect(screen.getByText("调用名称")).toBeInTheDocument();
    expect(screen.getByText("输入")).toBeInTheDocument();
    expect(screen.getAllByText(/rg --files app/)).toHaveLength(2);
    expect(screen.getByText("输出")).toBeInTheDocument();
    expect(screen.getByText(/app\/codex_history\.py/, { selector: "pre" })).toBeInTheDocument();
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
