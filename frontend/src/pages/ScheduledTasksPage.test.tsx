import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  snapshot: { task_id: 7, task_version: 3, name: "检查钉钉消息", prompt: "检查新的钉钉消息 $dingtalk-chat", command: "", cron_expression: "0 * * * * *", timezone_name: "Asia/Shanghai", runtime_id: "codex_oauth", runtime_options: { thinking: "high" as const }, required_runtime_capabilities: [], working_directory: "/tmp/ceo-agent", skill_refs: [operationRef] },
};
const task = {
  id: 7, migration_key: null, name: "检查钉钉消息", prompt: "检查新的钉钉消息 $dingtalk-chat", command: "",
  cron_expression: "0 * * * * *", timezone_name: "Asia/Shanghai", schedule_description: "每分钟 · Asia/Shanghai",
  next_run_at: "2026-09-08T12:01:00Z", runtime_id: "codex_oauth", runtime_options: { thinking: "high" as const } as { thinking?: "low" | "medium" | "high" | "xhigh" },
  required_runtime_capabilities: [] as string[],
  working_directory: "/tmp/ceo-agent", enabled: true, version: 3, skill_refs: [operationRef], recent_run: run,
  created_at: "2026-09-08T10:00:00Z", updated_at: "2026-09-08T11:00:00Z", deleted_at: null,
};
type TestTask = Omit<typeof task, "recent_run" | "deleted_at" | "migration_key"> & { recent_run: typeof run | null; deleted_at: string | null; migration_key: string | null };
const taskB: TestTask = { ...task, id: 8, name: "检查飞书消息", prompt: "检查飞书消息 $dingtalk-chat", version: 5, recent_run: null };
const options = {
  runtime_options: [
    { route_name: "codex_oauth", runtime_kind: "codex_cli", credential_mode: "local_oauth", model: "gpt-5.6-sol", available: true, unavailable_reason: null, supported_thinking: ["low", "medium", "high", "xhigh"], capabilities: ["local_process_execution", "local_service_database_access", "local_workspace_access"] },
    { route_name: "claude_cloud", runtime_kind: "claude_cli", credential_mode: "oauth", model: "claude", available: false, unavailable_reason: "snapshot_missing", supported_thinking: [], capabilities: [] },
    { route_name: "friday_runtime", runtime_kind: "friday_runtime", credential_mode: "service_api", model: "default", available: true, unavailable_reason: null, supported_thinking: [], capabilities: [] },
  ],
  managed_skill_options: [{ skill_id: 2, name: "ceo-minutes-sync", display_name: "每天听记同步", revisions: [
    { revision_id: 22, revision_number: 1, sha256: "old", source: "seed", available: false, unavailable_reason: "managed_revision_not_loaded" },
    { revision_id: 23, revision_number: 2, sha256: "current", source: "seed", available: true, unavailable_reason: null },
  ] }],
  operation_skill_options: [
    { name: "dingtalk-chat", source: "/skills/dingtalk-chat/SKILL.md", content_summary: "读取并处理钉钉消息", sha256: "chat", available: true, unavailable_reason: null },
    { name: "lark-im", source: "/skills/lark-im/SKILL.md", content_summary: "读取飞书消息", sha256: "lark", available: false, unavailable_reason: "operation_skill_name_conflict" },
  ],
  service_command_options: [{ name: "produce-once", description: "增量读取 DingTalk 未读消息，去重后写入 reply task。" }],
  meta: { snapshot_at: "2026-09-08T12:00:00Z" },
};
const commandRun: typeof run = { ...run, id: 13, scheduled_task_id: 9, execution_kind: "service_command", execution_id: "produce-once", snapshot: { ...run.snapshot, task_id: 9, name: "检查 DingTalk 消息", prompt: "", command: "produce-once", runtime_id: "", runtime_options: {} as typeof run.snapshot.runtime_options, working_directory: "", skill_refs: [] } };
const commandTask: TestTask = { ...task, id: 9, migration_key: "dingtalk-message-check-v1", name: "检查 DingTalk 消息", prompt: "", command: "produce-once", runtime_id: "", runtime_options: {}, working_directory: "", skill_refs: [], recent_run: commandRun };

function setup(items: TestTask[] = [task]) {
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

function renderPage(entry = "/scheduled-tasks") { return render(<MemoryRouter initialEntries={[entry]}><ScheduledTasksPage /></MemoryRouter>); }

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("ScheduledTasksPage", () => {
  it("selects the task named by the Attention deep link", async () => {
    setup([task, taskB]);
    renderPage("/scheduled-tasks?id=8");

    expect(await screen.findByLabelText("任务名称")).toHaveValue("检查飞书消息");
    expect(api.listScheduledTaskRuns).toHaveBeenCalledWith(8, "", expect.any(AbortSignal));
  });

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
    expect(screen.queryByRole("region", { name: "Skill 建议" })).not.toBeInTheDocument();
  });

  it("selects Skills through $ suggestions without inferring extra refs from arbitrary text", async () => {
    const user = userEvent.setup();
    renderPage();
    const prompt = await screen.findByLabelText("任务描述");
    await user.clear(prompt);
    await user.type(prompt, "同步听记 $ceo");
    const suggestions = screen.getByRole("region", { name: "Skill 建议" });
    await user.click(within(suggestions).getByRole("button", { name: /每天听记同步.*revision 2/ }));

    expect(prompt).toHaveValue("同步听记 $ceo-minutes-sync ");
    const chip = screen.getByRole("button", { name: /移除每天听记同步.*revision 2/ });
    expect(chip).toBeInTheDocument();
    fireEvent.change(prompt, { target: { value: "这个描述保留 $ceo-minutes-sync 但不会从 $dingtalk-chat 推断执行引用" } });
    await user.click(screen.getByRole("button", { name: "保存更改" }));

    expect(api.updateScheduledTask).toHaveBeenCalledWith(7, expect.objectContaining({
      version: 3,
      prompt: "这个描述保留 $ceo-minutes-sync 但不会从 $dingtalk-chat 推断执行引用",
      skill_refs: [{ ...managedRef, position: 0 }],
    }));
  });

  it("keeps explicit $ tokens and structured refs synchronized in both edit directions", async () => {
    const user = userEvent.setup();
    renderPage();
    const prompt = await screen.findByLabelText("任务描述");

    fireEvent.change(prompt, { target: { value: "只保留普通描述" } });
    expect(screen.queryByRole("button", { name: "移除dingtalk-chat" })).not.toBeInTheDocument();

    await user.type(prompt, " $ceo");
    await user.click(screen.getByRole("button", { name: /每天听记同步.*revision 2/ }));
    expect(screen.getByRole("button", { name: /移除每天听记同步.*revision 2/ })).toBeInTheDocument();
    fireEvent.change(prompt, { target: { value: "只保留普通描述" } });
    expect(screen.queryByRole("button", { name: /移除每天听记同步.*revision 2/ })).not.toBeInTheDocument();

    await user.type(prompt, " $dingtalk");
    await user.click(screen.getByRole("button", { name: /dingtalk-chat/ }));
    expect(prompt).toHaveValue("只保留普通描述 $dingtalk-chat ");
    await user.click(screen.getByRole("button", { name: "移除dingtalk-chat" }));
    expect(prompt).toHaveValue("只保留普通描述  ");
    expect(prompt).not.toHaveValue(expect.stringContaining("$dingtalk-chat"));
  });

  it("creates, toggles, manually runs, and deletes with explicit confirmation", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByLabelText("任务名称");
    await user.click(screen.getByRole("button", { name: "新建任务" }));
    expect(screen.getByRole("heading", { name: "新建定时任务" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("任务名称"), "飞书消息检查");
    await user.type(screen.getByLabelText("任务描述"), "检查飞书消息 $dingtalk");
    await user.click(screen.getByRole("button", { name: /dingtalk-chat/ }));
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

  it("does not select or submit an unavailable Runtime for a new task", async () => {
    const unavailableOptions = {
      ...options,
      runtime_options: options.runtime_options.map((runtime) => ({
        ...runtime,
        available: false,
        unavailable_reason: runtime.unavailable_reason || "probe_unavailable",
      })),
    };
    setup([]);
    api.getScheduledTaskOptions.mockResolvedValueOnce(unavailableOptions);
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "创建第一个任务" }));

    expect(screen.getByLabelText("Runtime")).toHaveValue("");
    expect(screen.getByText("当前没有可用的 Runtime，暂时无法创建任务。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建任务" })).toBeDisabled();
    expect(document.body.textContent).not.toContain("不可用：null");
  });

  it("blocks saving when the exact Runtime of an existing draft becomes unavailable", async () => {
    api.getScheduledTaskOptions.mockResolvedValueOnce({
      ...options,
      runtime_options: options.runtime_options.map((runtime) => runtime.route_name === task.runtime_id
        ? { ...runtime, available: false, unavailable_reason: "oauth_expired" }
        : runtime),
    });
    renderPage();

    expect(await screen.findByText("当前 Runtime 不可用：oauth_expired")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存更改" })).toBeDisabled();
    expect(api.updateScheduledTask).not.toHaveBeenCalled();
  });

  it("clears thinking when switching to a Runtime that does not support it", async () => {
    const user = userEvent.setup();
    renderPage();
    const runtime = await screen.findByLabelText("Runtime");
    expect(screen.getByLabelText("Reasoning")).toHaveValue("high");

    await user.selectOptions(runtime, "friday_runtime");
    expect(screen.queryByLabelText("Reasoning")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "保存更改" }));

    expect(api.updateScheduledTask).toHaveBeenCalledWith(7, expect.objectContaining({
      runtime_id: "friday_runtime",
      runtime_options: {},
    }));
  });

  it("keeps Friday unavailable for a task that requires the local service surface", async () => {
    setup([{ ...task, required_runtime_capabilities: ["local_process_execution", "local_service_database_access", "local_workspace_access"] }]);
    renderPage();

    const runtime = await screen.findByLabelText("Runtime");
    expect(within(runtime).getByRole("option", { name: /friday_runtime.*缺少能力/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "保存更改" })).not.toBeDisabled();
  });

  it("blocks an existing local-service draft whose saved Runtime is Friday", async () => {
    setup([{
      ...task,
      runtime_id: "friday_runtime",
      runtime_options: {},
      required_runtime_capabilities: ["local_process_execution", "local_service_database_access", "local_workspace_access"],
    }]);
    renderPage();

    expect(await screen.findByText(/当前 Runtime 缺少任务所需能力/)).toHaveTextContent("local_process_execution");
    expect(screen.getByRole("button", { name: "保存更改" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "立即运行" })).toBeDisabled();
  });

  it("defaults a new draft from the first available Runtime capability", async () => {
    setup([]);
    api.getScheduledTaskOptions.mockResolvedValueOnce({
      ...options,
      runtime_options: [options.runtime_options[2]],
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "创建第一个任务" }));

    expect(screen.getByLabelText("Runtime")).toHaveValue("friday_runtime");
    expect(screen.queryByLabelText("Reasoning")).not.toBeInTheDocument();
  });

  it("keeps a newer task context clean when save and toggle results for task A arrive late", async () => {
    const user = userEvent.setup();
    const save = deferred<{ item: TestTask; meta: { snapshot_at: string } }>();
    const toggle = deferred<{ item: TestTask; meta: { snapshot_at: string } }>();
    setup([task, taskB]);
    api.updateScheduledTask.mockReturnValueOnce(save.promise);
    api.setScheduledTaskEnabled.mockReturnValueOnce(toggle.promise);
    renderPage();
    await screen.findByLabelText("任务名称");

    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await user.click(screen.getByRole("button", { name: /检查飞书消息/ }));
    expect(screen.getByLabelText("任务名称")).toHaveValue("检查飞书消息");
    expect(screen.getByRole("button", { name: "保存更改" })).toBeEnabled();
    await act(async () => save.resolve({ item: { ...task, name: "A 已保存" }, meta: { snapshot_at: "now" } }));
    expect(screen.getByLabelText("任务名称")).toHaveValue("检查飞书消息");
    expect(screen.queryByText("定时任务已保存")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /A 已保存/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /A 已保存/ }));
    await user.click(screen.getByRole("button", { name: "暂停任务" }));
    await user.click(screen.getByRole("button", { name: /检查飞书消息/ }));
    await act(async () => toggle.resolve({ item: { ...task, name: "A 已保存", enabled: false, version: 4 }, meta: { snapshot_at: "now" } }));
    expect(screen.getByLabelText("任务名称")).toHaveValue("检查飞书消息");
    expect(screen.getByRole("button", { name: /A 已保存已暂停/ })).toBeInTheDocument();
  });

  it("does not let late delete or history results replace task B state", async () => {
    const user = userEvent.setup();
    const deletion = deferred<{ item: TestTask; meta: { snapshot_at: string } }>();
    const more = deferred<{ scheduled_task: TestTask; items: (typeof run)[]; meta: { snapshot_at: string; page_size: number; next_cursor: string; has_more: boolean } }>();
    setup([task, taskB]);
    api.deleteScheduledTask.mockReturnValueOnce(deletion.promise);
    api.listScheduledTaskRuns
      .mockResolvedValueOnce({ scheduled_task: task, items: [run], meta: { snapshot_at: "now", page_size: 20, next_cursor: "11", has_more: true } })
      .mockReturnValueOnce(more.promise)
      .mockResolvedValueOnce({ scheduled_task: taskB, items: [], meta: { snapshot_at: "now", page_size: 20, next_cursor: "", has_more: false } });
    renderPage();
    await screen.findByText("reply_task #91");
    await user.click(screen.getByRole("button", { name: "加载更多运行记录" }));
    await user.click(screen.getByRole("button", { name: "删除任务" }));
    await user.click(screen.getByRole("button", { name: "确认删除" }));
    await user.click(screen.getByRole("button", { name: /检查飞书消息/ }));
    await act(async () => more.resolve({ scheduled_task: task, items: [{ ...run, id: 10, execution_id: "stale-90" }], meta: { snapshot_at: "now", page_size: 20, next_cursor: "10", has_more: true } }));
    await act(async () => deletion.resolve({ item: { ...task, deleted_at: "now" }, meta: { snapshot_at: "now" } }));

    expect(screen.getByLabelText("任务名称")).toHaveValue("检查飞书消息");
    expect(screen.queryByText("reply_task #stale-90")).not.toBeInTheDocument();
    expect(screen.queryByText("定时任务已删除，历史记录仍保留")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /检查钉钉消息/ })).not.toBeInTheDocument();
  });

  it("provides a focus-contained delete dialog and keyboard-usable button suggestions", async () => {
    const user = userEvent.setup();
    renderPage();
    const deleteTrigger = await screen.findByRole("button", { name: "删除任务" });
    await user.click(deleteTrigger);
    const dialog = screen.getByRole("alertdialog", { name: "确认删除定时任务" });
    expect(dialog).toHaveAttribute("aria-describedby", "scheduled-task-delete-description");
    const cancel = within(dialog).getByRole("button", { name: "取消" });
    const confirm = within(dialog).getByRole("button", { name: "确认删除" });
    await waitFor(() => expect(cancel).toHaveFocus());
    await user.tab();
    expect(confirm).toHaveFocus();
    await user.tab({ shift: true });
    expect(cancel).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(dialog).not.toBeInTheDocument();
    expect(deleteTrigger).toHaveFocus();

    const prompt = screen.getByLabelText("任务描述");
    await user.type(prompt, " $ceo");
    const suggestions = screen.getByRole("region", { name: "Skill 建议" });
    const suggestion = within(suggestions).getByRole("button", { name: /每天听记同步.*revision 2/ });
    suggestion.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("button", { name: /移除每天听记同步.*revision 2/ })).toBeInTheDocument();
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

  it("keeps controls, Skill suggestions, and history readable around 390px", () => {
    expect(workbenchStyles).toMatch(/@media\s*\(max-width:\s*760px\)[\s\S]*?\.scheduled-tasks-page\s*\{[^}]*max-width:\s*100vw;[^}]*padding:\s*16px\s+12px\s+28px;[^}]*\}/);
    expect(workbenchStyles).toMatch(/@media\s*\(max-width:\s*760px\)[\s\S]*?\.scheduled-tasks-page\s+\.console-page-header\s+\.muted\s*\{[^}]*overflow-wrap:\s*anywhere;[^}]*\}/);
    expect(workbenchStyles).toMatch(/@media\s*\(max-width:\s*(?:390|400|420)px\)[\s\S]*?\.scheduled-task-actions\s*\{[^}]*flex-wrap:\s*wrap;[^}]*\}/);
    expect(workbenchStyles).toMatch(/@media\s*\(max-width:\s*(?:390|400|420)px\)[\s\S]*?\.scheduled-task-suggestions\s+button\s*\{[^}]*min-width:\s*0;[^}]*overflow-wrap:\s*anywhere;[^}]*\}/);
    expect(workbenchStyles).toMatch(/@media\s*\(max-width:\s*(?:390|400|420)px\)[\s\S]*?\.scheduled-task-history\s+li\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);[^}]*\}/);
  });
});

describe("service command tasks", () => {
  it("edits only name, Cron, and timezone for a service command task and shows its command execution", async () => {
    setup([commandTask]);
    api.listScheduledTaskRuns.mockResolvedValue({ scheduled_task: commandTask, items: [commandRun], meta: { snapshot_at: "now", page_size: 20, next_cursor: "", has_more: false } });
    api.updateScheduledTask.mockImplementation(async (_id, draft) => ({ item: { ...commandTask, ...draft, version: 4 }, meta: { snapshot_at: "now" } }));
    const user = userEvent.setup();
    renderPage("/scheduled-tasks?id=9");

    expect(await screen.findByLabelText("服务命令")).toHaveValue("produce-once");
    expect(screen.getByLabelText("服务命令")).toHaveAttribute("readonly");
    expect(screen.getByText("增量读取 DingTalk 未读消息，去重后写入 reply task。")).toBeInTheDocument();
    expect(screen.queryByLabelText("Runtime")).toBeNull();
    expect(screen.queryByLabelText("任务描述")).toBeNull();
    expect(screen.queryByText("Agent Skills")).toBeNull();
    expect(screen.getByText("service_command #produce-once")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "暂停任务" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "立即运行" })).toBeEnabled();

    await user.clear(screen.getByLabelText("Cron 表达式"));
    await user.type(screen.getByLabelText("Cron 表达式"), "0 */2 * * * *");
    await user.click(screen.getByRole("button", { name: "保存更改" }));

    await waitFor(() => expect(api.updateScheduledTask).toHaveBeenCalledWith(9, expect.objectContaining({ command: "produce-once", cron_expression: "0 */2 * * * *", prompt: "", runtime_id: "", skill_refs: [], version: 3 })));
  });
});
