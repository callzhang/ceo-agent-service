import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import workbenchStyles from "../styles.css?raw";
import type { ScheduledTask, ScheduledTaskRun } from "../api/scheduledTasks";

const api = vi.hoisted(() => ({
  listScheduledTasks: vi.fn(), getScheduledTaskOptions: vi.fn(), createScheduledTask: vi.fn(),
  updateScheduledTask: vi.fn(), setScheduledTaskEnabled: vi.fn(), runScheduledTask: vi.fn(),
  deleteScheduledTask: vi.fn(), listScheduledTaskRuns: vi.fn(), getScheduledTaskSkillPreview: vi.fn(),
}));
vi.mock("../api/scheduledTasks", () => api);

import { ScheduledTasksPage } from "./ScheduledTasksPage";

const operationRef = { skill_source: "operation" as const, skill_name: "dingtalk-chat", managed_skill_id: null, managed_revision_id: null, position: 0 };
const managedRef = { skill_source: "managed" as const, skill_name: "ceo-minutes-sync", managed_skill_id: 2, managed_revision_id: 23, position: 1 };
const run: ScheduledTaskRun = {
  id: 11, event_id: "manual:11", scheduled_task_id: 7, trigger_kind: "manual" as const,
  scheduled_for: "2026-09-08T12:00:00Z", dispatch_status: "dispatched", skip_or_error_reason: "",
  execution_kind: "reply_task", execution_id: "91", created_at: "2026-09-08T12:00:00Z", dispatched_at: "2026-09-08T12:00:01Z",
  snapshot: { task_id: 7, task_version: 3, name: "检查钉钉消息", description: "增量检查 DingTalk 消息并创建后续处理任务。", prompt: "检查新的钉钉消息 $dingtalk-chat", command: "", cron_expression: "0 * * * * *", timezone_name: "Asia/Shanghai", runtime_id: "codex_oauth", runtime_options: { thinking: "high" as const }, required_runtime_capabilities: [], working_directory: "/tmp/ceo-agent", skill_refs: [operationRef] },
};
const task: ScheduledTask = {
  id: 7, migration_key: null, name: "检查钉钉消息", description: "增量检查 DingTalk 消息并创建后续处理任务。", prompt: "检查新的钉钉消息 $dingtalk-chat", command: "",
  cron_expression: "0 * * * * *", timezone_name: "Asia/Shanghai", schedule_description: "每分钟执行 · Asia/Shanghai",
  next_run_at: "2026-09-08T12:01:00Z", runtime_id: "codex_oauth", runtime_options: { thinking: "high" as const } as { thinking?: "low" | "medium" | "high" | "xhigh" },
  required_runtime_capabilities: [] as string[],
  working_directory: "/tmp/ceo-agent", enabled: true, version: 3, skill_refs: [operationRef], recent_run: run,
  created_at: "2026-09-08T10:00:00Z", updated_at: "2026-09-08T11:00:00Z", deleted_at: null,
};
type TestTask = ScheduledTask;
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
  service_command_options: [
    { name: "produce-once", display_name: "读取新钉钉消息", description: "读取新的单聊和群聊 @ 消息；发现后由 Agent 根据最新上下文决定是否回复、表态、澄清或不处理。", channel: "dingtalk" as const, consumer_prompt_enabled: true },
    { name: "wechat-produce-once", display_name: "读取新微信消息", description: "读取已启用好友和群聊 @ 的新消息；仅按已配置范围和发送模式交由 Agent 判断是否回复。", channel: "wechat" as const, consumer_prompt_enabled: true },
    { name: "scan-meetings-once", display_name: "读取已结束会议", description: "读取已结束且会议资料可用的钉钉会议；由 Agent 整理结论、分歧、行动项和必要的会后澄清。", channel: "meeting" as const, consumer_prompt_enabled: true },
    { name: "sync-minutes-once", display_name: "同步听记到工作区", description: "归档尚未归档且可访问的钉钉 AI 听记，把可用摘要和逐字稿保存到工作区；权限受限或内容不可读时保留同步状态。", channel: "work_summary" as const, consumer_prompt_enabled: false },
  ],
  meta: { snapshot_at: "2026-09-08T12:00:00Z" },
};
const commandPrompt = "使用 $ceo-minutes-sync 与 $dingtalk-chat 处理 Trigger 发现的真实消息。";
const commandRefs = [{ ...managedRef, position: 0 }, { ...operationRef, position: 1 }];
const commandRun: ScheduledTaskRun = { ...run, id: 13, scheduled_task_id: 9, execution_kind: "service_command", execution_id: "produce-once", snapshot: { ...run.snapshot, task_id: 9, name: "检查 DingTalk 消息", description: "增量检查 DingTalk 消息并创建后续处理任务。", prompt: commandPrompt, command: "produce-once", runtime_id: "", runtime_options: {}, working_directory: "", skill_refs: commandRefs } };
const commandTask: TestTask = { ...task, id: 9, migration_key: "dingtalk-message-check-v1", name: "处理新的钉钉消息", description: "发现新的单聊或群聊 @ 消息后，由 Agent 读取最新上下文，决定回复、表态、澄清或不处理。", prompt: commandPrompt, command: "produce-once", runtime_id: "", runtime_options: {}, working_directory: "", skill_refs: commandRefs, recent_run: commandRun };

function setup(items: TestTask[] = [task]) {
  api.listScheduledTasks.mockResolvedValue({ items, meta: { total: items.length, snapshot_at: "now" } });
  api.getScheduledTaskOptions.mockResolvedValue(options);
  api.listScheduledTaskRuns.mockResolvedValue({ scheduled_task: task, items: [run], meta: { snapshot_at: "now", page_size: 20, next_cursor: "", has_more: false } });
  api.createScheduledTask.mockImplementation(async (draft) => ({ item: { ...task, ...draft, id: 8, version: 1, recent_run: null }, meta: { snapshot_at: "now" } }));
  api.updateScheduledTask.mockImplementation(async (_id, draft) => ({ item: { ...task, ...draft, version: 4 }, meta: { snapshot_at: "now" } }));
  api.setScheduledTaskEnabled.mockImplementation(async (_id, enabled) => ({ item: { ...task, enabled, version: 4 }, meta: { snapshot_at: "now" } }));
  api.runScheduledTask.mockResolvedValue({ item: { ...run, id: 12, event_id: "manual:12" }, meta: { snapshot_at: "now" } });
  api.deleteScheduledTask.mockResolvedValue({ item: { ...task, deleted_at: "now" }, meta: { snapshot_at: "now" } });
  api.getScheduledTaskSkillPreview.mockResolvedValue({ name: "dingtalk-chat", content: "# 钉钉消息\n\n读取完整消息上下文。" });
}

beforeEach(() => { vi.clearAllMocks(); setup(); });

function renderPage(entry = "/scheduled-tasks") { return render(<MemoryRouter initialEntries={[entry]}><ScheduledTasksPage /></MemoryRouter>); }

async function editSelectedTask(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "编辑任务" }));
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("ScheduledTasksPage", () => {
  it("keeps a selected task in a readable view until Edit, then saves the real draft", async () => {
    const user = userEvent.setup();
    renderPage();

    expect((await screen.findAllByText("增量检查 DingTalk 消息并创建后续处理任务。")).length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("任务名称")).toBeNull();
    await user.click(screen.getByRole("button", { name: "编辑任务" }));
    const description = screen.getByLabelText("任务描述");
    await user.clear(description);
    await user.type(description, "仅处理新的钉钉消息。");
    await user.click(screen.getByRole("button", { name: "保存更改" }));

    expect(api.updateScheduledTask).toHaveBeenCalledWith(7, expect.objectContaining({
      description: "仅处理新的钉钉消息。",
      version: 3,
    }));
    expect(await screen.findByText("定时任务已保存")).toBeInTheDocument();
    expect(screen.queryByLabelText("任务描述")).toBeNull();
  });

  it("previews the exact referenced Skill body on hover and keyboard focus", async () => {
    const user = userEvent.setup();
    renderPage();
    const skill = await screen.findByRole("button", { name: /预览 dingtalk-chat Skill/ });

    await user.hover(skill);
    expect(skill).toHaveAttribute("aria-expanded", "true");
    expect(skill).toHaveAttribute("aria-controls", expect.stringContaining("scheduled-task-skill-preview"));
    expect(await screen.findByRole("region", { name: "dingtalk-chat Skill 正文" })).toHaveTextContent("读取完整消息上下文");
    expect(api.getScheduledTaskSkillPreview).toHaveBeenCalledWith(operationRef, expect.any(AbortSignal), "chat");
    await user.unhover(skill);
    expect(screen.queryByRole("region", { name: "dingtalk-chat Skill 正文" })).toBeNull();

    skill.focus();
    expect(await screen.findByRole("region", { name: "dingtalk-chat Skill 正文" })).toBeInTheDocument();
  });

  it("retries a failed Skill preview on a later hover instead of latching the transient error", async () => {
    const user = userEvent.setup();
    api.getScheduledTaskSkillPreview
      .mockRejectedValueOnce(new Error("读取暂时失败"))
      .mockResolvedValueOnce({ name: "dingtalk-chat", content: "# 钉钉消息\n\n第二次读取成功。" });
    renderPage();
    const skill = await screen.findByRole("button", { name: /预览 dingtalk-chat Skill/ });

    await user.hover(skill);
    expect(await screen.findByRole("region", { name: "dingtalk-chat Skill 正文" })).toHaveTextContent("读取暂时失败");
    await user.unhover(skill);
    await user.hover(skill);

    expect(await screen.findByRole("region", { name: "dingtalk-chat Skill 正文" })).toHaveTextContent("第二次读取成功");
    expect(api.getScheduledTaskSkillPreview).toHaveBeenCalledTimes(2);
  });

  it("does not reuse an operation Skill body after the task view is remounted", async () => {
    const user = userEvent.setup();
    api.getScheduledTaskSkillPreview
      .mockResolvedValueOnce({ name: "dingtalk-chat", content: "# 钉钉消息\n\n旧版本。" })
      .mockResolvedValueOnce({ name: "dingtalk-chat", content: "# 钉钉消息\n\n新版本。" });
    const first = renderPage();
    const firstSkill = await screen.findByRole("button", { name: /预览 dingtalk-chat Skill/ });
    await user.hover(firstSkill);
    expect(await screen.findByRole("region", { name: "dingtalk-chat Skill 正文" })).toHaveTextContent("旧版本");
    first.unmount();

    renderPage();
    const secondSkill = await screen.findByRole("button", { name: /预览 dingtalk-chat Skill/ });
    await user.hover(secondSkill);
    expect(await screen.findByRole("region", { name: "dingtalk-chat Skill 正文" })).toHaveTextContent("新版本");
    expect(api.getScheduledTaskSkillPreview).toHaveBeenCalledTimes(2);
  });

  it("uses a focusable inline Skill preview so full content is not clipped by the task workspace", async () => {
    const user = userEvent.setup();
    renderPage();
    const skill = await screen.findByRole("button", { name: /预览 dingtalk-chat Skill/ });

    await user.hover(skill);
    const preview = await screen.findByRole("region", { name: "dingtalk-chat Skill 正文" });
    expect(preview).toHaveAttribute("tabindex", "0");
    preview.focus();
    expect(preview).toHaveFocus();
  });

  it("does not offer pause, run, or delete while an unsaved edit draft is visible", async () => {
    const user = userEvent.setup();
    renderPage();
    await editSelectedTask(user);

    expect(screen.getByRole("button", { name: "取消编辑" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "暂停任务" })).toBeNull();
    expect(screen.queryByRole("button", { name: "立即运行" })).toBeNull();
    expect(screen.queryByRole("button", { name: "删除任务" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "取消编辑" }));
    expect(screen.getByRole("button", { name: "暂停任务" })).toBeInTheDocument();
  });

  it("selects the task named by the Attention deep link", async () => {
    setup([task, taskB]);
    const user = userEvent.setup();
    renderPage("/scheduled-tasks?id=8");

    await editSelectedTask(user);
    expect(await screen.findByLabelText("任务名称")).toHaveValue("检查飞书消息");
    expect(api.listScheduledTaskRuns).toHaveBeenCalledWith(8, "", expect.any(AbortSignal));
  });

  it("shows a compact master-detail with readable schedule and execution state", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "定时任务" })).toBeInTheDocument();
    const list = screen.getByRole("region", { name: "定时任务列表" });
    expect(within(list).getByText("检查钉钉消息")).toBeInTheDocument();
    expect(within(list).getByText("每分钟执行 · Asia/Shanghai")).toBeInTheDocument();
    expect(within(list).getByText(/下次.*2026/)).toBeInTheDocument();
    expect(within(list).getByText(/最近.*dispatched/)).toBeInTheDocument();
    expect(screen.getByText("检查新的钉钉消息 $dingtalk-chat")).toBeInTheDocument();
    expect(screen.getAllByText("dingtalk-chat").length).toBeGreaterThan(0);
    expect(screen.queryByRole("region", { name: "Skill 建议" })).not.toBeInTheDocument();
  });

  it("selects Skills through $ suggestions without inferring extra refs from arbitrary text", async () => {
    const user = userEvent.setup();
    renderPage();
    await editSelectedTask(user);
    const prompt = await screen.findByLabelText("Agent 执行提示词");
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
    await editSelectedTask(user);
    const prompt = await screen.findByLabelText("Agent 执行提示词");

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
    await user.click(await screen.findByRole("button", { name: "新建任务" }));
    expect(screen.getByRole("heading", { name: "新建定时任务" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("任务名称"), "飞书消息检查");
    await user.type(screen.getByLabelText("任务描述"), "检查飞书消息并创建处理任务。");
    await user.type(screen.getByLabelText("Agent 执行提示词"), "检查飞书消息 $dingtalk");
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
    await editSelectedTask(user);
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
    const user = userEvent.setup();
    renderPage();
    await editSelectedTask(user);

    expect(await screen.findByText("当前 Runtime 不可用：oauth_expired")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存更改" })).toBeDisabled();
    expect(api.updateScheduledTask).not.toHaveBeenCalled();
  });

  it("clears thinking when switching to a Runtime that does not support it", async () => {
    const user = userEvent.setup();
    renderPage();
    await editSelectedTask(user);
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
    const user = userEvent.setup();
    renderPage();
    await editSelectedTask(user);

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
    const user = userEvent.setup();
    renderPage();
    await editSelectedTask(user);

    expect(await screen.findByText(/当前 Runtime 缺少任务所需能力/)).toHaveTextContent("local_process_execution");
    expect(screen.getByRole("button", { name: "保存更改" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "立即运行" })).toBeNull();
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
    await editSelectedTask(user);
    await screen.findByLabelText("任务名称");

    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await user.click(screen.getByRole("button", { name: /检查飞书消息/ }));
    await editSelectedTask(user);
    expect(screen.getByLabelText("任务名称")).toHaveValue("检查飞书消息");
    expect(screen.getByRole("button", { name: "保存更改" })).toBeEnabled();
    await act(async () => save.resolve({ item: { ...task, name: "A 已保存" }, meta: { snapshot_at: "now" } }));
    expect(screen.getByLabelText("任务名称")).toHaveValue("检查飞书消息");
    expect(screen.queryByText("定时任务已保存")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /A 已保存/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /A 已保存/ }));
    await user.click(screen.getByRole("button", { name: "暂停任务" }));
    await user.click(screen.getByRole("button", { name: /检查飞书消息/ }));
    await editSelectedTask(user);
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

    expect(screen.queryByLabelText("任务名称")).toBeNull();
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

    await editSelectedTask(user);
    const prompt = screen.getByLabelText("Agent 执行提示词");
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
  it("edits the Consumer Prompt and execution command without showing Settings-owned runtime details", async () => {
    setup([commandTask]);
    const user = userEvent.setup();
    renderPage("/scheduled-tasks?id=9");
    await editSelectedTask(user);

    expect(await screen.findByLabelText("Consumer Agent Prompt")).toHaveValue(commandPrompt);
    expect(screen.getByText("每天听记同步 · revision 2")).toBeInTheDocument();
    expect(screen.getByText("dingtalk-chat")).toBeInTheDocument();
    expect(screen.queryByText("lark-im")).toBeNull();
    expect(screen.queryByRole("region", { name: "下游 consumer" })).toBeNull();
    expect(screen.queryByText("Runtime 路由")).toBeNull();
    expect(screen.queryByText(/角色边界/)).toBeNull();
    expect(screen.getByLabelText("服务命令")).toBeEnabled();

    await user.selectOptions(screen.getByLabelText("服务命令"), "wechat-produce-once");
    await user.click(screen.getByRole("button", { name: "保存更改" }));

    await waitFor(() => expect(api.updateScheduledTask).toHaveBeenCalledWith(
      9,
      expect.objectContaining({
        command: "wechat-produce-once",
        prompt: commandPrompt,
        skill_refs: commandRefs,
        runtime_id: "",
        version: 3,
      }),
    ));
  });

  it("shows a readable service task without Settings-owned runtime details", async () => {
    setup([commandTask]);
    api.listScheduledTaskRuns.mockResolvedValue({ scheduled_task: commandTask, items: [commandRun], meta: { snapshot_at: "now", page_size: 20, next_cursor: "", has_more: false } });
    api.updateScheduledTask.mockImplementation(async (_id, draft) => ({ item: { ...commandTask, ...draft, version: 4 }, meta: { snapshot_at: "now" } }));
    const user = userEvent.setup();
    renderPage("/scheduled-tasks?id=9");
    await editSelectedTask(user);

    expect(await screen.findByLabelText("服务命令")).toHaveValue("produce-once");
    expect(screen.getByLabelText("服务命令")).toHaveValue("produce-once");
    expect(screen.getByText("每分钟执行 · Asia/Shanghai")).toBeInTheDocument();
    expect(screen.getByText("计划预览：每分钟执行 · Asia/Shanghai")).toBeInTheDocument();
    expect(screen.getByText("读取新的单聊和群聊 @ 消息；发现后由 Agent 根据最新上下文决定是否回复、表态、澄清或不处理。")).toBeInTheDocument();
    expect(screen.queryByLabelText("Runtime")).toBeNull();
    expect(screen.getByLabelText("任务描述")).toHaveValue("发现新的单聊或群聊 @ 消息后，由 Agent 读取最新上下文，决定回复、表态、澄清或不处理。");
    expect(screen.queryByText("Agent Skills")).toBeNull();
    expect(screen.queryByLabelText("Consumer Agent Runtime")).toBeNull();
    expect(screen.queryByLabelText("Consumer Agent 自定义描述")).toBeNull();
    expect(screen.queryByText("Consumer Agent 系统提示词")).toBeNull();
    expect(screen.queryByText("从提示词提取的 Skills")).toBeNull();
    expect(screen.queryByRole("region", { name: "下游 consumer" })).toBeNull();
    expect(screen.queryByText(/角色边界/)).toBeNull();
    expect(screen.queryByText("Runtime 路由")).toBeNull();
    expect(screen.getByLabelText("服务命令")).toBeEnabled();
    expect(screen.getAllByText("处理新的钉钉消息").length).toBeGreaterThanOrEqual(1);
    expect(await screen.findByText("技术详情")).toBeInTheDocument();
    expect(screen.queryByText("技术详情：produce-once")).toBeNull();
    expect(screen.queryByText("service_command #produce-once")).toBeNull();
    expect(screen.queryByRole("button", { name: "暂停任务" })).toBeNull();
    expect(screen.queryByRole("button", { name: "立即运行" })).toBeNull();

    await user.clear(screen.getByLabelText("Cron 表达式"));
    await user.type(screen.getByLabelText("Cron 表达式"), "0 30 * * * *");
    expect(screen.getByText("计划预览：每小时第30分钟执行 · Asia/Shanghai")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "保存更改" }));

    await waitFor(() => expect(api.updateScheduledTask).toHaveBeenCalledWith(9, expect.objectContaining({ command: "produce-once", cron_expression: "0 30 * * * *", prompt: commandPrompt, runtime_id: "", skill_refs: commandRefs, version: 3 })));
  });

  it("hides Consumer Prompt for a deterministic command with no Agent consumer", async () => {
    setup([{ ...commandTask, id: 10, migration_key: null, name: "同步 AI 听记", command: "sync-minutes-once", prompt: "", skill_refs: [], recent_run: null }]);
    const user = userEvent.setup();
    renderPage("/scheduled-tasks?id=10");
    await editSelectedTask(user);

    expect(await screen.findByLabelText("服务命令")).toHaveValue("sync-minutes-once");
    expect(screen.queryByLabelText("Consumer Agent Prompt")).toBeNull();
    expect(screen.queryByText("Consumer Agent Skills")).toBeNull();
  });

  it("describes a fixed second schedule without treating it as a custom Cron", async () => {
    setup([commandTask]);
    const user = userEvent.setup();
    renderPage("/scheduled-tasks?id=9");
    await editSelectedTask(user);

    const expression = await screen.findByLabelText("Cron 表达式");
    await user.clear(expression);
    await user.type(expression, "10 * * * * *");
    expect(screen.getByText("计划预览：每分钟第10秒执行 · Asia/Shanghai")).toBeInTheDocument();

    await user.clear(expression);
    await user.type(expression, "0 * * * * *");
    expect(screen.getByText("计划预览：每分钟执行 · Asia/Shanghai")).toBeInTheDocument();
  });

  it("keeps the execution type editable for a user-created command task", async () => {
    setup([{ ...commandTask, migration_key: null }]);
    api.listScheduledTaskRuns.mockResolvedValue({ scheduled_task: commandTask, items: [commandRun], meta: { snapshot_at: "now", page_size: 20, next_cursor: "", has_more: false } });
    const user = userEvent.setup();
    renderPage("/scheduled-tasks?id=9");
    await editSelectedTask(user);

    expect(await screen.findByLabelText("服务命令")).toHaveValue("produce-once");
    expect(screen.getByLabelText("服务命令")).toBeEnabled();
    expect(screen.queryByText("内置任务的执行类型由仓库维护，不能修改。")).toBeNull();
  });

  it("shows the real task description for an Agent task without a fabricated system prompt", async () => {
    const user = userEvent.setup();
    renderPage();
    await editSelectedTask(user);

    expect(await screen.findByLabelText("任务描述")).toHaveValue("增量检查 DingTalk 消息并创建后续处理任务。");
    expect(screen.getByText("任务描述")).toBeInTheDocument();
    expect(screen.getByLabelText("Agent 执行提示词")).toHaveValue("检查新的钉钉消息 $dingtalk-chat");
    expect(screen.getByLabelText("Agent 执行提示词")).toHaveAttribute("placeholder", expect.stringContaining("$"));
    expect(screen.getByLabelText("Runtime")).toBeInTheDocument();
    expect(screen.getByText("Agent Skills")).toBeInTheDocument();
    expect(screen.getByLabelText("服务命令")).toBeEnabled();
    expect(screen.queryByText("Consumer Agent 系统提示词")).toBeNull();
    expect(screen.queryByLabelText("Consumer Agent 自定义描述")).toBeNull();
    expect(screen.queryByLabelText("Consumer Agent Runtime")).toBeNull();
    expect(document.querySelector(".scheduled-task-system-prompt")).toBeNull();

    await user.click(screen.getByRole("button", { name: "新建任务" }));
    expect(screen.getByLabelText("服务命令")).toBeEnabled();
    expect(screen.queryByText("内置任务的执行类型由仓库维护，不能修改。")).toBeNull();
  });

  it("keeps the execution type editable for a repository managed Agent task", async () => {
    setup([{ ...task, migration_key: "dingtalk-agent-check-v1" }]);
    const user = userEvent.setup();
    renderPage();
    await editSelectedTask(user);

    expect(await screen.findByLabelText("任务描述")).toBeInTheDocument();
    expect(screen.getByLabelText("服务命令")).toBeEnabled();
    expect(screen.queryByText("内置任务的执行类型由仓库维护，不能修改。")).toBeNull();
  });
});
