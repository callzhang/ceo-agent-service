import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import workbenchStyles from "../styles.css?raw";

const api = vi.hoisted(() => ({
  listScheduledTasks: vi.fn(), getScheduledTaskOptions: vi.fn(), createScheduledTask: vi.fn(),
  updateScheduledTask: vi.fn(), setScheduledTaskEnabled: vi.fn(), runScheduledTask: vi.fn(),
  deleteScheduledTask: vi.fn(), listScheduledTaskRuns: vi.fn(),
}));
vi.mock("../api/scheduledTasks", () => api);

import { ScheduledTasksPage } from "./ScheduledTasksPage";

const operationRef = { skill_source: "operation" as const, skill_name: "dingtalk-chat", managed_skill_id: null, managed_revision_id: null, position: 0 };
const managedRef = { skill_source: "managed" as const, skill_name: "ceo-minutes-sync", managed_skill_id: 2, managed_revision_id: 23, position: 1 };
const run = {
  id: 11, event_id: "manual:11", scheduled_task_id: 7, trigger_kind: "manual" as const,
  scheduled_for: "2026-09-08T12:00:00Z", dispatch_status: "dispatched", skip_or_error_reason: "",
  execution_kind: "reply_task", execution_id: "91", created_at: "2026-09-08T12:00:00Z", dispatched_at: "2026-09-08T12:00:01Z",
  snapshot: { task_id: 7, task_version: 3, name: "检查钉钉消息", prompt: "检查新的钉钉消息 $dingtalk-chat", cron_expression: "0 * * * * *", timezone_name: "Asia/Shanghai", runtime_id: "codex_oauth", runtime_options: { thinking: "high" as const }, working_directory: "/tmp/ceo-agent", skill_refs: [operationRef] },
};
const task = {
  id: 7, migration_key: null, name: "检查钉钉消息", prompt: "检查新的钉钉消息 $dingtalk-chat",
  cron_expression: "0 * * * * *", timezone_name: "Asia/Shanghai", schedule_description: "每分钟 · Asia/Shanghai",
  next_run_at: "2026-09-08T12:01:00Z", runtime_id: "codex_oauth", runtime_options: { thinking: "high" as const },
  working_directory: "/tmp/ceo-agent", enabled: true, version: 3, skill_refs: [operationRef], recent_run: run,
  created_at: "2026-09-08T10:00:00Z", updated_at: "2026-09-08T11:00:00Z", deleted_at: null,
};
const options = {
  runtime_options: [
    { route_name: "codex_oauth", runtime_kind: "codex_cli", credential_mode: "local_oauth", model: "gpt-5.6-sol", available: true, unavailable_reason: null },
    { route_name: "claude_cloud", runtime_kind: "claude_cloud", credential_mode: "oauth", model: "claude", available: false, unavailable_reason: "snapshot_missing" },
  ],
  managed_skill_options: [{ skill_id: 2, name: "ceo-minutes-sync", display_name: "每天听记同步", revisions: [
    { revision_id: 22, revision_number: 1, sha256: "old", source: "seed", available: false, unavailable_reason: "managed_revision_not_loaded" },
    { revision_id: 23, revision_number: 2, sha256: "current", source: "seed", available: true, unavailable_reason: null },
  ] }],
  operation_skill_options: [
    { name: "dingtalk-chat", source: "/skills/dingtalk-chat/SKILL.md", content_summary: "读取并处理钉钉消息", sha256: "chat", available: true, unavailable_reason: null },
    { name: "lark-im", source: "/skills/lark-im/SKILL.md", content_summary: "读取飞书消息", sha256: "lark", available: false, unavailable_reason: "operation_skill_name_conflict" },
  ],
  meta: { snapshot_at: "2026-09-08T12:00:00Z" },
};

function setup(items = [task]) {
  api.listScheduledTasks.mockResolvedValue({ items, meta: { total: items.length, snapshot_at: "now" } });
  api.getScheduledTaskOptions.mockResolvedValue(options);
  api.listScheduledTaskRuns.mockResolvedValue({ scheduled_task: task, items: [run], meta: { snapshot_at: "now", page_size: 20, next_cursor: "", has_more: false } });
  api.createScheduledTask.mockImplementation(async (draft) => ({ item: { ...task, ...draft, id: 8, version: 1, recent_run: null }, meta: { snapshot_at: "now" } }));
  api.updateScheduledTask.mockImplementation(async (_id, draft) => ({ item: { ...task, ...draft, version: 4 }, meta: { snapshot_at: "now" } }));
  api.setScheduledTaskEnabled.mockImplementation(async (_id, enabled) => ({ item: { ...task, enabled, version: 4 }, meta: { snapshot_at: "now" } }));
  api.runScheduledTask.mockResolvedValue({ item: { ...run, id: 12, event_id: "manual:12" }, meta: { snapshot_at: "now" } });
  api.deleteScheduledTask.mockResolvedValue({ item: { ...task, deleted_at: "now" }, meta: { snapshot_at: "now" } });
}

beforeEach(() => { vi.clearAllMocks(); setup(); });

function renderPage() { return render(<MemoryRouter><ScheduledTasksPage /></MemoryRouter>); }

describe("ScheduledTasksPage", () => {
  it("shows a compact master-detail with readable schedule and execution state", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "定时任务" })).toBeInTheDocument();
    const list = screen.getByRole("region", { name: "定时任务列表" });
    expect(within(list).getByText("检查钉钉消息")).toBeInTheDocument();
    expect(within(list).getByText("每分钟 · Asia/Shanghai")).toBeInTheDocument();
    expect(within(list).getByText(/下次.*2026/)).toBeInTheDocument();
    expect(within(list).getByText(/最近.*dispatched/)).toBeInTheDocument();
    expect(screen.getByLabelText("任务名称")).toHaveValue("检查钉钉消息");
    expect(screen.getByText("codex_oauth · gpt-5.6-sol")).toBeInTheDocument();
    expect(screen.getByText("claude_cloud · claude · 不可用：snapshot_missing")).toBeInTheDocument();
    expect(screen.getAllByText("dingtalk-chat").length).toBeGreaterThan(0);
    expect(screen.queryByRole("listbox", { name: "Skill 建议" })).not.toBeInTheDocument();
  });

  it("selects Skills through $ suggestions and submits structured refs independently from prompt parsing", async () => {
    const user = userEvent.setup();
    renderPage();
    const prompt = await screen.findByLabelText("任务描述");
    await user.clear(prompt);
    await user.type(prompt, "同步听记 $ceo");
    const suggestions = screen.getByRole("listbox", { name: "Skill 建议" });
    await user.click(within(suggestions).getByRole("option", { name: /每天听记同步.*revision 2/ }));

    expect(prompt).toHaveValue("同步听记 $ceo-minutes-sync ");
    const chip = screen.getByRole("button", { name: /移除每天听记同步.*revision 2/ });
    expect(chip).toBeInTheDocument();
    await user.clear(prompt);
    await user.type(prompt, "这个描述不再包含 Skill 名称");
    await user.click(screen.getByRole("button", { name: "保存更改" }));

    expect(api.updateScheduledTask).toHaveBeenCalledWith(7, expect.objectContaining({
      version: 3,
      prompt: "这个描述不再包含 Skill 名称",
      skill_refs: [operationRef, managedRef],
    }));
  });

  it("creates, toggles, manually runs, and deletes with explicit confirmation", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByLabelText("任务名称");
    await user.click(screen.getByRole("button", { name: "新建任务" }));
    expect(screen.getByRole("heading", { name: "新建定时任务" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("任务名称"), "飞书消息检查");
    await user.type(screen.getByLabelText("任务描述"), "检查飞书消息 $dingtalk");
    await user.click(screen.getByRole("option", { name: /dingtalk-chat/ }));
    await user.click(screen.getByRole("button", { name: "创建任务" }));
    expect(api.createScheduledTask).toHaveBeenCalledWith(expect.objectContaining({ name: "飞书消息检查", skill_refs: [operationRef] }));

    await user.click(screen.getByRole("button", { name: /检查钉钉消息运行中/ }));

    await user.click(screen.getByRole("button", { name: "暂停任务" }));
    expect(api.setScheduledTaskEnabled).toHaveBeenCalledWith(7, false, 3);
    await user.click(screen.getByRole("button", { name: "立即运行" }));
    expect(api.runScheduledTask).toHaveBeenCalledWith(7);
    await user.click(screen.getByRole("button", { name: "删除任务" }));
    expect(screen.getByText("删除后任务不会再触发，历史记录仍会保留。")).toBeInTheDocument();
    expect(api.deleteScheduledTask).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "确认删除" }));
    expect(api.deleteScheduledTask).toHaveBeenCalledWith(7, 4);
  });

  it("loads cursor history and exposes execution references", async () => {
    const user = userEvent.setup();
    api.listScheduledTaskRuns
      .mockResolvedValueOnce({ scheduled_task: task, items: [run], meta: { snapshot_at: "now", page_size: 20, next_cursor: "11", has_more: true } })
      .mockResolvedValueOnce({ scheduled_task: task, items: [{ ...run, id: 10, execution_id: "90" }], meta: { snapshot_at: "now", page_size: 20, next_cursor: "", has_more: false } });
    renderPage();
    expect(await screen.findByText("reply_task #91")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "加载更多运行记录" }));
    expect(api.listScheduledTaskRuns).toHaveBeenLastCalledWith(7, "11");
    expect(await screen.findByText("reply_task #90")).toBeInTheDocument();
  });

  it("preserves the draft and offers reload when the server reports a version conflict", async () => {
    const user = userEvent.setup();
    api.updateScheduledTask.mockRejectedValue(Object.assign(new Error("版本冲突"), { status: 409, code: "conflict" }));
    renderPage();
    const name = await screen.findByLabelText("任务名称");
    await user.clear(name); await user.type(name, "我的未保存修改");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("其他页面已更新这个任务");
    expect(name).toHaveValue("我的未保存修改");
    await user.click(screen.getByRole("button", { name: "重新加载最新版本" }));
    expect(api.listScheduledTasks).toHaveBeenCalledTimes(2);
  });

  it("renders loading, empty, and recoverable error states", async () => {
    let resolve!: (value: unknown) => void;
    api.listScheduledTasks.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    const view = renderPage();
    expect(screen.getByRole("status")).toHaveTextContent("正在加载定时任务");
    resolve({ items: [], meta: { total: 0, snapshot_at: "now" } });
    expect(await screen.findByText("还没有定时任务")).toBeInTheDocument();
    view.unmount();

    api.listScheduledTasks.mockRejectedValueOnce(new Error("数据库暂时不可用"));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("数据库暂时不可用");
    expect(screen.getByRole("button", { name: "重试加载" })).toBeInTheDocument();
  });

  it("switches to a single-column list-then-editor layout on narrow screens", () => {
    const style = document.createElement("style"); style.textContent = workbenchStyles; document.head.append(style);
    expect(workbenchStyles).toMatch(/@media\s*\(max-width:\s*760px\)[\s\S]*?\.scheduled-task-workspace\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
    expect(workbenchStyles).toMatch(/\.scheduled-task-workspace\s*\{[^}]*grid-template-columns:\s*minmax\([^)]*\)\s+minmax\(0,\s*1fr\)/);
    style.remove();
  });
});
