import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createScheduledTask,
  deleteScheduledTask,
  getScheduledTaskSkillPreview,
  getScheduledTaskOptions,
  listScheduledTaskRuns,
  listScheduledTasks,
  runScheduledTask,
  setScheduledTaskEnabled,
  updateScheduledTask,
} from "./scheduledTasks";

const task = {
  id: 7,
  migration_key: null,
  name: "检查钉钉消息",
  description: "增量检查 DingTalk 消息并创建后续处理任务。",
  prompt: "检查新消息 $dingtalk-chat",
  command: "",
  cron_expression: "0 * * * * *",
  timezone_name: "Asia/Shanghai",
  schedule_description: "每分钟执行 · Asia/Shanghai",
  next_run_at: "2026-09-08T12:01:00Z",
  runtime_id: "codex_oauth",
  runtime_options: { thinking: "high" },
  required_runtime_capabilities: [],
  working_directory: "/tmp/ceo-agent",
  enabled: true,
  version: 3,
  skill_refs: [{ skill_source: "operation", skill_name: "dingtalk-chat", managed_skill_id: null, managed_revision_id: null, position: 0 }],
  recent_run: null,
  created_at: "2026-09-08T12:00:00Z",
  updated_at: "2026-09-08T12:00:00Z",
  deleted_at: null,
} as const;
const validRun = {
  id: 11, event_id: "manual:11", scheduled_task_id: 7, trigger_kind: "manual",
  scheduled_for: "2026-09-08T12:00:00Z", first_scheduled_for: "2026-09-08T12:00:00Z", occurrence_count: 1,
  dispatch_status: "pending", skip_or_error_reason: "",
  execution_kind: "", execution_id: "", created_at: "2026-09-08T12:00:00Z", dispatched_at: null,
  attempts: [],
  snapshot: { task_id: 7, task_version: 3, name: task.name, description: task.description, prompt: task.prompt, command: task.command, cron_expression: task.cron_expression, timezone_name: task.timezone_name, runtime_id: task.runtime_id, runtime_options: task.runtime_options, required_runtime_capabilities: task.required_runtime_capabilities, working_directory: task.working_directory, skill_refs: task.skill_refs },
} as const;
const validOptions = {
  runtime_options: [{ route_name: "codex_oauth", runtime_kind: "codex_cli", credential_mode: "local_oauth", model: "gpt", available: true, unavailable_reason: null, supported_thinking: ["low", "medium", "high", "xhigh"], capabilities: ["local_process_execution"] }],
  managed_skill_options: [{ skill_id: 2, name: "managed", display_name: "Managed", revisions: [{ revision_id: 3, revision_number: 1, sha256: "abc", source: "settings", available: true, unavailable_reason: null }] }],
  operation_skill_options: [{ name: "operation", source: "/skills/operation/SKILL.md", content_summary: "Operation", sha256: "def", available: true, unavailable_reason: null }],
  service_command_options: [{ name: "produce-once", display_name: "读取新钉钉消息", description: "读取新的单聊和群聊 @ 消息", channel: "dingtalk", consumer_prompt_enabled: true }],
  meta: { snapshot_at: "now" },
} as const;
const commandTask = {
  ...task, id: 9, migration_key: "dingtalk-message-check-v1", name: "处理新的钉钉消息", description: "发现新的单聊或群聊 @ 消息后，由 Agent 读取最新上下文，决定回复、表态、澄清或不处理。", prompt: "使用 $dingtalk-chat 处理真实消息。", command: "produce-once",
  runtime_id: "", runtime_options: {}, working_directory: "", skill_refs: task.skill_refs,
  recent_run: { ...validRun, id: 12, scheduled_task_id: 9, dispatch_status: "dispatched", execution_kind: "service_command", execution_id: "produce-once", dispatched_at: "2026-09-08T12:00:02Z", snapshot: { ...validRun.snapshot, task_id: 9, name: "检查 DingTalk 消息", description: "增量检查 DingTalk 消息并创建后续处理任务。", prompt: "使用 $dingtalk-chat 处理真实消息。", command: "produce-once", runtime_id: "", runtime_options: {}, working_directory: "", skill_refs: task.skill_refs } },
} as const;

afterEach(() => vi.unstubAllGlobals());

describe("scheduled tasks API", () => {
  it("loads the exact referenced Skill body from its managed revision or operation document", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 23, skill_id: 2, sha256: "managed-sha", content: "# Managed Skill\n\nExact revision body" }), { headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ name: "dingtalk-chat", sha256: "operation-sha", content: "# Operation Skill\n\nCurrent operation body" }), { headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);

    await expect(getScheduledTaskSkillPreview({ skill_source: "managed", skill_name: "ceo-minutes-sync", managed_skill_id: 2, managed_revision_id: 23, position: 0 }, undefined, "managed-sha"))
      .resolves.toEqual({ name: "ceo-minutes-sync", content: "# Managed Skill\n\nExact revision body" });
    await expect(getScheduledTaskSkillPreview({ skill_source: "operation", skill_name: "dingtalk-chat", managed_skill_id: null, managed_revision_id: null, position: 0 }, undefined, "operation-sha"))
      .resolves.toEqual({ name: "dingtalk-chat", content: "# Operation Skill\n\nCurrent operation body" });

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/console/settings/managed-skill-revisions/23", expect.any(Object));
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/console/scheduled-task-operation-skills/dingtalk-chat", expect.any(Object));
  });

  it.each([
    ["managed revision identity", { id: 99, skill_id: 2, content: "wrong" }, { skill_source: "managed" as const, skill_name: "ceo-minutes-sync", managed_skill_id: 2, managed_revision_id: 23, position: 0 }],
    ["managed skill identity", { id: 23, skill_id: 99, content: "wrong" }, { skill_source: "managed" as const, skill_name: "ceo-minutes-sync", managed_skill_id: 2, managed_revision_id: 23, position: 0 }],
    ["operation name", { name: "lark-im", content: "wrong" }, { skill_source: "operation" as const, skill_name: "dingtalk-chat", managed_skill_id: null, managed_revision_id: null, position: 0 }],
    ["expected digest", { name: "dingtalk-chat", sha256: "old", content: "wrong" }, { skill_source: "operation" as const, skill_name: "dingtalk-chat", managed_skill_id: null, managed_revision_id: null, position: 0 }],
  ])("rejects a preview response with mismatched %s", async (_label, payload, ref) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json" } })));

    await expect(getScheduledTaskSkillPreview(ref, undefined, _label === "expected digest" ? "current" : undefined))
      .rejects.toThrow("invalid scheduled task Skill preview response");
  });

  it("validates list and option responses instead of trusting malformed payloads", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [task], meta: { total: 1, snapshot_at: "now" } }), { headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ runtime_options: "invalid" }), { headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);

    expect((await listScheduledTasks()).items[0].version).toBe(3);
    await expect(getScheduledTaskOptions()).rejects.toThrow("invalid scheduled task options response");
  });

  it.each([
    ["managed ref without exact ids", { ...task, skill_refs: [{ ...task.skill_refs[0], skill_source: "managed", managed_skill_id: null, managed_revision_id: null }] }],
    ["managed ref with invalid ids", { ...task, skill_refs: [{ ...task.skill_refs[0], skill_source: "managed", managed_skill_id: 0, managed_revision_id: -2 }] }],
    ["operation ref carrying managed ids", { ...task, skill_refs: [{ ...task.skill_refs[0], managed_skill_id: 2, managed_revision_id: 3 }] }],
    ["operation ref without identity", { ...task, skill_refs: [{ ...task.skill_refs[0], skill_name: "" }] }],
    ["unknown dispatch state", { ...task, recent_run: { ...validRun, dispatch_status: "completed" } }],
    ["zero task id", { ...task, id: 0 }],
    ["negative task id", { ...task, id: -1 }],
    ["fractional task id", { ...task, id: 1.5 }],
    ["zero task version", { ...task, version: 0 }],
    ["negative task version", { ...task, version: -1 }],
    ["fractional task version", { ...task, version: 1.5 }],
    ["zero run id", { ...task, recent_run: { ...validRun, id: 0 } }],
    ["fractional run id", { ...task, recent_run: { ...validRun, id: 1.5 } }],
    ["missing first scheduled instant", { ...task, recent_run: { ...validRun, first_scheduled_for: undefined } }],
    ["zero run occurrence count", { ...task, recent_run: { ...validRun, occurrence_count: 0 } }],
    ["invalid Attempt link", { ...task, recent_run: { ...validRun, attempts: [{ id: 0, status: "completed" }] } }],
    ["blank Attempt status", { ...task, recent_run: { ...validRun, attempts: [{ id: 1, status: "" }] } }],
    ["negative run task id", { ...task, recent_run: { ...validRun, scheduled_task_id: -1 } }],
    ["zero snapshot task id", { ...task, recent_run: { ...validRun, snapshot: { ...validRun.snapshot, task_id: 0 } } }],
    ["fractional snapshot task id", { ...task, recent_run: { ...validRun, snapshot: { ...validRun.snapshot, task_id: 1.5 } } }],
    ["negative snapshot version", { ...task, recent_run: { ...validRun, snapshot: { ...validRun.snapshot, task_version: -1 } } }],
    ["empty task refs", { ...task, skill_refs: [] }],
    ["command refs without prompt", { ...commandTask, prompt: "" }],
    ["command missing", { ...task, command: undefined }],
    ["command snapshot refs without prompt", { ...commandTask, recent_run: { ...commandTask.recent_run, snapshot: { ...commandTask.recent_run.snapshot, prompt: "" } } }],
    ["empty snapshot refs", { ...task, recent_run: { ...validRun, snapshot: { ...validRun.snapshot, skill_refs: [] } } }],
    ["duplicate ref positions", { ...task, skill_refs: [task.skill_refs[0], { ...task.skill_refs[0], skill_name: "lark-im", position: 0 }] }],
    ["skipped ref position", { ...task, skill_refs: [task.skill_refs[0], { ...task.skill_refs[0], skill_name: "lark-im", position: 2 }] }],
    ["out-of-order ref positions", { ...task, skill_refs: [{ ...task.skill_refs[0], position: 1 }, { ...task.skill_refs[0], skill_name: "lark-im", position: 0 }] }],
    ["fractional ref position", { ...task, skill_refs: [{ ...task.skill_refs[0], position: 0.5 }] }],
    ["negative ref position", { ...task, skill_refs: [{ ...task.skill_refs[0], position: -1 }] }],
    ["unknown task runtime option", { ...task, runtime_options: { thinking: "high", model: "other" } }],
    ["invalid task thinking", { ...task, runtime_options: { thinking: "ultra" } }],
    ["wrong task runtime option type", { ...task, runtime_options: { thinking: 3 } }],
    ["unknown snapshot runtime option", { ...task, recent_run: { ...validRun, snapshot: { ...validRun.snapshot, runtime_options: { thinking: "high", model: "other" } } } }],
    ["invalid snapshot thinking", { ...task, recent_run: { ...validRun, snapshot: { ...validRun.snapshot, runtime_options: { thinking: "ultra" } } } }],
    ["duplicate task runtime capabilities", { ...task, required_runtime_capabilities: ["local", "local"] }],
    ["unsorted task runtime capabilities", { ...task, required_runtime_capabilities: ["z", "a"] }],
    ["invalid snapshot runtime capabilities", { ...task, recent_run: { ...validRun, snapshot: { ...validRun.snapshot, required_runtime_capabilities: [""] } } }],
  ])("rejects %s", async (_label, invalidTask) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: [invalidTask], meta: { total: 1, snapshot_at: "now" } }), { headers: { "Content-Type": "application/json" } })));

    await expect(listScheduledTasks()).rejects.toThrow("invalid scheduled tasks response");
  });

  it.each([
    ["available runtime with a reason", { ...validOptions.runtime_options[0], available: true, unavailable_reason: "unexpected" }],
    ["unavailable runtime without a reason", { ...validOptions.runtime_options[0], available: false, unavailable_reason: null }],
    ["unavailable runtime with a blank reason", { ...validOptions.runtime_options[0], available: false, unavailable_reason: " " }],
    ["runtime with an invalid thinking capability", { ...validOptions.runtime_options[0], supported_thinking: ["ultra"] }],
    ["runtime with duplicate thinking capabilities", { ...validOptions.runtime_options[0], supported_thinking: ["high", "high"] }],
    ["runtime with duplicate capabilities", { ...validOptions.runtime_options[0], capabilities: ["local", "local"] }],
  ])("rejects %s", async (_label, runtimeOption) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ runtime_options: [runtimeOption], managed_skill_options: [], operation_skill_options: [], meta: { snapshot_at: "now" } }), { headers: { "Content-Type": "application/json" } })));

    await expect(getScheduledTaskOptions()).rejects.toThrow("invalid scheduled task options response");
  });

  it.each([
    ["zero managed skill id", { ...validOptions, managed_skill_options: [{ ...validOptions.managed_skill_options[0], skill_id: 0 }] }],
    ["blank managed identity", { ...validOptions, managed_skill_options: [{ ...validOptions.managed_skill_options[0], name: " " }] }],
    ["zero revision id", { ...validOptions, managed_skill_options: [{ ...validOptions.managed_skill_options[0], revisions: [{ ...validOptions.managed_skill_options[0].revisions[0], revision_id: 0 }] }] }],
    ["fractional revision number", { ...validOptions, managed_skill_options: [{ ...validOptions.managed_skill_options[0], revisions: [{ ...validOptions.managed_skill_options[0].revisions[0], revision_number: 1.5 }] }] }],
    ["blank revision identity", { ...validOptions, managed_skill_options: [{ ...validOptions.managed_skill_options[0], revisions: [{ ...validOptions.managed_skill_options[0].revisions[0], source: "" }] }] }],
    ["unavailable revision without reason", { ...validOptions, managed_skill_options: [{ ...validOptions.managed_skill_options[0], revisions: [{ ...validOptions.managed_skill_options[0].revisions[0], available: false, unavailable_reason: null }] }] }],
    ["available revision with reason", { ...validOptions, managed_skill_options: [{ ...validOptions.managed_skill_options[0], revisions: [{ ...validOptions.managed_skill_options[0].revisions[0], unavailable_reason: "unexpected" }] }] }],
    ["blank operation identity", { ...validOptions, operation_skill_options: [{ ...validOptions.operation_skill_options[0], source: " " }] }],
    ["unavailable operation without reason", { ...validOptions, operation_skill_options: [{ ...validOptions.operation_skill_options[0], available: false, unavailable_reason: null }] }],
    ["available operation with reason", { ...validOptions, operation_skill_options: [{ ...validOptions.operation_skill_options[0], unavailable_reason: "unexpected" }] }],
  ])("rejects malformed Skill option: %s", async (_label, invalidOptions) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(invalidOptions), { headers: { "Content-Type": "application/json" } })));

    await expect(getScheduledTaskOptions()).rejects.toThrow("invalid scheduled task options response");
  });

  it("uses exact versions for update, enable state, and delete mutations", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ item: task, meta: { snapshot_at: "now" } }), { headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);
    const draft = {
      name: task.name, description: task.description, prompt: task.prompt, command: task.command, cron_expression: task.cron_expression,
      timezone_name: task.timezone_name, runtime_id: task.runtime_id,
      runtime_options: task.runtime_options, working_directory: task.working_directory,
      required_runtime_capabilities: [...task.required_runtime_capabilities],
      enabled: task.enabled, skill_refs: [...task.skill_refs],
    };

    await createScheduledTask(draft);
    await updateScheduledTask(task.id, { ...draft, version: task.version });
    await setScheduledTaskEnabled(task.id, false, task.version);
    await deleteScheduledTask(task.id, task.version);
    const calls = fetch.mock.calls as unknown as Array<[RequestInfo | URL, RequestInit | undefined]>;

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/console/scheduled-tasks", expect.objectContaining({ method: "POST" }));
    expect(JSON.parse(String(calls[1][1]?.body))).toMatchObject({ version: 3 });
    expect(fetch).toHaveBeenNthCalledWith(3, "/api/console/scheduled-tasks/7/disable", expect.objectContaining({ method: "POST", body: JSON.stringify({ version: 3 }) }));
    expect(fetch).toHaveBeenNthCalledWith(4, "/api/console/scheduled-tasks/7?version=3", expect.objectContaining({ method: "DELETE" }));
  });

  it("runs manually and paginates history with the opaque cursor", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ item: validRun, meta: { snapshot_at: "now" } }), { status: 201, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ scheduled_task: task, items: [validRun], latest_attempt_run: validRun, meta: { snapshot_at: "now", page_size: 20, next_cursor: "11", has_more: true } }), { headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);

    expect((await runScheduledTask(7)).item.id).toBe(11);
    const history = await listScheduledTaskRuns(7, "25");
    expect(history.meta.next_cursor).toBe("11");
    expect(history.latest_attempt_run?.id).toBe(11);
    expect(fetch).toHaveBeenLastCalledWith("/api/console/scheduled-tasks/7/runs?cursor=25&page_size=20", expect.any(Object));
  });
});

describe("service command tasks", () => {
  it("accepts a command task with a targeted Consumer Prompt", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: [commandTask], meta: { total: 1, snapshot_at: "now" } }), { headers: { "Content-Type": "application/json" } })));

    const listed = (await listScheduledTasks()).items[0];

    expect(listed.command).toBe("produce-once");
    expect(listed.recent_run?.execution_kind).toBe("service_command");
    expect(listed.recent_run?.execution_id).toBe("produce-once");
  });

  it("accepts a WeChat command whose consumer loads no Skill and keeps its channel", async () => {
    const wechat = { name: "wechat-produce-once", display_name: "读取新微信消息", description: "读取微信消息", channel: "wechat", consumer_prompt_enabled: true };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...validOptions, service_command_options: [...validOptions.service_command_options, wechat] }), { headers: { "Content-Type": "application/json" } })));

    const catalog = (await getScheduledTaskOptions()).service_command_options;

    expect(catalog.map((option) => option.channel)).toEqual(["dingtalk", "wechat"]);
    expect(catalog[1].consumer_prompt_enabled).toBe(true);
  });

  it("accepts a meeting command whose consumer exports no instruction constant", async () => {
    const meeting = { name: "scan-meetings-once", display_name: "读取已结束会议", description: "读取已结束的会议", channel: "meeting", consumer_prompt_enabled: true };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...validOptions, service_command_options: [meeting] }), { headers: { "Content-Type": "application/json" } })));

    const catalog = (await getScheduledTaskOptions()).service_command_options;

    expect(catalog[0].consumer_prompt_enabled).toBe(true);
  });

  it("accepts an Email discovery command and preserves its channel", async () => {
    const email = { name: "email-message-check-once", display_name: "读取新邮件", description: "读取新邮件并触发分类", channel: "email", consumer_prompt_enabled: true };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...validOptions, service_command_options: [email] }), { headers: { "Content-Type": "application/json" } })));

    const catalog = (await getScheduledTaskOptions()).service_command_options;

    expect(catalog[0].channel).toBe("email");
  });

  it.each([
    ["unknown channel", { channel: "mailbox" }],
    ["missing Consumer Prompt flag", { consumer_prompt_enabled: undefined }],
    ["non-boolean Consumer Prompt flag", { consumer_prompt_enabled: "yes" }],
  ])("rejects a service command with %s", async (_label, patch) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...validOptions, service_command_options: [{ ...validOptions.service_command_options[0], ...patch }] }), { headers: { "Content-Type": "application/json" } })));

    await expect(getScheduledTaskOptions()).rejects.toThrow("invalid scheduled task options response");
  });

  it("rejects an options response without the service command catalog", async () => {
    const { service_command_options: _dropped, ...withoutCommands } = validOptions;
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(withoutCommands), { headers: { "Content-Type": "application/json" } })));

    await expect(getScheduledTaskOptions()).rejects.toThrow("invalid scheduled task options response");
  });
});
