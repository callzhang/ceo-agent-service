import { describe, expect, it, vi } from "vitest";

import {
  executionName,
  executionStateLabel,
  formatWorkbenchDateTime,
  parseWorkbenchTimestamp,
  taskStateLabel,
} from "./presentation";

describe("Workbench presentation", () => {
  it("parses backend timestamps as UTC and formats them in the browser timezone", () => {
    vi.stubEnv("TZ", "Asia/Shanghai");
    try {
      const parsed = parseWorkbenchTimestamp("2026-08-13 15:14:36");
      expect(parsed?.toISOString()).toBe("2026-08-13T15:14:36.000Z");
      expect(formatWorkbenchDateTime("2026-08-13 15:14:36")).toMatchObject({
        dateTime: "2026-08-13T15:14:36.000Z",
      });
      expect(formatWorkbenchDateTime("2026-08-13 15:14:36")?.label).toMatch(/23[:：]14[:：]36/);
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it("supports ISO timestamps with an explicit timezone", () => {
    expect(parseWorkbenchTimestamp("2026-08-13T15:14:36Z")?.toISOString()).toBe("2026-08-13T15:14:36.000Z");
    expect(parseWorkbenchTimestamp("2026-08-13T23:14:36+08:00")?.toISOString()).toBe("2026-08-13T15:14:36.000Z");
    expect(parseWorkbenchTimestamp("2026-08-13T15:14:36.1Z")?.toISOString()).toBe("2026-08-13T15:14:36.100Z");
    expect(parseWorkbenchTimestamp("2026-08-13T23:14:36.12+08:00")?.toISOString()).toBe("2026-08-13T15:14:36.120Z");
    expect(parseWorkbenchTimestamp("2026-08-13T15:14:36.123Z")?.toISOString()).toBe("2026-08-13T15:14:36.123Z");
  });

  it("rejects impossible backend dates and invalid timestamp text", () => {
    expect(parseWorkbenchTimestamp("2026-02-30 12:00:00")).toBeNull();
    expect(parseWorkbenchTimestamp("not-a-date")).toBeNull();
    expect(formatWorkbenchDateTime("not-a-date")).toBeNull();
  });

  it("rejects invalid or timezone-less ISO timestamps", () => {
    expect(parseWorkbenchTimestamp("2026-02-30T12:00:00Z")).toBeNull();
    expect(parseWorkbenchTimestamp("2026-08-13T15:14:36")).toBeNull();
    expect(parseWorkbenchTimestamp("2026-08-13T15:14:36+24:00")).toBeNull();
    expect(parseWorkbenchTimestamp("2026-08-13T15:14:36+08:60")).toBeNull();
  });

  it.each([
    ["idle", "空闲"],
    ["queued", "排队中"],
    ["running", "执行中"],
    ["waiting_confirmation", "等待确认"],
    ["completed", "已完成"],
    ["stopped", "已停止"],
    ["failed", "失败"],
  ] as const)("maps %s task state to %s", (state, label) => {
    expect(taskStateLabel(state)).toBe(label);
  });

  it("uses a safe label for an unexpected runtime task state", () => {
    expect(taskStateLabel("unexpected" as unknown as Parameters<typeof taskStateLabel>[0])).toBe("状态未知");
  });

  it.each([
    ["completed", "已完成"],
    ["success", "已完成"],
    ["failed", "失败"],
    ["error", "失败"],
    ["aborted", "已中止"],
    ["anything-else", "执行中"],
  ])("maps execution state %s to %s", (state, label) => {
    expect(executionStateLabel(state)).toBe(label);
  });

  it("localizes known and legacy tool names without exposing arbitrary names", () => {
    expect(executionName("command")).toBe("本地命令");
    expect(executionName("mcp_tool")).toBe("MCP 工具");
    expect(executionName("google_calendar.search_events")).toBe("Google 日历查询");
    expect(executionName("gmail.search_emails")).toBe("邮件查询");
    expect(executionName("request_reviewed_action")).toBe("操作确认");
    expect(executionName("本地命令")).toBe("本地命令");
    expect(executionName("MCP 工具")).toBe("MCP 工具");
    expect(executionName("Google 日历查询")).toBe("Google 日历查询");
    expect(executionName("邮件查询")).toBe("邮件查询");
    expect(executionName("操作确认")).toBe("操作确认");
    expect(executionName("evil.provider.secret_tool")).toBe("MCP 工具");
    expect(executionName({ tool: "evil.provider.secret_tool" })).toBe("工具调用");
  });
});
