import { useEffect, useRef, useState } from "react";
import { getPromptPreview, type PromptPreviewAttempt, type PromptPreviewItem, type PromptPreviewQuery, type PromptPreviewRole, type PromptPreviewRoute } from "../api/promptPreview";

function positiveId(value: string) {
  const parsed = Number(value);
  return value.trim() && Number.isSafeInteger(parsed) && parsed > 0 ? parsed : undefined;
}

export function RuntimePromptPreview() {
  const [role, setRole] = useState<PromptPreviewRole>("consumer");
  const [route, setRoute] = useState("");
  const [routes, setRoutes] = useState<PromptPreviewRoute[]>([]);
  const [mode, setMode] = useState<"current" | "historical">("current");
  const [taskId, setTaskId] = useState("");
  const [runId, setRunId] = useState("");
  const [attempts, setAttempts] = useState<PromptPreviewAttempt[]>([]);
  const [attemptId, setAttemptId] = useState("");
  const [item, setItem] = useState<PromptPreviewItem | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);

  function clearPreview(resetAttempts = true) {
    if (resetAttempts) { setAttempts([]); setAttemptId(""); }
    active.current?.abort();
    setItem(null);
    setError("");
    setLoading(false);
  }

  function load(params: PromptPreviewQuery) {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setItem(null);
    setError("");
    setLoading(true);
    const timer = setTimeout(() => {
      controller.abort();
      setError("加载超过 30 秒没有返回，请点击查看 / 刷新重试。");
      setLoading(false);
    }, 30_000);
    controller.signal.addEventListener("abort", () => clearTimeout(timer), { once: true });
    getPromptPreview(params, controller.signal).then((response) => {
      clearTimeout(timer);
      if (controller.signal.aborted) return;
      setItem(response.item);
      if (response.item.mode === "historical") {
        setAttempts(response.item.attempts);
        setAttemptId(response.item.runtime_attempt_id === null ? "" : String(response.item.runtime_attempt_id));
      }
      setRoutes(response.item.routes);
      if (response.item.mode === "current") setRoute(response.item.route_name);
      setLoading(false);
    }, (reason: unknown) => {
      clearTimeout(timer);
      if (controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : "预览加载失败，请重试");
      setLoading(false);
    });
  }

  useEffect(() => {
    load({ role: "consumer" });
    return () => active.current?.abort();
  }, []);

  const invalidId = mode === "historical" ? !positiveId(runId) : Boolean(taskId && !positiveId(taskId));
  const controlStyle = { width: "100%", minWidth: 0 };
  const fieldStyle = { display: "grid", gap: "6px", minWidth: 0 };
  const textStyle = { minWidth: 0, maxWidth: "100%", whiteSpace: "pre-wrap" as const, overflowWrap: "anywhere" as const };
  return <section aria-label="完整运行输入预览" style={{ minWidth: 0 }}>
    <p className="muted">当前预览按已保存的配置生成；历史输入显示该次运行保存的原文。查看不会运行任务或保存设置。</p>
    <form onSubmit={(event) => {
      event.preventDefault();
      if (invalidId) return;
      load(mode === "historical" ? { role, run_id: positiveId(runId), ...(attemptId ? { runtime_attempt_id: positiveId(attemptId) } : {}) } : { role, ...(route ? { route_name: route } : {}), ...(taskId ? { task_id: positiveId(taskId) } : {}) });
    }}>
      <div className="settings-field-list" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 180px), 1fr))" }}>
        <label className="runtime-field" style={fieldStyle}>角色<select style={controlStyle} aria-label="角色" value={role} onChange={(event) => { clearPreview(); setRole(event.target.value as PromptPreviewRole); }}><option value="consumer">Consumer</option><option value="audit">Audit</option></select></label>
        <label className="runtime-field" style={fieldStyle}>运行路线<select style={controlStyle} aria-label="运行路线" disabled={mode === "historical"} value={route} onChange={(event) => { clearPreview(); setRoute(event.target.value); }}>{!route && <option value="">默认路线</option>}{routes.map((entry) => <option key={entry.name} value={entry.name}>{entry.name} · {entry.runtime_kind} · {entry.model}</option>)}</select></label>
        <label className="runtime-field" style={fieldStyle}>预览来源<select style={controlStyle} aria-label="预览来源" value={mode} onChange={(event) => { clearPreview(); setRoute(""); setMode(event.target.value as "current" | "historical"); }}><option value="current">当前预览</option><option value="historical">历史输入</option></select></label>
        {mode === "current" ? <label className="runtime-field" style={fieldStyle}>任务 ID（可选）<input style={controlStyle} aria-label="任务 ID（可选）" type="number" min="1" step="1" value={taskId} onChange={(event) => { clearPreview(); setTaskId(event.target.value); }} /></label> : <label className="runtime-field" style={fieldStyle}>运行 ID<input style={controlStyle} aria-label="运行 ID" type="number" min="1" step="1" required value={runId} onChange={(event) => { clearPreview(); setRunId(event.target.value); }} /></label>}
        {mode === "historical" && attempts.length > 0 && <label className="runtime-field" style={fieldStyle}>运行尝试<select style={controlStyle} aria-label="运行尝试" value={attemptId} onChange={(event) => { clearPreview(false); setAttemptId(event.target.value); }}>
          {!attemptId && <option value="">最后一次尝试</option>}
          {attempts.map((attempt) => <option key={attempt.runtime_attempt_id} value={attempt.runtime_attempt_id}>{attempt.runtime_attempt_id} · {attempt.route_name} · {attempt.submission_state === "invoked" ? "已提交" : "已准备"} · {attempt.rendered_at}</option>)}
        </select></label>}
      </div>
      <div className="settings-save-row"><button className="primary-button" type="submit" disabled={invalidId}>查看 / 刷新</button></div>
    </form>
    {loading && <p role="status">正在加载完整输入…</p>}
    {error && <p className="save-error" role="alert">{error}</p>}
    {!loading && !error && !item && <p className="muted">{mode === "historical" ? "输入运行 ID 后查看历史输入。" : "选择完成后点击查看 / 刷新。"}</p>}
    {item && <>
      <p className="muted" style={textStyle}>{item.mode === "historical" ? "历史输入 · 已保存的运行记录" : "当前预览 · 当前已保存配置"} · {item.role === "audit" ? "Audit" : "Consumer"} · {item.route_name} · {item.runtime_kind} · {item.model} · {item.rendered_at || "时间未记录"}{item.run_id !== null && ` · 运行 ${item.run_id}`}{item.task_id !== null && ` · 任务 ${item.task_id}`}</p>
      {item.mode === "historical" && <p className="muted" style={textStyle}>
        {item.submission_state === "prepared" ? "已准备的服务输入" : item.submission_state === "invoked" ? "已提交的服务输入" : "预览输入"}
        {item.runtime_attempt_id !== null && ` · 运行尝试 ${item.runtime_attempt_id}`}
        {item.execution_generation !== null && ` · 执行批次 ${item.execution_generation}`}
        {item.proposal_revision !== null && ` · 提案版本 ${item.proposal_revision}`}
        {item.stage_index !== null && ` · 阶段 ${item.stage_index}`}
      </p>}
      {item.scope && <p className="muted" style={textStyle}>{item.scope}</p>}
      {item.status === "available" && item.reason && <p className="muted" style={textStyle}>{item.reason}</p>}
      {item.status === "unavailable" ? <p role="status">{item.reason || (item.mode === "historical" ? "该运行的历史输入不可用。" : "当前任务预览不可用。")}</p> : <>
        {item.mode === "current" && item.task_id === null && <p className="muted">未绑定任务：展示当前公共上下文，不包含具体任务输入。</p>}
        <h3>运行环境与能力说明 (Runtime Context)</h3>
        <pre className="prompt-preview" style={textStyle}>{item.runtime_context || "未记录运行环境说明"}</pre>
        <h3>Developer Prompt · 完整指令</h3>
        <pre className="prompt-preview" style={textStyle}>{item.developer_instructions || "未提供 Developer Prompt"}</pre>
        <h3>User Prompt · 完整任务输入</h3>
        <pre className="prompt-preview" style={textStyle}>{item.task_prompt || "未绑定具体任务输入"}</pre>
        {item.runtime_kind === "claude_cli" && <><h3>Claude · 完整服务输入</h3><pre className="prompt-preview" style={textStyle}>{item.submitted_input || "未保存服务输入"}</pre></>}
      </>}
    </>}
  </section>;
}
