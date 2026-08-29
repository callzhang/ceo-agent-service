import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useSearchParams } from "react-router-dom";

import { displayValue, getSettings, saveSettings } from "../api/console";
import { TokenEditor } from "../components/editor/TokenEditor";
import { SecretField } from "../components/forms/SecretField";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SnapshotBadge } from "../components/status/SnapshotBadge";
import { StatusBadge } from "../components/status/StatusBadge";
import { AttentionPanel } from "./AttentionPage";
import { StatusPanel } from "./StatusPage";

type RecordValue = Record<string, unknown>;
type SettingsSection = "status" | "info" | "configuration" | "agent-runtime" | "prompts" | "connectors" | "audit-rules" | "attention";

const sections: Array<[SettingsSection, string]> = [
  ["status", "Status"], ["info", "Info"], ["configuration", "Configuration"], ["agent-runtime", "Agent Runtime"],
  ["prompts", "Prompts"], ["connectors", "Connectors"], ["audit-rules", "Audit Rules"], ["attention", "Attention"],
];

function record(value: unknown): RecordValue {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as RecordValue : {};
}

function fieldsOf(payload: RecordValue) {
  return record(payload.fields);
}

function SectionNav({ section }: { section: SettingsSection }) {
  return <nav className="settings-nav-react" aria-label="Settings navigation">
    {sections.map(([key, label]) => <NavLink key={key} to={`/settings?tab=${key}`} className={section === key ? "active" : ""} aria-current={section === key ? "page" : undefined}>{label}</NavLink>)}
  </nav>;
}

function SettingsCard({ children }: { children: ReactNode }) {
  return <section className="console-card settings-content-card">{children}</section>;
}

function SaveBar({ state }: { state: "idle" | "saving" | "saved" | "error" }) {
  return <div className="settings-save-row"><button type="submit" className="primary-button" disabled={state === "saving"}>{state === "saving" ? "保存中…" : "保存"}</button>{state === "saved" && <span className="save-success" role="status">已保存</span>}{state === "error" && <span className="save-error" role="alert">保存失败，草稿仍保留</span>}</div>;
}

function ConfigTable({ groups, compatibility, draft, setDraft }: { groups: RecordValue[]; compatibility: RecordValue[]; draft: RecordValue; setDraft: (value: RecordValue) => void }) {
  const update = (key: string, value: string) => setDraft({ ...draft, [key]: value });
  return <>
    {groups.map((group) => <section className="configuration-group" key={displayValue(group.name)}>
      <h2>{displayValue(group.name)}</h2>
      <div className="settings-table-wrap"><table className="settings-table"><thead><tr><th>Key</th><th>Current value</th><th>Description</th></tr></thead><tbody>
        {Array.isArray(group.items) && group.items.map((rawItem, index) => { const item = record(rawItem); const key = displayValue(item.key || index); const editable = item.editable !== false; return <tr key={key}><td data-label="Key"><code>{key}</code></td><td data-label="Current value">{editable ? <input aria-label={key} value={displayValue(draft[key] ?? item.value)} onChange={(event) => update(key, event.target.value)} /> : <code>{displayValue(item.value)}</code>}</td><td data-label="Description"><SummaryDescription value={displayValue(item.description)} /></td></tr>; })}
      </tbody></table></div>
    </section>)}
    {compatibility.length > 0 && <details className="settings-collapse"><summary>Compatibility keys</summary><p className="muted">旧版本兼容字段只读展示，不再作为重复配置编辑。身份显示名请统一修改 <code>USER_ALIAS</code>。</p><div className="settings-table-wrap"><table className="settings-table"><tbody>{compatibility.map((item) => <tr key={displayValue(item.key)}><td data-label="Key"><code>{displayValue(item.key)}</code></td><td data-label="Current value"><code>{displayValue(item.value)}</code></td><td data-label="Description">{displayValue(item.description)}</td></tr>)}</tbody></table></div></details>}
  </>;
}

function SummaryDescription({ value }: { value: string }) {
  return <span className="settings-description" title={value}>{value || "未提供描述"}</span>;
}

function PromptPanel({ payload, prompt, view, draft, setDraft, saveState }: { payload: RecordValue; prompt: "developer" | "user"; view: "template" | "preview"; draft: RecordValue; setDraft: (value: RecordValue) => void; saveState: "idle" | "saving" | "saved" | "error" }) {
  const templateKey = `${prompt}_template`;
  const value = displayValue(draft[templateKey] ?? fieldsOf(payload)[templateKey]);
  const preview = displayValue(record(payload.preview)[prompt]);
  return <SettingsCard>
    <div className="settings-card-heading"><div><h2>Prompts</h2><p className="muted">{prompt === "developer" ? "Developer Prompt" : "User Prompt"} · 模板由服务端读取，预览使用当前运行时上下文。</p></div><span className="settings-path">{prompt === "developer" ? "developer_prompt.md" : "user_prompt.md"}</span></div>
    <div className="settings-pill-row" role="tablist" aria-label="Prompt sections"><NavLink role="tab" aria-selected={prompt === "developer"} className={prompt === "developer" ? "active" : ""} to="/settings?tab=prompts&prompt=developer&view=template">Developer Prompt</NavLink><NavLink role="tab" aria-selected={prompt === "user"} className={prompt === "user" ? "active" : ""} to="/settings?tab=prompts&prompt=user&view=template">User Prompt</NavLink></div>
    <div className="settings-pill-row settings-view-row" role="tablist" aria-label="Prompt view"><NavLink role="tab" aria-selected={view === "template"} className={view === "template" ? "active" : ""} to={`/settings?tab=prompts&prompt=${prompt}&view=template`}>Template</NavLink><NavLink role="tab" aria-selected={view === "preview"} className={view === "preview" ? "active" : ""} to={`/settings?tab=prompts&prompt=${prompt}&view=preview`}>Rendered preview</NavLink></div>
    {view === "template" ? <form onSubmit={(event) => event.preventDefault()}><TokenEditor id="prompt-template" label="Template" value={value} onChange={(next) => setDraft({ ...draft, [templateKey]: next })} rows={18} /><p className="muted prompt-runtime-note">运行时注入变量：<code>{"{{principal}}"}</code> <code>{"{{conversation}}"}</code>。这些变量不需要手动填写。</p><SaveBar state={saveState} /></form> : <><p className="muted">Rendered preview · {prompt === "user" ? "sample runtime context" : "current configuration"}</p><pre className="prompt-preview">{preview || "未提供预览"}</pre></>}
  </SettingsCard>;
}

function ConnectorPanel({ payload, connector }: { payload: RecordValue; connector: string }) {
  const item = record(payload[connector]);
  const state = displayValue(item.state || item.status || "unknown");
  const commands = Array.isArray(item.commands) ? item.commands : [];
  if (connector === "wechat") return <SettingsCard><h2>Connectors</h2><div className="settings-pill-row" role="tablist" aria-label="Connector sections"><ConnectorTab value="dingtalk" label="DingTalk" active={false} /><ConnectorTab value="lark" label="Lark" active={false} /><ConnectorTab value="wechat" label="WeChat" active /></div><h3>微信自动回复对象</h3><p className="muted">持续维护自动回复范围；微信连接和能力检查请在 Tutorial 完成。</p><a className="secondary-button settings-inline-link" href="/wechat/conversations">打开回复范围</a></SettingsCard>;
  return <SettingsCard><div className="settings-card-heading"><div><h2>Connectors</h2><p className="muted">External connector status, live probes, and local CLI readiness.</p></div><StatusBadge value={state} /></div><div className="settings-pill-row" role="tablist" aria-label="Connector sections"><ConnectorTab value="dingtalk" label="DingTalk" active={connector === "dingtalk"} /><ConnectorTab value="lark" label="Lark" active={connector === "lark"} /><ConnectorTab value="wechat" label="WeChat" active={false} /></div><div className="connector-heading"><h3>{connector === "dingtalk" ? "DingTalk connector" : "Lark connector"}</h3><StatusBadge value={state} /></div><p className="muted">只显示当前连接器的 readiness、live probe 和登录状态。</p><div className="connector-state-grid"><StateItem label="Reason" value={displayValue(item.reason_code)} /><StateItem label="Login" value={displayValue(item.login || "not requested")} /><StateItem label="Last success" value={displayValue(item.last_success || (state === "ready" ? "本次检查" : "尚无成功记录"))} /><StateItem label="Detail" value={displayValue(item.detail || "没有额外说明。")} /></div><h4>Checks</h4><div className="connector-commands">{commands.length ? commands.map((command, index) => <code key={index}>{displayValue(command)}</code>) : <span className="muted">未执行</span>}</div></SettingsCard>;
}

function ConnectorTab({ value, label, active }: { value: string; label: string; active: boolean }) {
  return <NavLink role="tab" aria-selected={active} className={active ? "active" : ""} to={`/settings?tab=connectors&connector=${value}`}>{label}</NavLink>;
}

function StateItem({ label, value }: { label: string; value: string }) { return <div className="connector-state-item"><span>{label}</span><strong>{value}</strong></div>; }

function RuntimePanel({ payload, draft, setDraft, saveState }: { payload: RecordValue; draft: RecordValue; setDraft: (value: RecordValue) => void; saveState: "idle" | "saving" | "saved" | "error" }) {
  const value = (key: string) => displayValue(draft[key] ?? fieldsOf(payload)[key]);
  const input = (key: string, label: string, type = "text") => <label className="runtime-field"><span>{label}</span><input type={type} value={value(key)} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} /></label>;
  return <SettingsCard><div className="settings-card-heading"><div><p className="eyebrow">Settings / Agent Runtime</p><h2>Agent Runtime</h2><p className="muted">集中管理模型路由和 fallback 凭据。保存后重启主服务，运行中的 worker 才会使用新配置。</p></div><span className="settings-path">.env backed</span></div><div className="runtime-overview"><StateItem label="Primary route" value="Codex OAuth" /><StateItem label="Model" value={value("CEO_CODEX_MODEL")} /><StateItem label="API fallback" value={value("CEO_AGENT_RUNTIME_ROUTES").includes("codex_api") ? "Active" : "Not active"} /><StateItem label="Friday Runtime" value={value("CEO_AGENT_RUNTIME_ROUTES").includes("friday_runtime") ? "Active" : "Not active"} /></div><form onSubmit={(event) => event.preventDefault()}><div className="runtime-card-grid"><section className="runtime-card"><div className="runtime-card-head"><div><h3>Codex OAuth</h3><p>默认的本机 OAuth 路由</p></div><StatusBadge value="Active" /></div><div className="runtime-fields">{input("CEO_CODEX_MODEL", "Model")}{input("CEO_CODEX_MODEL_REASONING_EFFORT", "Thinking strength")}</div></section><section className="runtime-card"><div className="runtime-card-head"><div><h3>Codex API fallback</h3><p>OAuth 不可用时的备用路由</p></div><StatusBadge value={value("CEO_AGENT_RUNTIME_ROUTES").includes("codex_api") ? "Active" : "Not active"} /></div><div className="runtime-fields">{input("CEO_CODEX_API_BASE_URL", "API Base URL", "url")}{input("CEO_CODEX_API_MODEL", "Fallback model")}<SecretField id="codex-api-token" label="API Token" configured value="" onChange={(next) => setDraft({ ...draft, CEO_CODEX_API_KEY: next })} /></div></section><section className="runtime-card runtime-card-wide"><div className="runtime-card-head"><div><h3>Friday Runtime</h3><p>本机 Friday Runtime 服务和 provider 凭据</p></div><StatusBadge value={value("CEO_AGENT_RUNTIME_ROUTES").includes("friday_runtime") ? "Active" : "Not active"} /></div><div className="runtime-fields">{input("CEO_FRIDAY_RUNTIME_BASE_URL", "Runtime Base URL", "url")}{input("CEO_FRIDAY_RUNTIME_PROJECT_ID", "Project ID")}{input("CEO_FRIDAY_RUNTIME_PROVIDER_BASE_URL", "Provider Base URL", "url")}{input("CEO_FRIDAY_RUNTIME_PROVIDER_MODEL", "Provider model")}<SecretField id="friday-runtime-ticket" label="Runtime ticket" configured value="" onChange={(next) => setDraft({ ...draft, CEO_FRIDAY_RUNTIME_TICKET: next })} /><SecretField id="friday-session-token" label="Session token" configured value="" onChange={(next) => setDraft({ ...draft, CEO_FRIDAY_SESSION_TOKEN: next })} /></div></section></div><div className="runtime-save-bar"><span className="muted">敏感值不会回填到页面。</span><SaveBar state={saveState} /></div></form></SettingsCard>;
}

function SettingsContent({ section, payload, draft, setDraft, prompt, view, connector, auditRule, saveState }: { section: SettingsSection; payload: RecordValue; draft: RecordValue; setDraft: (value: RecordValue) => void; prompt: "developer" | "user"; view: "template" | "preview"; connector: string; auditRule: "template" | "consumer" | "audit"; saveState: "idle" | "saving" | "saved" | "error" }) {
  if (section === "status") return <StatusPanel />;
  if (section === "attention") return <AttentionPanel />;
  if (section === "prompts") return <PromptPanel payload={payload} prompt={prompt} view={view} draft={draft} setDraft={setDraft} saveState={saveState} />;
  if (section === "connectors") return <ConnectorPanel payload={payload} connector={connector} />;
  if (section === "agent-runtime") return <RuntimePanel payload={payload} draft={draft} setDraft={setDraft} saveState={saveState} />;
  if (section === "audit-rules") {
    const template = displayValue(draft.template ?? fieldsOf(payload).template);
    const preview = displayValue(record(payload.preview)[auditRule]);
    return <SettingsCard><h2>Audit Rules</h2><p className="muted">Audit Rules 先定义可配置模板，再分别查看 Consumer 和 Audit wrapper 的最终渲染结果。Template 中的 <code>{"{{principal}}"}</code> 会使用当前配置显示名替换。</p><p className="muted">当前视图：{auditRule}</p><div className="settings-pill-row" role="tablist" aria-label="Audit Rule sections">{(["template", "consumer", "audit"] as const).map((key) => <NavLink key={key} role="tab" aria-selected={auditRule === key} className={auditRule === key ? "active" : ""} to={`/settings?tab=audit-rules&rule=${key}&view=${key === "template" ? "template" : "preview"}`}>{key[0].toUpperCase() + key.slice(1)}</NavLink>)}</div><div className="settings-pill-row settings-view-row" role="tablist" aria-label="Audit Rule view"><NavLink role="tab" aria-selected={auditRule === "template"} className={auditRule === "template" ? "active" : ""} to={`/settings?tab=audit-rules&rule=${auditRule}&view=template`}>Template</NavLink><NavLink role="tab" aria-selected={auditRule !== "template"} className={auditRule !== "template" ? "active" : ""} to={`/settings?tab=audit-rules&rule=${auditRule}&view=preview`}>Rendered preview</NavLink></div>{auditRule === "template" ? <form onSubmit={(event) => event.preventDefault()}><TokenEditor id="audit-rules-template" label="Configurable rules" value={template} onChange={(next) => setDraft({ ...draft, template: next })} rows={16} /><SaveBar state={saveState} /></form> : <pre className="prompt-preview">{preview || template}</pre>}</SettingsCard>;
  }
  if (section === "configuration") { const groups = Array.isArray(payload.groups) ? payload.groups.map(record) : []; const compatibility = Array.isArray(payload.compatibility) ? payload.compatibility.map(record) : []; return <SettingsCard><h2>Configuration</h2><p className="muted">所有影响服务行为的环境配置统一保存在 <code>.env</code>；每个配置项的说明和当前值保持在同一行。</p><form onSubmit={(event) => event.preventDefault()}><ConfigTable groups={groups} compatibility={compatibility} draft={draft} setDraft={setDraft} /><SaveBar state={saveState} /></form></SettingsCard>; }
  return <SettingsCard><h2>Producer 路由配置</h2><p className="muted">这里展示 producer 如何把钉钉消息变成 reply task。实际生效值请在 Configuration 中查看。</p><dl className="info-list">{Object.entries(fieldsOf(payload)).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{displayValue(value)}</dd></div>)}</dl></SettingsCard>;
}

export function SettingsPage() {
  const [params] = useSearchParams();
  const rawSection = params.get("tab") || "status";
  const section = (rawSection === "config" ? "configuration" : rawSection) as SettingsSection;
  const prompt = params.get("prompt") === "user" ? "user" : "developer";
  const view = params.get("view") === "preview" ? "preview" : "template";
  const connector = params.get("connector") || "dingtalk";
  const auditRule = (["template", "consumer", "audit"] as const).includes(params.get("rule") as never) ? params.get("rule") as "template" | "consumer" | "audit" : "template";
  const [payload, setPayload] = useState<RecordValue | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const [draft, setDraft] = useState<RecordValue>({});
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  useEffect(() => {
    if (section === "status" || section === "attention") return;
    const controller = new AbortController(); setState("loading"); setSaveState("idle");
    getSettings(section, controller.signal).then((response) => { setPayload(response.item); setDraft(fieldsOf(response.item)); setSnapshot(response.meta.snapshot_at); setState("ready"); setError(""); }).catch((reason: unknown) => { if (controller.signal.aborted) return; setError(reason instanceof Error ? reason.message : "加载失败"); setState("error"); });
    return () => controller.abort();
  }, [section]);
  async function save() { if (!payload) return; setSaveState("saving"); try { const fields = section === "prompts" ? { template: displayValue(draft[`${prompt}_template`]) } : section === "audit-rules" ? { template: displayValue(draft.template) } : draft; await saveSettings(section, fields, section === "prompts" ? { prompt } : {}); setSaveState("saved"); } catch { setSaveState("error"); } }
  const label = sections.find(([key]) => key === section)?.[1] || "Settings";
  const content = state === "error" ? <SettingsCard><div className="page-state page-state-error" role="alert">{error}</div></SettingsCard> : state === "loading" && !payload && section !== "status" && section !== "attention" ? <SettingsCard><div className="page-state" role="status">正在加载…</div></SettingsCard> : <SettingsContent section={section} payload={payload || {}} draft={draft} setDraft={setDraft} prompt={prompt} view={view} connector={connector} auditRule={auditRule} saveState={saveState} />;
  const actions = section === "status" || section === "attention" ? undefined : <SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} />;
  return <ConsolePageLayout title={label} actions={actions}><div className="settings-layout-react"><SectionNav section={section} /><div className="settings-content" onSubmit={(event) => { if ((event.target as HTMLFormElement).tagName === "FORM") { event.preventDefault(); void save(); } }}>{content}</div></div></ConsolePageLayout>;
}
