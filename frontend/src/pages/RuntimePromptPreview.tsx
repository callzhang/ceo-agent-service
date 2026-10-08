import { useEffect, useRef, useState } from "react";
import { getPromptPreview, type PromptPreviewAttempt, type PromptPreviewItem, type PromptPreviewQuery, type PromptPreviewRole, type PromptPreviewRoute } from "../api/promptPreview";

function positiveId(value: string) {
  const parsed = Number(value);
  return value.trim() && Number.isSafeInteger(parsed) && parsed > 0 ? parsed : undefined;
}

function ConfigurationFingerprints({ item }: { item: PromptPreviewItem }) {
  const fields = [
    ["developer_template", "Developer template"],
    ["developer_instructions", "Developer rendered principles"],
    ["user_template", item.role === "audit" ? "User template（Audit 不使用）" : "User template"],
    ["work_profile_instruction", "Work profile wrapper"],
  ] as const;
  return <details className="settings-collapse">
    <summary>配置指纹与 Task 来源</summary>
    <p className="muted">仅比较配置指纹，不代表完整输入或外部事实相同。{item.mode === "historical" ? "历史 SHA 只来自该次保存记录；缺项为未记录，未按当前配置重建。" : "当前配置的 User SHA 不表示历史 Task 已用新模板重新渲染。"}</p>
    {item.task_source_run_id != null && <p className="muted">Task 来源运行 {item.task_source_run_id} · 保存时间 {item.task_source_rendered_at || "未记录"}</p>}
    <div className="settings-table-wrap"><table className="settings-table" aria-label="配置 SHA 与来源">
      <thead><tr><th>配置项</th><th>{item.mode === "historical" ? "历史配置" : "当前配置"}</th><th>历史 Task 来源配置</th><th>指纹比较</th></tr></thead>
      <tbody>{fields.map(([key, label]) => {
        const configuration = item.configuration_fingerprints?.[key];
        const source = item.task_source_configuration_fingerprints?.[key];
        const comparison = item.mode === "current" && item.task_id === null ? "无任务来源" : !configuration || !source ? "无法比较" : configuration === source ? "一致" : "不同";
        return <tr key={key}><td>{label}</td><td><code style={{ overflowWrap: "anywhere" }}>{configuration || "未记录"}</code></td><td><code style={{ overflowWrap: "anywhere" }}>{source || "未记录"}</code></td><td>{comparison}</td></tr>;
      })}</tbody>
    </table></div>
  </details>;
}

function PromptSections({ item }: { item: PromptPreviewItem }) {
  if (!item.sections?.length) {
    return <p className="muted">{item.mode === "historical" ? "该历史输入未记录分段来源" : "当前预览未返回分段来源"}</p>;
  }
  return <section aria-label="输入分段来源">
    <h3>输入分段来源与顺序</h3>
    <p className="muted">顺序来自服务组装记录。字符数按本页返回并显示的已清理文本统计，不含分段之间的拼接分隔符。CLI 自带的系统提示、工具定义或会话历史不在这份分段回执中。</p>
    <div className="settings-table-wrap"><table className="settings-table" aria-label="输入分段来源与顺序">
      <thead><tr><th>顺序</th><th>分段</th><th>来源</th><th>提交位置</th><th>字符数</th></tr></thead>
      <tbody>{item.sections.map((section, index) => <tr key={`${section.placement}-${index}-${section.name}`}>
        <td data-label="顺序">{index + 1}</td>
        <td data-label="分段">{section.name}</td>
        <td data-label="来源">{section.source}</td>
        <td data-label="提交位置">{section.placement === "developer" ? "Developer" : "Task"}</td>
        <td data-label="字符数">{section.characters}</td>
      </tr>)}</tbody>
    </table></div>
  </section>;
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
    <p className="muted">当前预览按已保存的配置生成；绑定任务时沿用该角色已保存的历史任务正文，来源见下方说明。历史输入显示该次运行保存的原文。查看不会运行任务或保存设置。</p>
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
      <p className="muted" style={textStyle}>{item.mode === "historical" ? "历史输入 · 已保存的运行记录" : item.task_id === null || item.status === "unavailable" ? "当前预览 · 当前已保存配置" : "当前预览 · 当前配置与已保存历史任务正文"} · {item.role === "audit" ? "Audit" : "Consumer"} · {item.route_name} · {item.runtime_kind} · {item.model} · {item.rendered_at || "时间未记录"}{item.run_id !== null && ` · 运行 ${item.run_id}`}{item.task_id !== null && ` · 任务 ${item.task_id}`}</p>
      {item.mode === "historical" && <p className="muted" style={textStyle}>
        {item.submission_state === "prepared" ? "已准备的服务输入" : item.submission_state === "invoked" ? "已提交的服务输入" : "预览输入"}
        {item.runtime_attempt_id !== null && ` · 运行尝试 ${item.runtime_attempt_id}`}
        {item.execution_generation !== null && ` · 执行批次 ${item.execution_generation}`}
        {item.proposal_revision !== null && ` · 提案版本 ${item.proposal_revision}`}
        {item.stage_index !== null && ` · 阶段 ${item.stage_index}`}
      </p>}
      {item.scope && <p className="muted" style={textStyle}>{item.scope}</p>}
      {item.status === "available" && item.reason && <p className="muted" style={textStyle}>{item.reason}</p>}
      <PromptSections item={item} />
      <ConfigurationFingerprints item={item} />
      {item.status === "unavailable" ? <p role="status">{item.reason || (item.mode === "historical" ? "该运行的历史输入不可用。" : "当前任务预览不可用。")}</p> : <>
        {item.mode === "current" && item.task_id === null && <p className="muted">未绑定任务：展示当前公共上下文，不包含具体任务输入。</p>}
        {item.mode === "current" && item.task_id !== null && <p className="muted">Developer 按当前已保存配置组装；Task 沿用已保存的历史正文，{item.role === "audit" ? "包含当时的独立候选审核上下文" : "可能采用当时的 User 模板"}。这份混合预览不是该次运行的实际输入，也不代表外部资料当前状态；实际原文请切换到历史输入。</p>}
        <h3>运行环境与能力说明 (Runtime Context)</h3>
        <pre className="prompt-preview" style={textStyle}>{item.runtime_context || "未记录运行环境说明"}</pre>
        <h3>Developer Prompt · 完整指令</h3>
        <pre className="prompt-preview" style={textStyle}>{item.developer_instructions || "未提供 Developer Prompt"}</pre>
        <h3>{item.role === "audit" ? "Task · 完整候选审核输入" : "User Prompt · 完整任务输入"}</h3>
        <pre className="prompt-preview" style={textStyle}>{item.task_prompt || "未绑定具体任务输入"}</pre>
        {item.runtime_kind === "claude_cli" && <><h3>Claude · 完整服务输入</h3><pre className="prompt-preview" style={textStyle}>{item.submitted_input || "未保存服务输入"}</pre></>}
      </>}
    </>}
  </section>;
}
