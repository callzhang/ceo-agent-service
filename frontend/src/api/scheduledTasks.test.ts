import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createScheduledTask,
  deleteScheduledTask,
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
  prompt: "检查新消息 $dingtalk-chat",
  command: "",
  cron_expression: "0 * * * * *",
  timezone_name: "Asia/Shanghai",
  schedule_description: "0 * * * * * · Asia/Shanghai",
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
  scheduled_for: "2026-09-08T12:00:00Z", dispatch_status: "pending", skip_or_error_reason: "",
  execution_kind: "", execution_id: "", created_at: "2026-09-08T12:00:00Z", dispatched_at: null,
  snapshot: { task_id: 7, task_version: 3, name: task.name, prompt: task.prompt, command: task.command, cron_expression: task.cron_expression, timezone_name: task.timezone_name, runtime_id: task.runtime_id, runtime_options: task.runtime_options, required_runtime_capabilities: task.required_runtime_capabilities, working_directory: task.working_directory, skill_refs: task.skill_refs },
} as const;
const validOptions = {
  runtime_options: [{ route_name: "codex_oauth", runtime_kind: "codex_cli", credential_mode: "local_oauth", model: "gpt", available: true, unavailable_reason: null, supported_thinking: ["low", "medium", "high", "xhigh"], capabilities: ["local_process_execution"] }],
  managed_skill_options: [{ skill_id: 2, name: "managed", display_name: "Managed", revisions: [{ revision_id: 3, revision_number: 1, sha256: "abc", source: "settings", available: true, unavailable_reason: null }] }],
  operation_skill_options: [{ name: "operation", source: "/skills/operation/SKILL.md", content_summary: "Operation", sha256: "def", available: true, unavailable_reason: null }],
  service_command_options: [{ name: "produce-once", display_name: "检查钉钉消息", description: "增量读取 DingTalk 未读消息" }],
  meta: { snapshot_at: "now" },
} as const;
const commandTask = {
  ...task, id: 9, migration_key: "dingtalk-message-check-v1", name: "检查 DingTalk 消息", prompt: "", command: "produce-once",
  runtime_id: "", runtime_options: {}, working_directory: "", skill_refs: [],
  recent_run: { ...validRun, id: 12, scheduled_task_id: 9, dispatch_status: "dispatched", execution_kind: "service_command", execution_id: "produce-once", dispatched_at: "2026-09-08T12:00:02Z", snapshot: { ...validRun.snapshot, task_id: 9, name: "检查 DingTalk 消息", prompt: "", command: "produce-once", runtime_id: "", runtime_options: {}, working_directory: "", skill_refs: [] } },
} as const;

afterEach(() => vi.unstubAllGlobals());

describe("scheduled tasks API", () => {
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
    ["negative run task id", { ...task, recent_run: { ...validRun, scheduled_task_id: -1 } }],
    ["zero snapshot task id", { ...task, recent_run: { ...validRun, snapshot: { ...validRun.snapshot, task_id: 0 } } }],
    ["fractional snapshot task id", { ...task, recent_run: { ...validRun, snapshot: { ...validRun.snapshot, task_id: 1.5 } } }],
    ["negative snapshot version", { ...task, recent_run: { ...validRun, snapshot: { ...validRun.snapshot, task_version: -1 } } }],
    ["empty task refs", { ...task, skill_refs: [] }],
    ["command task carrying refs", { ...commandTask, skill_refs: task.skill_refs }],
    ["command missing", { ...task, command: undefined }],
    ["command snapshot carrying refs", { ...commandTask, recent_run: { ...commandTask.recent_run, snapshot: { ...commandTask.recent_run.snapshot, skill_refs: task.skill_refs } } }],
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
      name: task.name, prompt: task.prompt, command: task.command, cron_expression: task.cron_expression,
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
      .mockResolvedValueOnce(new Response(JSON.stringify({ scheduled_task: task, items: [validRun], meta: { snapshot_at: "now", page_size: 20, next_cursor: "11", has_more: true } }), { headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);

    expect((await runScheduledTask(7)).item.id).toBe(11);
    expect((await listScheduledTaskRuns(7, "25")).meta.next_cursor).toBe("11");
    expect(fetch).toHaveBeenLastCalledWith("/api/console/scheduled-tasks/7/runs?cursor=25&page_size=20", expect.any(Object));
  });
});

describe("service command tasks", () => {
  it("accepts a command task without Runtime or Skill refs and its command execution reference", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: [commandTask], meta: { total: 1, snapshot_at: "now" } }), { headers: { "Content-Type": "application/json" } })));

    const listed = (await listScheduledTasks()).items[0];

    expect(listed.command).toBe("produce-once");
    expect(listed.recent_run?.execution_kind).toBe("service_command");
    expect(listed.recent_run?.execution_id).toBe("produce-once");
  });

  it("rejects an options response without the service command catalog", async () => {
    const { service_command_options: _dropped, ...withoutCommands } = validOptions;
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(withoutCommands), { headers: { "Content-Type": "application/json" } })));

    await expect(getScheduledTaskOptions()).rejects.toThrow("invalid scheduled task options response");
  });
});
