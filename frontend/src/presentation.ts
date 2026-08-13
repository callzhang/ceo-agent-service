import type { TaskState, TurnStatus } from "./types";

const taskStateLabels: Record<TaskState, string> = {
  idle: "空闲",
  queued: "排队中",
  running: "执行中",
  waiting_confirmation: "等待确认",
  completed: "已完成",
  stopped: "已停止",
  failed: "失败",
};

const legacyToolLabels: Record<string, string> = {
  command: "本地命令",
  mcp_tool: "MCP 工具",
  "google_calendar.search_events": "Google 日历查询",
  "gmail.search_emails": "邮件查询",
  request_reviewed_action: "操作确认",
};

const approvedToolLabels: Record<string, string> = {
  "本地命令": "本地命令",
  "MCP 工具": "MCP 工具",
  "Google 日历查询": "Google 日历查询",
  "邮件查询": "邮件查询",
  "操作确认": "操作确认",
};

function isBackendTimestamp(value: string): boolean {
  if (
    value.length !== 19 ||
    value[4] !== "-" ||
    value[7] !== "-" ||
    value[10] !== " " ||
    value[13] !== ":" ||
    value[16] !== ":"
  ) {
    return false;
  }
  for (const [index, character] of Array.from(value).entries()) {
    if ([4, 7, 10, 13, 16].includes(index)) continue;
    if (character < "0" || character > "9") return false;
  }
  return true;
}

export function parseWorkbenchTimestamp(value: string): Date | null {
  if (isBackendTimestamp(value)) {
    const parts = [
      Number(value.slice(0, 4)),
      Number(value.slice(5, 7)),
      Number(value.slice(8, 10)),
      Number(value.slice(11, 13)),
      Number(value.slice(14, 16)),
      Number(value.slice(17, 19)),
    ];
    const parsed = new Date(`${value.slice(0, 10)}T${value.slice(11)}Z`);
    if (
      parsed.getUTCFullYear() !== parts[0] ||
      parsed.getUTCMonth() + 1 !== parts[1] ||
      parsed.getUTCDate() !== parts[2] ||
      parsed.getUTCHours() !== parts[3] ||
      parsed.getUTCMinutes() !== parts[4] ||
      parsed.getUTCSeconds() !== parts[5]
    ) {
      return null;
    }
    return parsed;
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function formatWorkbenchDateTime(value: string): { dateTime: string; label: string } | null {
  const parsed = parseWorkbenchTimestamp(value);
  if (!parsed) return null;
  return {
    dateTime: parsed.toISOString(),
    label: new Intl.DateTimeFormat("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(parsed),
  };
}

export function taskStateLabel(value: TaskState | TurnStatus): string {
  return Object.prototype.hasOwnProperty.call(taskStateLabels, value) ? taskStateLabels[value] : "执行中";
}

export function executionStateLabel(value: string): string {
  if (value === "completed" || value === "success") return "已完成";
  if (value === "failed" || value === "error") return "失败";
  if (value === "aborted") return "已中止";
  return "执行中";
}

export function executionName(value: unknown): string {
  if (typeof value !== "string") return "工具调用";
  const normalized = value.trim();
  if (Object.prototype.hasOwnProperty.call(legacyToolLabels, normalized)) {
    return legacyToolLabels[normalized];
  }
  if (Object.prototype.hasOwnProperty.call(approvedToolLabels, normalized)) {
    return approvedToolLabels[normalized];
  }
  return "MCP 工具";
}
