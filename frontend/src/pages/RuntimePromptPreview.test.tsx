import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RuntimePromptPreview } from "./RuntimePromptPreview";

const getPromptPreview = vi.hoisted(() => vi.fn());
vi.mock("../api/promptPreview", () => ({ getPromptPreview }));
const item = {
  mode: "current", status: "available", role: "consumer", runtime_kind: "codex_cli", route_name: "primary", model: "model-a",
  rendered_at: "2026-10-05T18:00:00Z", task_id: null, run_id: null,
  attempts: [], runtime_attempt_id: null, execution_generation: null, proposal_revision: null, stage_index: null, submission_state: "preview",
  developer_instructions: "Complete developer input\nwith profile", task_prompt: "Complete task input", submitted_input: "Complete task input",
  runtime_context: "Context: tools and local time", reason: "", scope: "unbound",
  routes: [{ name: "primary", runtime_kind: "codex_cli", model: "model-a" }, { name: "claude", runtime_kind: "claude_cli", model: "model-b" }],
};
function response(overrides = {}) { return { item: { ...item, ...overrides }, meta: { snapshot_at: item.rendered_at } }; }

describe("RuntimePromptPreview", () => {
  beforeEach(() => { vi.resetAllMocks(); getPromptPreview.mockResolvedValue(response()); });
  it("shows full returned current input with source, role, time, and unbound context", async () => {
    render(<RuntimePromptPreview />);
    expect(await screen.findByText((_text, node) => node?.tagName === "PRE" && node.textContent === item.developer_instructions)).toBeInTheDocument();
    expect(screen.getByText(item.task_prompt)).toBeInTheDocument();
    expect(screen.getByText(item.runtime_context)).toBeInTheDocument();
    expect(screen.getByText("运行环境与能力说明 (Runtime Context)")).toBeInTheDocument();
    expect(screen.getByText(/未绑定任务/)).toBeInTheDocument();
    expect(screen.getByText(/2026-10-05T18:00:00Z/)).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "角色" })).toHaveValue("consumer");
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
  });
  it("shows available source scope and reason supplied by the service", async () => {
    getPromptPreview.mockResolvedValueOnce(response({ scope: "当前配置与公共上下文", reason: "敏感字段已隐藏" }));
    render(<RuntimePromptPreview />);
    expect(await screen.findByText("当前配置与公共上下文")).toBeInTheDocument();
    expect(screen.getByText("敏感字段已隐藏")).toBeInTheDocument();
  });
  it("identifies the historical Task body in a bound current configuration preview", async () => {
    getPromptPreview.mockResolvedValueOnce(response({ task_id: 42, run_id: 9, scope: "服务输入范围", reason: "任务正文来自 run 9 保存的输入，不代表外部资料当前状态。" }));
    render(<RuntimePromptPreview />);
    expect(await screen.findByText(/当前配置与已保存历史任务正文/)).toBeInTheDocument();
    expect(screen.getByText(/Developer 按当前已保存配置组装/)).toHaveTextContent("Task 沿用已保存的历史正文");
    expect(screen.getByText(/Developer 按当前已保存配置组装/)).toHaveTextContent("不是该次运行的实际输入");
    expect(screen.getByText("任务正文来自 run 9 保存的输入，不代表外部资料当前状态。")).toBeInTheDocument();
    expect(screen.getByText("服务输入范围")).toBeInTheDocument();
    expect(screen.getByText(item.task_prompt)).toBeInTheDocument();
    expect(screen.queryByText(/未绑定任务/)).not.toBeInTheDocument();
  });
  it("describes a bound Audit Task as candidate review context rather than a User template", async () => {
    getPromptPreview.mockResolvedValueOnce(response({ role: "audit", task_id: 42 }));
    render(<RuntimePromptPreview />);
    expect(await screen.findByRole("heading", { name: "Task · 完整候选审核输入" })).toBeInTheDocument();
    expect(screen.getByText(/Developer 按当前已保存配置组装/)).toHaveTextContent("独立候选审核上下文");
    expect(screen.queryByText(/可能采用当时的 User 模板/)).not.toBeInTheDocument();
  });
  it("shows current and historical Task configuration hashes separately without replacing Task text", async () => {
    const current = { developer_instructions: "f".repeat(64), developer_template: "a".repeat(64), user_template: "b".repeat(64), work_profile_instruction: "c".repeat(64) };
    const saved = { developer_template: "d".repeat(64), user_template: "e".repeat(64), work_profile_instruction: current.work_profile_instruction };
    getPromptPreview.mockResolvedValueOnce(response({ task_id: 42, configuration_fingerprints: current,
      task_source_configuration_fingerprints: saved, task_source_run_id: 9, task_source_rendered_at: "2026-10-04T10:00:00Z" }));
    render(<RuntimePromptPreview />);
    await screen.findByText(item.task_prompt);
    fireEvent.click(screen.getByText("配置指纹与 Task 来源"));
    const table = within(screen.getByRole("table", { name: "配置 SHA 与来源" }));
    expect(table.getByRole("columnheader", { name: "当前配置" })).toBeInTheDocument();
    expect(table.getByRole("columnheader", { name: "历史 Task 来源配置" })).toBeInTheDocument();
    const developer = within(table.getByRole("row", { name: /Developer template/ }));
    expect(developer.getByText(current.developer_template)).toBeInTheDocument();
    expect(developer.getByText(saved.developer_template)).toBeInTheDocument();
    const rendered = within(table.getByText("Developer rendered principles").closest("tr")!);
    expect(rendered.getByText(current.developer_instructions)).toBeInTheDocument();
    expect(rendered.getByText("未记录")).toBeInTheDocument();
    expect(rendered.getByText("无法比较")).toBeInTheDocument();
    expect(developer.getByText("不同")).toBeInTheDocument();
    expect(table.getByRole("row", { name: /Work profile wrapper/ })).toHaveTextContent("一致");
    expect(screen.getByText(/Task 来源运行 9/)).toHaveTextContent("2026-10-04T10:00:00Z");
    expect(screen.getByText(/仅比较配置指纹/)).toBeInTheDocument();
    expect(screen.getByText(item.task_prompt)).toBeInTheDocument();
  });
  it("shows absent old fingerprint metadata as unrecorded rather than current hashes", async () => {
    getPromptPreview.mockResolvedValueOnce(response({ mode: "historical", run_id: 9, task_id: 42 }));
    render(<RuntimePromptPreview />);
    await screen.findByText(item.task_prompt);
    fireEvent.click(screen.getByText("配置指纹与 Task 来源"));
    const table = within(screen.getByRole("table", { name: "配置 SHA 与来源" }));
    expect(table.getByRole("columnheader", { name: "历史配置" })).toBeInTheDocument();
    expect(table.getAllByText("未记录")).toHaveLength(8);
    expect(table.getAllByText("无法比较")).toHaveLength(4);
  });
  it("requests selected role, route and task and shows Claude service input", async () => {
    render(<RuntimePromptPreview />);
    await screen.findByText((_text, node) => node?.tagName === "PRE" && node.textContent === item.developer_instructions);
    fireEvent.change(screen.getByLabelText("角色"), { target: { value: "audit" } });
    fireEvent.change(screen.getByLabelText("运行路线"), { target: { value: "claude" } });
    fireEvent.change(screen.getByLabelText("任务 ID（可选）"), { target: { value: "42" } });
    getPromptPreview.mockResolvedValue(response({ role: "audit", runtime_kind: "claude_cli", submitted_input: "<instructions>Wrapped actual input</instructions>", task_id: 42, scope: "task" }));
    fireEvent.click(screen.getByRole("button", { name: "查看 / 刷新" }));
    expect(await screen.findByText("<instructions>Wrapped actual input</instructions>")).toBeInTheDocument();
    expect(getPromptPreview).toHaveBeenLastCalledWith({ role: "audit", route_name: "claude", task_id: 42 }, expect.any(AbortSignal));
  });
  it("requires a run ID for history and never substitutes current input for missing history", async () => {
    render(<RuntimePromptPreview />);
    await screen.findByText((_text, node) => node?.tagName === "PRE" && node.textContent === item.developer_instructions);
    fireEvent.change(screen.getByLabelText("预览来源"), { target: { value: "historical" } });
    expect(screen.queryByText((_text, node) => node?.tagName === "PRE" && node.textContent === item.developer_instructions)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看 / 刷新" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("运行 ID"), { target: { value: "9" } });
    getPromptPreview.mockResolvedValue(response({ mode: "historical", status: "unavailable", reason: "该运行未保存输入", run_id: 9 }));
    fireEvent.click(screen.getByRole("button", { name: "查看 / 刷新" }));
    expect(await screen.findByText("该运行未保存输入")).toBeInTheDocument();
    expect(screen.queryByText(item.task_prompt)).not.toBeInTheDocument();
    expect(screen.getByLabelText("运行路线")).toBeDisabled();
    expect(getPromptPreview).toHaveBeenLastCalledWith({ role: "consumer", run_id: 9 }, expect.any(AbortSignal));
  });
  it.each(["prepared", "invoked"] as const)("labels %s historical input and shows recorded attempt, generation and revision", async (submissionState) => {
    render(<RuntimePromptPreview />);
    await screen.findByText(item.task_prompt);
    fireEvent.change(screen.getByLabelText("预览来源"), { target: { value: "historical" } });
    fireEvent.change(screen.getByLabelText("运行 ID"), { target: { value: "9" } });
    getPromptPreview.mockResolvedValue(response({ mode: "historical", run_id: 9, runtime_kind: "claude_cli", runtime_attempt_id: 72, execution_generation: "generation-17", proposal_revision: 3, stage_index: 0, submission_state: submissionState, reason: "输入状态由运行记录确认" }));
    fireEvent.click(screen.getByRole("button", { name: "查看 / 刷新" }));
    expect(await screen.findByText(submissionState === "prepared" ? /已准备的服务输入/ : /已提交的服务输入/)).toBeInTheDocument();
    expect(screen.getByText(/运行尝试 72/)).toBeInTheDocument();
    expect(screen.getByText(/执行批次 generation-17/)).toBeInTheDocument();
    expect(screen.getByText(/提案版本 3/)).toBeInTheDocument();
    expect(screen.getByText(/阶段 0/)).toBeInTheDocument();
    expect(screen.getByText("输入状态由运行记录确认")).toBeInTheDocument();
    expect(screen.queryByText(submissionState === "prepared" ? /已提交的服务输入/ : /已准备的服务输入/)).not.toBeInTheDocument();
  });
  it("lets the backend choose the current default after returning from history", async () => {
    render(<RuntimePromptPreview />);
    await screen.findByText(item.task_prompt);
    fireEvent.change(screen.getByLabelText("预览来源"), { target: { value: "historical" } });
    fireEvent.change(screen.getByLabelText("预览来源"), { target: { value: "current" } });
    expect(screen.getByLabelText("运行路线")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "查看 / 刷新" }));
    await screen.findByText(item.task_prompt);
    expect(getPromptPreview).toHaveBeenLastCalledWith({ role: "consumer" }, expect.any(AbortSignal));
  });
  it("shows an unavailable bound current task reason without incomplete inputs", async () => {
    render(<RuntimePromptPreview />);
    await screen.findByText(item.task_prompt);
    fireEvent.change(screen.getByLabelText("任务 ID（可选）"), { target: { value: "42" } });
    getPromptPreview.mockResolvedValue(response({ status: "unavailable", task_id: 42, reason: "该任务尚无完整已保存输入" }));
    fireEvent.click(screen.getByRole("button", { name: "查看 / 刷新" }));
    expect(await screen.findByText("该任务尚无完整已保存输入")).toBeInTheDocument();
    expect(screen.queryByText(/当前配置与已保存历史任务正文/)).not.toBeInTheDocument();
    expect(screen.queryByText(item.task_prompt)).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Developer Prompt · 完整指令" })).not.toBeInTheDocument();
  });
  it("lets history select an earlier invoked attempt before a later prepared attempt", async () => {
    const attempts = [
      { runtime_attempt_id: 71, route_name: "earlier", rendered_at: "2026-10-05T17:00:00Z", submission_state: "invoked" },
      { runtime_attempt_id: 72, route_name: "later", rendered_at: "2026-10-05T18:00:00Z", submission_state: "prepared" },
    ];
    render(<RuntimePromptPreview />);
    await screen.findByText(item.task_prompt);
    fireEvent.change(screen.getByLabelText("预览来源"), { target: { value: "historical" } });
    fireEvent.change(screen.getByLabelText("运行 ID"), { target: { value: "9" } });
    getPromptPreview.mockResolvedValueOnce(response({ mode: "historical", run_id: 9, attempts, runtime_attempt_id: 72, submission_state: "prepared" }));
    fireEvent.click(screen.getByRole("button", { name: "查看 / 刷新" }));
    expect(await screen.findByLabelText("运行尝试")).toHaveValue("72");
    expect(screen.getByRole("option", { name: /71.*earlier.*已提交/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /72.*later.*已准备/ })).toBeInTheDocument();
    getPromptPreview.mockResolvedValueOnce(response({ mode: "historical", run_id: 9, attempts, runtime_attempt_id: 71, submission_state: "invoked", task_prompt: "Earlier actual input" }));
    fireEvent.change(screen.getByLabelText("运行尝试"), { target: { value: "71" } });
    fireEvent.click(screen.getByRole("button", { name: "查看 / 刷新" }));
    expect(await screen.findByText("Earlier actual input")).toBeInTheDocument();
    expect(getPromptPreview).toHaveBeenLastCalledWith({ role: "consumer", run_id: 9, runtime_attempt_id: 71 }, expect.any(AbortSignal));
    fireEvent.change(screen.getByLabelText("运行 ID"), { target: { value: "10" } });
    expect(screen.queryByLabelText("运行尝试")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看 / 刷新" }));
    expect(getPromptPreview).toHaveBeenLastCalledWith({ role: "consumer", run_id: 10 }, expect.any(AbortSignal));
  });
  it("aborts outdated requests and ignores their later responses", async () => {
    let resolveOld!: (value: unknown) => void;
    getPromptPreview.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    const view = render(<RuntimePromptPreview />);
    const signal = getPromptPreview.mock.calls[0][1];
    fireEvent.change(screen.getByLabelText("角色"), { target: { value: "audit" } });
    expect(signal.aborted).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "查看 / 刷新" }));
    await screen.findByText((_text, node) => node?.tagName === "PRE" && node.textContent === item.developer_instructions);
    await act(async () => { resolveOld(response({ developer_instructions: "Obsolete input" })); });
    expect(screen.queryByText("Obsolete input")).not.toBeInTheDocument();
    view.unmount();
  });
  it("ends a stalled preview request with an error and permits refresh", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      getPromptPreview.mockImplementationOnce(() => new Promise(() => {}));
      render(<RuntimePromptPreview />);
      const signal = getPromptPreview.mock.calls[0][1];
      await act(async () => { await vi.advanceTimersByTimeAsync(30_001); });
      expect(screen.getByRole("alert")).toHaveTextContent("加载超过 30 秒没有返回");
      expect(signal.aborted).toBe(true);
      expect(screen.queryByText("正在加载完整输入…")).not.toBeInTheDocument();
    } finally { vi.useRealTimers(); }
  });
  it("shows request errors and allows refresh", async () => {
    getPromptPreview.mockRejectedValueOnce(new Error("预览加载失败"));
    render(<RuntimePromptPreview />);
    expect(await screen.findByRole("alert")).toHaveTextContent("预览加载失败");
    fireEvent.click(screen.getByRole("button", { name: "查看 / 刷新" }));
    await waitFor(() => expect(screen.getByText(item.task_prompt)).toBeInTheDocument());
  });
});

describe("prompt preview request", () => {
  it("uses the read-only endpoint with encoded route and only supplied IDs", async () => {
    const api = await vi.importActual<typeof import("../api/promptPreview")>("../api/promptPreview");
    const fetchRequest = vi.spyOn(globalThis, "fetch").mockResolvedValue({ ok: true, json: async () => response() } as Response);
    try {
      const controller = new AbortController();
      await api.getPromptPreview({ role: "audit", route_name: "special route&x", run_id: 12, runtime_attempt_id: 71 }, controller.signal);
      const [path, options] = fetchRequest.mock.calls[0];
      const url = new URL(String(path), "http://localhost");
      expect(url.pathname).toBe("/api/console/settings/prompt-preview");
      expect(Object.fromEntries(url.searchParams)).toEqual({ role: "audit", route_name: "special route&x", run_id: "12", runtime_attempt_id: "71" });
      expect(options?.signal).toBe(controller.signal);
      expect(options?.method).toBeUndefined();
      expect(options?.body).toBeUndefined();
    } finally { fetchRequest.mockRestore(); }
  });
});
