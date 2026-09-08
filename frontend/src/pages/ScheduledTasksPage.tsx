import { useEffect, useMemo, useState } from "react";

import {
  createScheduledTask,
  deleteScheduledTask,
  getScheduledTaskOptions,
  listScheduledTaskRuns,
  listScheduledTasks,
  runScheduledTask,
  setScheduledTaskEnabled,
  updateScheduledTask,
  type ManagedSkillOption,
  type ManagedScheduledTaskSkillRef,
  type OperationSkillOption,
  type OperationScheduledTaskSkillRef,
  type ScheduledTask,
  type ScheduledTaskDraft,
  type ScheduledTaskOptions,
  type ScheduledTaskRun,
  type ScheduledTaskSkillRef,
} from "../api/scheduledTasks";

type LoadState = "loading" | "ready" | "error";
type MutationState = "idle" | "saving";

interface SkillChoice {
  key: string;
  name: string;
  label: string;
  description: string;
  available: boolean;
  unavailableReason: string | null;
  ref: Omit<ManagedScheduledTaskSkillRef, "position"> | Omit<OperationScheduledTaskSkillRef, "position">;
}

function emptyDraft(options: ScheduledTaskOptions | null): ScheduledTaskDraft {
  return {
    name: "",
    prompt: "",
    cron_expression: "0 0 9 * * *",
    timezone_name: "Asia/Shanghai",
    runtime_id: options?.runtime_options.find((item) => item.available)?.route_name || options?.runtime_options[0]?.route_name || "",
    runtime_options: { thinking: "high" },
    working_directory: "",
    enabled: true,
    skill_refs: [],
  };
}

function taskDraft(task: ScheduledTask): ScheduledTaskDraft {
  return {
    name: task.name,
    prompt: task.prompt,
    cron_expression: task.cron_expression,
    timezone_name: task.timezone_name,
    runtime_id: task.runtime_id,
    runtime_options: task.runtime_options,
    working_directory: task.working_directory,
    enabled: task.enabled,
    skill_refs: task.skill_refs,
  };
}

function timeLabel(value: string | null) {
  if (!value) return "无";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function errorMessage(reason: unknown, fallback: string) {
  return reason instanceof Error && reason.message ? reason.message : fallback;
}

function isConflict(reason: unknown) {
  if (typeof reason !== "object" || reason === null) return false;
  const value = reason as { status?: unknown; code?: unknown };
  return value.status === 409 || value.code === "conflict";
}

function skillChoices(options: ScheduledTaskOptions | null): SkillChoice[] {
  if (!options) return [];
  const managed = options.managed_skill_options.flatMap((skill: ManagedSkillOption) => skill.revisions.map((revision) => ({
    key: `managed:${skill.skill_id}:${revision.revision_id}`,
    name: skill.name,
    label: `${skill.display_name} · revision ${revision.revision_number}`,
    description: `${skill.name} · ${revision.source}`,
    available: revision.available,
    unavailableReason: revision.unavailable_reason,
    ref: { skill_source: "managed" as const, skill_name: skill.name, managed_skill_id: skill.skill_id, managed_revision_id: revision.revision_id },
  })));
  const operation = options.operation_skill_options.map((skill: OperationSkillOption) => ({
    key: `operation:${skill.name}`,
    name: skill.name,
    label: skill.name,
    description: skill.content_summary,
    available: skill.available,
    unavailableReason: skill.unavailable_reason,
    ref: { skill_source: "operation" as const, skill_name: skill.name, managed_skill_id: null, managed_revision_id: null },
  }));
  return [...managed, ...operation];
}

function refKey(ref: Omit<ScheduledTaskSkillRef, "position"> | ScheduledTaskSkillRef) {
  return ref.skill_source === "managed"
    ? `managed:${ref.managed_skill_id}:${ref.managed_revision_id}`
    : `operation:${ref.skill_name}`;
}

function positionedRef(ref: SkillChoice["ref"], position: number): ScheduledTaskSkillRef {
  return ref.skill_source === "managed" ? { ...ref, position } : { ...ref, position };
}

function activeSkillQuery(prompt: string) {
  const marker = prompt.lastIndexOf("$");
  if (marker < 0) return null;
  const suffix = prompt.slice(marker + 1);
  if ([...suffix].some((character) => character.trim() === "")) return null;
  return { marker, query: suffix.toLocaleLowerCase() };
}

function isSkillNameCharacter(character: string | undefined) {
  if (!character) return false;
  const code = character.charCodeAt(0);
  return (code >= 48 && code <= 57)
    || (code >= 65 && code <= 90)
    || (code >= 97 && code <= 122)
    || character === "-"
    || character === "_";
}

function skillTokenRanges(prompt: string, skillName: string) {
  const token = `$${skillName}`;
  const ranges: Array<{ start: number; end: number }> = [];
  let offset = 0;
  while (offset < prompt.length) {
    const start = prompt.indexOf(token, offset);
    if (start < 0) break;
    const end = start + token.length;
    if (!isSkillNameCharacter(prompt[end])) ranges.push({ start, end });
    offset = end;
  }
  return ranges;
}

function hasSkillToken(prompt: string, skillName: string) {
  return skillTokenRanges(prompt, skillName).length > 0;
}

function removeSkillToken(prompt: string, skillName: string) {
  return skillTokenRanges(prompt, skillName)
    .reverse()
    .reduce((value, range) => `${value.slice(0, range.start)}${value.slice(range.end)}`, prompt);
}

function TaskListItem({ task, selected, onSelect }: { task: ScheduledTask; selected: boolean; onSelect: () => void }) {
  return <button type="button" className={`scheduled-task-list-item${selected ? " is-selected" : ""}`} aria-pressed={selected} onClick={onSelect}>
    <span className="scheduled-task-list-title"><strong>{task.name}</strong><span className={`scheduled-task-enabled${task.enabled ? "" : " is-paused"}`}>{task.enabled ? "运行中" : "已暂停"}</span></span>
    <span>{task.schedule_description}</span>
    <small>下次：{timeLabel(task.next_run_at)}</small>
    <small>最近：{task.recent_run?.dispatch_status || "尚未运行"}</small>
  </button>;
}

function RunHistory({ runs, hasMore, loading, onMore }: { runs: ScheduledTaskRun[]; hasMore: boolean; loading: boolean; onMore: () => void }) {
  return <section className="scheduled-task-history" aria-labelledby="scheduled-task-history-title">
    <div className="scheduled-task-section-heading"><h3 id="scheduled-task-history-title">运行记录</h3><span>{runs.length} 条</span></div>
    {runs.length === 0 ? <p className="scheduled-task-empty-copy">尚无运行记录。</p> : <ol>{runs.map((run) => <li key={run.id}>
      <div><strong>{run.trigger_kind === "manual" ? "手动运行" : "定时触发"}</strong><span>{run.dispatch_status}</span></div>
      <small>{timeLabel(run.scheduled_for)}</small>
      {run.execution_kind && run.execution_id && <span>{run.execution_kind} #{run.execution_id}</span>}
      {run.skip_or_error_reason && <p>{run.skip_or_error_reason}</p>}
    </li>)}</ol>}
    {hasMore && <button type="button" className="secondary-button" disabled={loading} onClick={onMore}>{loading ? "加载中…" : "加载更多运行记录"}</button>}
  </section>;
}

export function ScheduledTasksPage() {
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [options, setOptions] = useState<ScheduledTaskOptions | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<ScheduledTaskDraft>(() => emptyDraft(null));
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [mutationState, setMutationState] = useState<MutationState>("idle");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [conflict, setConflict] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [runs, setRuns] = useState<ScheduledTaskRun[]>([]);
  const [historyCursor, setHistoryCursor] = useState("");
  const [historyHasMore, setHistoryHasMore] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [skillMenuOpen, setSkillMenuOpen] = useState(false);

  const selected = tasks.find((item) => item.id === selectedId) || null;
  const choices = useMemo(() => skillChoices(options), [options]);
  const query = activeSkillQuery(draft.prompt);
  const suggestions = !skillMenuOpen || query === null ? [] : choices.filter((choice) => choice.name.toLocaleLowerCase().includes(query.query) || choice.label.toLocaleLowerCase().includes(query.query));

  async function load(signal?: AbortSignal) {
    setLoadState("loading"); setError(""); setConflict(false);
    try {
      const [taskResult, optionResult] = await Promise.all([listScheduledTasks(signal), getScheduledTaskOptions(signal)]);
      if (signal?.aborted) return;
      setTasks(taskResult.items); setOptions(optionResult);
      const nextSelected = taskResult.items.find((item) => item.id === selectedId) || taskResult.items[0] || null;
      setSelectedId(nextSelected?.id || null); setCreating(false);
      setDraft(nextSelected ? taskDraft(nextSelected) : emptyDraft(optionResult));
      setLoadState("ready");
    } catch (reason) {
      if (signal?.aborted) return;
      setError(errorMessage(reason, "定时任务加载失败")); setLoadState("error");
    }
  }

  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, []);

  useEffect(() => {
    if (!selectedId || creating) { setRuns([]); setHistoryCursor(""); setHistoryHasMore(false); return; }
    const controller = new AbortController(); setHistoryLoading(true);
    void listScheduledTaskRuns(selectedId, "", controller.signal).then((page) => {
      if (controller.signal.aborted) return;
      setRuns(page.items); setHistoryCursor(page.meta.next_cursor); setHistoryHasMore(page.meta.has_more);
    }).catch((reason) => { if (!controller.signal.aborted) setError(errorMessage(reason, "运行记录加载失败")); })
      .finally(() => { if (!controller.signal.aborted) setHistoryLoading(false); });
    return () => controller.abort();
  }, [selectedId, creating]);

  function chooseTask(task: ScheduledTask) {
    setSelectedId(task.id); setCreating(false); setDraft(taskDraft(task)); setError(""); setMessage(""); setConflict(false); setConfirmDelete(false); setSkillMenuOpen(false);
  }

  function beginCreate() {
    setCreating(true); setSelectedId(null); setDraft(emptyDraft(options)); setError(""); setMessage(""); setConflict(false); setConfirmDelete(false); setSkillMenuOpen(false);
  }

  function updateDraft<K extends keyof ScheduledTaskDraft>(key: K, value: ScheduledTaskDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value })); setMessage(""); setError(""); setConflict(false);
  }

  function updatePrompt(prompt: string) {
    setDraft((current) => ({
      ...current,
      prompt,
      skill_refs: current.skill_refs
        .filter((ref) => !hasSkillToken(current.prompt, ref.skill_name) || hasSkillToken(prompt, ref.skill_name))
        .map((ref, position) => ({ ...ref, position })),
    }));
    setMessage(""); setError(""); setConflict(false);
  }

  function selectSkill(choice: SkillChoice) {
    if (!choice.available || query === null) return;
    const nextPrompt = `${draft.prompt.slice(0, query.marker)}$${choice.name} `;
    const existing = draft.skill_refs.some((ref) => refKey(ref) === choice.key);
    const refs: ScheduledTaskSkillRef[] = existing ? draft.skill_refs : [
      ...draft.skill_refs.filter((ref) => !(ref.skill_source === choice.ref.skill_source && ref.skill_name === choice.name)),
      positionedRef(choice.ref, 0),
    ].map((ref, position) => ({ ...ref, position }));
    setDraft((current) => ({ ...current, prompt: nextPrompt, skill_refs: refs }));
    setSkillMenuOpen(false);
  }

  function removeSkill(index: number) {
    setDraft((current) => {
      const removed = current.skill_refs[index];
      const skill_refs = current.skill_refs.filter((_, position) => position !== index).map((ref, position) => ({ ...ref, position }));
      const stillReferenced = removed && skill_refs.some((ref) => ref.skill_name === removed.skill_name);
      return {
        ...current,
        prompt: removed && !stillReferenced ? removeSkillToken(current.prompt, removed.skill_name) : current.prompt,
        skill_refs,
      };
    });
    setMessage(""); setError(""); setConflict(false);
  }

  async function save() {
    if (!draft.name.trim() || !draft.prompt.trim() || !draft.cron_expression.trim() || !draft.timezone_name.trim() || !draft.runtime_id || draft.skill_refs.length === 0) {
      setError("请填写名称、描述、Cron、时区、Runtime，并至少选择一个 Skill。"); return;
    }
    setMutationState("saving"); setError(""); setMessage(""); setConflict(false);
    try {
      const result = creating
        ? await createScheduledTask(draft)
        : selected ? await updateScheduledTask(selected.id, { ...draft, version: selected.version }) : null;
      if (!result) return;
      setTasks((current) => creating ? [...current, result.item] : current.map((item) => item.id === result.item.id ? result.item : item));
      setSelectedId(result.item.id); setCreating(false); setDraft(taskDraft(result.item)); setMessage(creating ? "定时任务已创建" : "定时任务已保存");
    } catch (reason) {
      if (isConflict(reason)) { setConflict(true); setError("其他页面已更新这个任务。你的草稿仍保留，请重新加载最新版本后再确认修改。"); }
      else setError(errorMessage(reason, creating ? "创建失败，草稿仍保留" : "保存失败，草稿仍保留"));
    } finally { setMutationState("idle"); }
  }

  async function toggleEnabled() {
    if (!selected) return;
    setMutationState("saving"); setError("");
    try {
      const result = await setScheduledTaskEnabled(selected.id, !selected.enabled, selected.version);
      setTasks((current) => current.map((item) => item.id === result.item.id ? result.item : item)); setDraft(taskDraft(result.item));
    } catch (reason) {
      if (isConflict(reason)) { setConflict(true); setError("其他页面已更新这个任务。请重新加载最新版本。"); }
      else setError(errorMessage(reason, "任务状态更新失败"));
    } finally { setMutationState("idle"); }
  }

  async function runNow() {
    if (!selected) return;
    setMutationState("saving"); setError("");
    try { const result = await runScheduledTask(selected.id); setRuns((current) => [result.item, ...current]); setMessage("已创建一次手动运行"); }
    catch (reason) { setError(errorMessage(reason, "手动运行失败")); }
    finally { setMutationState("idle"); }
  }

  async function remove() {
    if (!selected) return;
    setMutationState("saving"); setError("");
    try {
      await deleteScheduledTask(selected.id, selected.version);
      const remaining = tasks.filter((item) => item.id !== selected.id); setTasks(remaining); setConfirmDelete(false);
      const next = remaining[0] || null; setSelectedId(next?.id || null); setDraft(next ? taskDraft(next) : emptyDraft(options)); setMessage("定时任务已删除，历史记录仍保留");
    } catch (reason) {
      if (isConflict(reason)) { setConflict(true); setError("其他页面已更新这个任务。请重新加载最新版本。"); }
      else setError(errorMessage(reason, "删除失败"));
    } finally { setMutationState("idle"); }
  }

  async function loadMoreRuns() {
    if (!selected || !historyCursor || historyLoading) return;
    setHistoryLoading(true);
    try { const page = await listScheduledTaskRuns(selected.id, historyCursor); setRuns((current) => [...current, ...page.items]); setHistoryCursor(page.meta.next_cursor); setHistoryHasMore(page.meta.has_more); }
    catch (reason) { setError(errorMessage(reason, "运行记录加载失败")); }
    finally { setHistoryLoading(false); }
  }

  if (loadState === "loading") return <main className="console-page scheduled-tasks-page"><section className="console-card page-state" role="status">正在加载定时任务…</section></main>;
  if (loadState === "error") return <main className="console-page scheduled-tasks-page"><section className="console-card page-state page-state-error" role="alert"><p>{error}</p><button type="button" className="secondary-button" onClick={() => void load()}>重试加载</button></section></main>;

  return <main className="console-page scheduled-tasks-page">
    <header className="console-page-header"><div><p className="eyebrow">AGENT CRON</p><h1>定时任务</h1><p className="muted">配置 Cron、Agent Skills 与执行 Runtime。Connector 只提供连接能力。</p></div><button type="button" className="primary-button" onClick={beginCreate}>新建任务</button></header>
    {message && <p className="scheduled-task-notice" role="status">{message}</p>}
    <div className="scheduled-task-workspace">
      <section className="scheduled-task-master" aria-label="定时任务列表">
        <div className="scheduled-task-pane-heading"><h2>任务</h2><span>{tasks.length}</span></div>
        {tasks.length ? <div className="scheduled-task-list">{tasks.map((task) => <TaskListItem key={task.id} task={task} selected={task.id === selectedId} onSelect={() => chooseTask(task)} />)}</div> : <div className="scheduled-task-empty"><strong>还没有定时任务</strong><p>新建后，系统会按 Cron 触发 Agent 执行。</p><button type="button" className="secondary-button" onClick={beginCreate}>创建第一个任务</button></div>}
      </section>
      <section className="scheduled-task-detail" aria-label={creating ? "新建定时任务" : "定时任务编辑器"}>
        {(creating || selected) ? <>
          <div className="scheduled-task-pane-heading"><div><h2>{creating ? "新建定时任务" : selected?.name}</h2>{selected && <small>版本 {selected.version}</small>}</div>{selected && <div className="scheduled-task-actions"><button type="button" className="secondary-button" disabled={mutationState === "saving"} onClick={() => void toggleEnabled()}>{selected.enabled ? "暂停任务" : "启用任务"}</button><button type="button" className="secondary-button" disabled={mutationState === "saving"} onClick={() => void runNow()}>立即运行</button><button type="button" className="danger-button" disabled={mutationState === "saving"} onClick={() => setConfirmDelete(true)}>删除任务</button></div>}</div>
          {confirmDelete && <div className="scheduled-task-delete-confirm" role="alertdialog" aria-label="确认删除定时任务"><p>删除后任务不会再触发，历史记录仍会保留。</p><div><button type="button" className="danger-button" onClick={() => void remove()}>确认删除</button><button type="button" className="secondary-button" onClick={() => setConfirmDelete(false)}>取消</button></div></div>}
          {error && <div className="scheduled-task-form-error" role="alert"><span>{error}</span>{conflict && <button type="button" className="secondary-button" onClick={() => void load()}>重新加载最新版本</button>}</div>}
          <form className="scheduled-task-form" onSubmit={(event) => { event.preventDefault(); void save(); }}>
            <label><span>任务名称</span><input aria-label="任务名称" value={draft.name} onChange={(event) => updateDraft("name", event.target.value)} /></label>
            <div className="scheduled-task-form-row"><label><span>Cron（秒 分 时 日 月 周）</span><input aria-label="Cron 表达式" value={draft.cron_expression} onChange={(event) => updateDraft("cron_expression", event.target.value)} /></label><label><span>时区</span><input aria-label="时区" value={draft.timezone_name} onChange={(event) => updateDraft("timezone_name", event.target.value)} /></label></div>
            <div className="scheduled-task-form-row"><label><span>Runtime</span><select aria-label="Runtime" value={draft.runtime_id} onChange={(event) => updateDraft("runtime_id", event.target.value)}>{options?.runtime_options.map((runtime) => <option key={runtime.route_name} value={runtime.route_name} disabled={!runtime.available}>{runtime.route_name} · {runtime.model}{runtime.available ? "" : ` · 不可用：${runtime.unavailable_reason}`}</option>)}</select></label><label><span>Reasoning</span><select aria-label="Reasoning" value={draft.runtime_options.thinking || ""} onChange={(event) => updateDraft("runtime_options", { thinking: event.target.value as ScheduledTaskDraft["runtime_options"]["thinking"] })}><option value="low">low</option><option value="medium">medium</option><option value="high">high</option><option value="xhigh">xhigh</option></select></label></div>
            <label><span>工作目录（可选）</span><input aria-label="工作目录" value={draft.working_directory} onChange={(event) => updateDraft("working_directory", event.target.value)} placeholder="使用服务默认目录" /></label>
            <label className="scheduled-task-prompt-field"><span>任务描述</span><textarea aria-label="任务描述" rows={7} value={draft.prompt} onChange={(event) => { updatePrompt(event.target.value); setSkillMenuOpen(activeSkillQuery(event.target.value) !== null); }} placeholder="描述 Agent 每次触发要完成什么；输入 $ 引用 Skill" />
              {skillMenuOpen && query !== null && <div className="scheduled-task-suggestions" role="listbox" aria-label="Skill 建议">{suggestions.length ? suggestions.map((choice) => <button type="button" role="option" aria-selected="false" disabled={!choice.available} key={choice.key} onClick={() => selectSkill(choice)}><strong>{choice.label}</strong><small>{choice.description}{choice.available ? "" : ` · 不可用：${choice.unavailableReason}`}</small></button>) : <p>没有匹配的 Skill</p>}</div>}
            </label>
            <div className="scheduled-task-skill-block"><div className="scheduled-task-section-heading"><h3>Agent Skills</h3><span>由结构化引用执行，不从描述文字推断</span></div>{draft.skill_refs.length ? <div className="scheduled-task-skill-chips">{draft.skill_refs.map((ref, index) => { const choice = choices.find((item) => item.key === refKey(ref)); const label = choice?.label || ref.skill_name; return <button type="button" key={`${refKey(ref)}:${index}`} aria-label={`移除${label}`} onClick={() => removeSkill(index)}><span>{label}</span><small>{ref.skill_source === "managed" ? "Managed" : "Operation"}</small><b aria-hidden="true">×</b></button>; })}</div> : <p className="scheduled-task-empty-copy">输入 <code>$</code> 搜索并选择至少一个 Skill。</p>}</div>
            <button type="submit" className="primary-button" disabled={mutationState === "saving"}>{mutationState === "saving" ? "保存中…" : creating ? "创建任务" : "保存更改"}</button>
          </form>
          {!creating && <RunHistory runs={runs} hasMore={historyHasMore} loading={historyLoading} onMore={() => void loadMoreRuns()} />}
        </> : <div className="scheduled-task-empty"><strong>选择或新建一个任务</strong><p>右侧会显示 Cron、Skills、Runtime 与运行记录。</p></div>}
      </section>
    </div>
  </main>;
}
