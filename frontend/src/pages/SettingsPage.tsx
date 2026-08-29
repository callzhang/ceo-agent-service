import { useEffect, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { displayValue, getSettings, listAttention, listWechat, listWechatTargets, saveSettings, saveWechatReplyScope, type WechatScopeTarget } from "../api/console";
import { TokenEditor } from "../components/editor/TokenEditor";
import { SecretField } from "../components/forms/SecretField";
import { SearchField } from "../components/filters/SearchField";
import { SelectField } from "../components/filters/SelectField";
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

function SectionNav({ section, attentionCount }: { section: SettingsSection; attentionCount: number }) {
  return <nav className="settings-nav-react" aria-label="Settings navigation">
    {sections.map(([key, label]) => <Link key={key} to={`/settings?tab=${key}`} className={section === key ? "active" : ""} aria-current={section === key ? "page" : undefined}>{label}{key === "attention" && attentionCount > 0 && <span className="settings-nav-badge" aria-label={`${attentionCount} 个未解决问题`}>{attentionCount > 99 ? "99+" : attentionCount}</span>}</Link>)}
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

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function highlightRenderedPreview(template: string, preview: string): ReactNode {
  const parts = template.split(/(\{\{[^{}]+\}\})/g);
  if (!parts.some((part) => /^\{\{[^{}]+\}\}$/.test(part))) return preview;
  const groups: string[] = [];
  const pattern = parts.map((part) => {
    if (/^\{\{[^{}]+\}\}$/.test(part)) {
      groups.push(part);
      return "([\\s\\S]*?)";
    }
    return escapeRegExp(part);
  }).join("");
  const match = new RegExp(`^${pattern}$`).exec(preview);
  if (!match) return preview;
  let groupIndex = 1;
  return parts.map((part, index) => {
    if (!/^\{\{[^{}]+\}\}$/.test(part)) return <span key={index}>{part}</span>;
    const value = match[groupIndex++] || "";
    return <mark key={index} title={`运行时变量 ${part}`}>{value}</mark>;
  });
}

function InfoPanel({ payload }: { payload: RecordValue }) {
  const sections = Array.isArray(payload.sections) ? payload.sections.map(record) : [];
  const notes = Array.isArray(payload.notes) ? payload.notes.map(displayValue).filter(Boolean) : [];
  return <SettingsCard>
    <div className="settings-card-heading"><div><h2>Producer 路由配置</h2><p className="muted">这里展示 producer 如何把钉钉消息变成 reply task。实际生效值请在 Configuration 中查看。</p></div><span className="settings-path">Runtime logic</span></div>
    {notes.length > 0 && <div className="info-notes" aria-label="运行说明">{notes.map((note, index) => <p key={index}>{note}</p>)}</div>}
    <div className="info-logic-list" aria-label="Producer 路由说明">{sections.map((section, sectionIndex) => <section className="info-logic-section" key={displayValue(section.title) || sectionIndex}><h3>{displayValue(section.title)}</h3><dl>{Array.isArray(section.items) && section.items.map((rawItem, itemIndex) => { const item = record(rawItem); return <div key={displayValue(item.label) || itemIndex}><dt>{displayValue(item.label)}</dt><dd><SummaryDescription value={displayValue(item.description)} /></dd></div>; })}</dl></section>)}</div>
    <details className="settings-collapse"><summary>当前运行上下文</summary><dl className="info-list">{Object.entries(fieldsOf(payload)).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{displayValue(value)}</dd></div>)}</dl></details>
  </SettingsCard>;
}

function PromptPanel({ payload, prompt, view, draft, setDraft, saveState }: { payload: RecordValue; prompt: "developer" | "user"; view: "template" | "preview"; draft: RecordValue; setDraft: (value: RecordValue) => void; saveState: "idle" | "saving" | "saved" | "error" }) {
  const templateKey = `${prompt}_template`;
  const value = displayValue(draft[templateKey] ?? fieldsOf(payload)[templateKey]);
  const preview = displayValue(record(payload.preview)[prompt]);
  return <SettingsCard>
    <div className="settings-card-heading"><div><h2>Prompts</h2><p className="muted">{prompt === "developer" ? "Developer Prompt" : "User Prompt"} · 模板由服务端读取，预览使用当前运行时上下文。</p></div><span className="settings-path">{prompt === "developer" ? "developer_prompt.md" : "user_prompt.md"}</span></div>
    <div className="settings-pill-row" role="tablist" aria-label="Prompt sections"><Link role="tab" aria-selected={prompt === "developer"} className={prompt === "developer" ? "active" : ""} to="/settings?tab=prompts&prompt=developer&view=template">Developer Prompt</Link><Link role="tab" aria-selected={prompt === "user"} className={prompt === "user" ? "active" : ""} to="/settings?tab=prompts&prompt=user&view=template">User Prompt</Link></div>
    <div className="settings-pill-row settings-view-row" role="tablist" aria-label="Prompt view"><Link role="tab" aria-selected={view === "template"} className={view === "template" ? "active" : ""} to={`/settings?tab=prompts&prompt=${prompt}&view=template`}>Template</Link><Link role="tab" aria-selected={view === "preview"} className={view === "preview" ? "active" : ""} to={`/settings?tab=prompts&prompt=${prompt}&view=preview`}>Rendered preview</Link></div>
    <div id="prompt-panel" role="tabpanel" aria-label={view === "template" ? "Template" : "Rendered preview"}>{view === "template" ? <form onSubmit={(event) => event.preventDefault()}><TokenEditor id="prompt-template" label="Template" value={value} onChange={(next) => setDraft({ ...draft, [templateKey]: next })} rows={18} /><p className="muted prompt-runtime-note">运行时注入变量：<code>{"{{principal}}"}</code> <code>{"{{conversation}}"}</code>。这些变量不需要手动填写。</p><SaveBar state={saveState} /></form> : <><p className="muted">Rendered preview · {prompt === "user" ? "sample runtime context" : "current configuration"}</p><pre className="prompt-preview">{preview ? highlightRenderedPreview(value, preview) : "未提供预览"}</pre></>}</div>
  </SettingsCard>;
}

function scopeKey(target: WechatScopeTarget) {
  return `${target.target_type}:${target.target_id}`;
}

function toWechatTarget(value: unknown): WechatScopeTarget | null {
  const item = record(value);
  const targetType = item.target_type === "group" || item.target_type === "direct" ? item.target_type : null;
  const targetId = typeof item.target_id === "string" ? item.target_id : "";
  if (!targetType || !targetId) return null;
  const triggerMode = targetType === "group" ? "mention_current_account" : "every_inbound_text";
  return {
    account_id: typeof item.account_id === "string" ? item.account_id : undefined,
    target_type: targetType,
    target_id: targetId,
    display_name: typeof item.display_name === "string" && item.display_name.trim() ? item.display_name : targetId,
    trigger_mode: triggerMode,
    conversation_id: typeof item.conversation_id === "string" && item.conversation_id ? item.conversation_id : targetId,
    enabled: item.enabled !== false,
  };
}

function WechatTargetRow({ target, selected, onToggle }: { target: WechatScopeTarget; selected: boolean; onToggle: (target: WechatScopeTarget) => void }) {
  const kind = target.target_type === "group" ? "群聊" : "好友";
  return <label className="wechat-target-row">
    <input type="checkbox" checked={selected} onChange={() => onToggle(target)} />
    <span className="wechat-target-copy"><strong>{target.display_name}</strong><small>{kind} · {target.target_id}</small></span>
  </label>;
}

function WechatReplyScopePanel() {
  const [selected, setSelected] = useState<Map<string, WechatScopeTarget>>(new Map());
  const [savedKeys, setSavedKeys] = useState<Set<string>>(new Set());
  const [targets, setTargets] = useState<WechatScopeTarget[]>([]);
  const [accountId, setAccountId] = useState("");
  const [search, setSearch] = useState("");
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [searchState, setSearchState] = useState<"idle" | "loading" | "error">("idle");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState("");
  const [searchError, setSearchError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setLoadState("loading");
    listWechat("/api/console/wechat/conversations", controller.signal).then((response) => {
      const scopes = response.items.map(toWechatTarget).filter((item): item is WechatScopeTarget => item !== null);
      const chosen = scopes.filter((item) => item.enabled !== false);
      setAccountId(scopes.find((item) => item.account_id)?.account_id || "");
      setSelected(new Map(chosen.map((item) => [scopeKey(item), item])));
      setSavedKeys(new Set(chosen.map(scopeKey)));
      setTargets(chosen);
      setLoadState("ready");
      setError("");
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : "回复范围加载失败");
      setLoadState("error");
    });
    return () => controller.abort();
  }, []);

  const selectedTargets = Array.from(selected.values());
  const dirty = selected.size !== savedKeys.size || selectedTargets.some((item) => !savedKeys.has(scopeKey(item)));

  function toggleTarget(target: WechatScopeTarget) {
    setSaveState("idle");
    setSelected((current) => {
      const next = new Map(current);
      const key = scopeKey(target);
      if (next.has(key)) next.delete(key); else next.set(key, { ...target, enabled: true });
      return next;
    });
  }

  async function searchTargets() {
    setSearchState("loading");
    setSearchError("");
    try {
      const response = await listWechatTargets({ query: search.trim(), kind: "all", limit: 50 });
      setAccountId((current) => current || response.account_id);
      const next = response.items.map(toWechatTarget).filter((item): item is WechatScopeTarget => item !== null);
      setTargets(next);
      setSearchState("idle");
    } catch (reason: unknown) {
      setSearchError(reason instanceof Error ? reason.message : "联系人读取失败");
      setSearchState("error");
    }
  }

  async function saveScope() {
    if (!accountId) {
      setSaveState("error");
      setError("尚未连接可用的微信账号，请先在 Tutorial 完成微信连接。");
      return;
    }
    setSaveState("saving");
    setError("");
    try {
      await saveWechatReplyScope(accountId, selectedTargets);
      setSavedKeys(new Set(selected.keys()));
      setSaveState("saved");
    } catch (reason: unknown) {
      setSaveState("error");
      setError(reason instanceof Error ? reason.message : "回复范围保存失败");
    }
  }

  return <div className="wechat-scope-panel" id="wechat-reply-scope-panel">
    <div className="wechat-scope-heading"><div><h3>微信自动回复对象</h3><p className="muted">只处理这里明确选中的好友和群聊。好友触发模式为接收任意消息，群聊触发模式为提及当前账号。</p></div><span className={`wechat-scope-state ${dirty ? "is-dirty" : ""}`} role="status">{dirty ? "有未保存更改" : saveState === "saved" ? "已保存" : "已同步"}</span></div>
    {loadState === "loading" && <div className="page-state" role="status">正在加载已保存的回复范围…</div>}
    {loadState === "error" && <div className="page-state page-state-error" role="alert">{error}</div>}
    {loadState === "ready" && <>
      <div className="wechat-selected-block"><div className="wechat-section-label">当前回复范围 <span>{selectedTargets.length} 个对象</span></div>{selectedTargets.length ? <div className="wechat-target-list">{selectedTargets.map((target) => <WechatTargetRow key={scopeKey(target)} target={target} selected onToggle={toggleTarget} />)}</div> : <p className="wechat-empty">尚未选择对象。搜索并勾选后，点击“保存回复范围”。</p>}</div>
      <div className="wechat-target-picker"><div className="wechat-section-label">添加或调整对象</div><div className="wechat-search-row"><SearchField id="wechat-target-search" label="搜索好友或群聊" value={search} placeholder="按名称或 ID 搜索" onChange={(value) => { setSaveState("idle"); setSearch(value); }} onClear={() => { setSaveState("idle"); setSearch(""); }} /><button type="button" className="secondary-button" onClick={() => void searchTargets()} disabled={searchState === "loading"}>{searchState === "loading" ? "搜索中…" : "搜索"}</button></div>{searchError && <p className="field-error" role="alert">{searchError}</p>}{targets.length > 0 && <div className="wechat-target-list">{targets.map((target) => <WechatTargetRow key={scopeKey(target)} target={target} selected={selected.has(scopeKey(target))} onToggle={toggleTarget} />)}</div>}{!targets.length && <p className="wechat-empty">点击搜索读取可用的微信好友和群聊。</p>}</div>
      <div className="wechat-scope-actions"><button type="button" className="primary-button" onClick={() => void saveScope()} disabled={!dirty || saveState === "saving"}>{saveState === "saving" ? "保存中…" : "保存回复范围"}</button>{saveState === "saved" && <span className="save-success" role="status">回复范围已保存</span>}{saveState === "error" && <span className="save-error" role="alert">保存失败，当前选择仍保留</span>}</div>
    </>}
    {error && loadState === "ready" && <p className="field-error" role="alert">{error}</p>}
  </div>;
}

function ConnectorPanel({ payload, connector }: { payload: RecordValue; connector: string }) {
  const item = record(payload[connector]);
  const state = displayValue(item.state || item.status || "unknown");
  const commands = Array.isArray(item.commands) ? item.commands : [];
  if (connector === "wechat") return <SettingsCard><div className="settings-card-heading"><div><h2>Connectors</h2><p className="muted">WeChat 自动回复范围在当前页面直接维护。</p></div></div><div className="settings-pill-row" role="tablist" aria-label="Connector sections"><ConnectorTab value="dingtalk" label="DingTalk" active={false} /><ConnectorTab value="lark" label="Lark" active={false} /><ConnectorTab value="wechat" label="WeChat" active /></div><div id="connector-panel" role="tabpanel" aria-label="WeChat connector"><div className="connector-heading"><h3>WeChat connector</h3></div><p className="muted">连接和能力检查请在 Tutorial 完成；下方回复范围编辑不会自动发送消息。</p><WechatReplyScopePanel /></div></SettingsCard>;
  return <SettingsCard><div className="settings-card-heading"><div><h2>Connectors</h2><p className="muted">External connector status, live probes, and local CLI readiness.</p></div><StatusBadge value={state} /></div><div className="settings-pill-row" role="tablist" aria-label="Connector sections"><ConnectorTab value="dingtalk" label="DingTalk" active={connector === "dingtalk"} /><ConnectorTab value="lark" label="Lark" active={connector === "lark"} /><ConnectorTab value="wechat" label="WeChat" active={false} /></div><div id="connector-panel" role="tabpanel" aria-label={`${connector} connector`}><div className="connector-heading"><h3>{connector === "dingtalk" ? "DingTalk connector" : "Lark connector"}</h3><StatusBadge value={state} /></div><p className="muted">只显示当前连接器的 readiness、live probe 和登录状态。</p><div className="connector-state-grid"><StateItem label="Reason" value={displayValue(item.reason_code)} /><StateItem label="Login" value={displayValue(item.login || "not requested")} /><StateItem label="Last success" value={displayValue(item.last_success || (state === "ready" ? "本次检查" : "尚无成功记录"))} /><StateItem label="Detail" value={displayValue(item.detail || "没有额外说明。")} /></div><h4>Checks</h4><div className="connector-commands">{commands.length ? commands.map((command, index) => <code key={index}>{displayValue(command)}</code>) : <span className="muted">未执行</span>}</div></div></SettingsCard>;
}

function ConnectorTab({ value, label, active }: { value: string; label: string; active: boolean }) {
  return <Link role="tab" aria-selected={active} className={active ? "active" : ""} to={`/settings?tab=connectors&connector=${value}`}>{label}</Link>;
}

function StateItem({ label, value }: { label: string; value: string }) { return <div className="connector-state-item"><span>{label}</span><strong>{value}</strong></div>; }

const CODEX_MODEL_OPTIONS = [
  { value: "gpt-5.5", label: "GPT-5.5" },
  { value: "gpt-5.6-sol", label: "GPT-5.6 Sol" },
  { value: "gpt-5.6-terra", label: "GPT-5.6 Terra" },
  { value: "gpt-5.6-luna", label: "GPT-5.6 Luna" },
];

const COMPATIBLE_MODEL_GROUPS = [
  { label: "OpenAI", options: CODEX_MODEL_OPTIONS },
  { label: "MiniMax", options: [
    { value: "MiniMax-M2.5", label: "MiniMax M2.5" },
    { value: "MiniMax-M2.1", label: "MiniMax M2.1" },
    { value: "MiniMax-M2", label: "MiniMax M2" },
  ] },
  { label: "Qwen", options: [
    { value: "qwen3-max", label: "Qwen3 Max" },
    { value: "qwen3-coder-plus", label: "Qwen3 Coder Plus" },
    { value: "qwen-plus", label: "Qwen Plus" },
    { value: "qwen-turbo", label: "Qwen Turbo" },
  ] },
  { label: "智谱", options: [
    { value: "glm-5", label: "GLM-5" },
    { value: "glm-4.7", label: "GLM-4.7" },
    { value: "glm-4.6", label: "GLM-4.6" },
    { value: "glm-4.5", label: "GLM-4.5" },
  ] },
];

function rawValue(draft: RecordValue, payload: RecordValue, key: string) {
  const candidate = draft[key] ?? fieldsOf(payload)[key];
  return typeof candidate === "string" ? candidate : "";
}

function modelOptions(groups: Array<{ label: string; options: Array<{ value: string; label: string }> }>, current: string) {
  const options = groups.flatMap((group) => group.options);
  if (current && !options.some((option) => option.value === current)) {
    return [{ label: "当前配置", options: [{ value: current, label: `当前配置：${current}` }] }, ...groups];
  }
  return groups;
}

function ModelSelect({ id, label, value, groups, onChange }: { id: string; label: string; value: string; groups: Array<{ label: string; options: Array<{ value: string; label: string }> }>; onChange: (value: string) => void }) {
  return <SelectField id={id} label={label} value={value} onChange={onChange}><option value="">请选择模型</option>{modelOptions(groups, value).map((group) => <optgroup key={group.label} label={group.label}>{group.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</optgroup>)}</SelectField>;
}

function RuntimePanel({ payload, draft, setDraft, saveState }: { payload: RecordValue; draft: RecordValue; setDraft: (value: RecordValue) => void; saveState: "idle" | "saving" | "saved" | "error" }) {
  const value = (key: string) => displayValue(draft[key] ?? fieldsOf(payload)[key]);
  const raw = (key: string) => rawValue(draft, payload, key);
  const input = (key: string, label: string, type = "text") => <label className="runtime-field"><span>{label}</span><input type={type} value={value(key)} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} /></label>;
  const update = (key: string, next: string) => setDraft({ ...draft, [key]: next });
  return <SettingsCard><div className="settings-card-heading"><div><p className="eyebrow">Settings / Agent Runtime</p><h2>Agent Runtime</h2><p className="muted">集中管理模型路由和 fallback 凭据。保存后重启主服务，运行中的 worker 才会使用新配置。</p></div><span className="settings-path">.env backed</span></div><div className="runtime-overview"><StateItem label="Primary route" value="Codex OAuth" /><StateItem label="Model" value={value("CEO_CODEX_MODEL")} /><StateItem label="API fallback" value={value("CEO_AGENT_RUNTIME_ROUTES").includes("codex_api") ? "Active" : "Not active"} /><StateItem label="Friday Runtime" value={value("CEO_AGENT_RUNTIME_ROUTES").includes("friday_runtime") ? "Active" : "Not active"} /></div><form onSubmit={(event) => event.preventDefault()}><div className="runtime-card-grid"><section className="runtime-card"><div className="runtime-card-head"><div><h3>Codex OAuth</h3><p>默认的本机 OAuth 路由</p></div><StatusBadge value="Active" /></div><div className="runtime-fields"><ModelSelect id="codex-model" label="Model" value={raw("CEO_CODEX_MODEL")} groups={[{ label: "Codex / OpenAI", options: CODEX_MODEL_OPTIONS }]} onChange={(next) => update("CEO_CODEX_MODEL", next)} />{input("CEO_CODEX_MODEL_REASONING_EFFORT", "Thinking strength")}</div></section><section className="runtime-card"><div className="runtime-card-head"><div><h3>Codex API fallback</h3><p>OAuth 不可用时的备用路由</p></div><StatusBadge value={value("CEO_AGENT_RUNTIME_ROUTES").includes("codex_api") ? "Active" : "Not active"} /></div><div className="runtime-fields">{input("CEO_CODEX_API_BASE_URL", "API Base URL", "url")}<ModelSelect id="codex-api-model" label="Fallback model" value={raw("CEO_CODEX_API_MODEL")} groups={COMPATIBLE_MODEL_GROUPS} onChange={(next) => update("CEO_CODEX_API_MODEL", next)} /><SecretField id="codex-api-token" label="API Token" configured={Boolean(raw("CEO_CODEX_API_KEY"))} value={raw("CEO_CODEX_API_KEY")} onChange={(next) => update("CEO_CODEX_API_KEY", next)} /></div></section><section className="runtime-card runtime-card-wide"><div className="runtime-card-head"><div><h3>Friday Runtime</h3><p>本机 Friday Runtime 服务和 provider 凭据</p></div><StatusBadge value={value("CEO_AGENT_RUNTIME_ROUTES").includes("friday_runtime") ? "Active" : "Not active"} /></div><div className="runtime-fields">{input("CEO_FRIDAY_RUNTIME_BASE_URL", "Runtime Base URL", "url")}{input("CEO_FRIDAY_RUNTIME_PROJECT_ID", "Project ID")}{input("CEO_FRIDAY_RUNTIME_PROVIDER_BASE_URL", "Provider Base URL", "url")}<ModelSelect id="friday-provider-model" label="Provider model" value={raw("CEO_FRIDAY_RUNTIME_PROVIDER_MODEL")} groups={COMPATIBLE_MODEL_GROUPS} onChange={(next) => update("CEO_FRIDAY_RUNTIME_PROVIDER_MODEL", next)} /><SecretField id="friday-provider-api-token" label="Provider API Token" configured={Boolean(raw("CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY"))} value={raw("CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY")} onChange={(next) => update("CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY", next)} /><SecretField id="friday-runtime-ticket" label="Runtime ticket" configured={Boolean(raw("CEO_FRIDAY_RUNTIME_TICKET"))} value={raw("CEO_FRIDAY_RUNTIME_TICKET")} onChange={(next) => update("CEO_FRIDAY_RUNTIME_TICKET", next)} /><SecretField id="friday-session-token" label="Session token" configured={Boolean(raw("CEO_FRIDAY_SESSION_TOKEN"))} value={raw("CEO_FRIDAY_SESSION_TOKEN")} onChange={(next) => update("CEO_FRIDAY_SESSION_TOKEN", next)} /></div></section></div><div className="runtime-save-bar"><span className="muted">已保存凭据会回填；可直接编辑后保存。</span><SaveBar state={saveState} /></div></form></SettingsCard>;
}

function SettingsContent({ section, payload, draft, setDraft, prompt, view, connector, auditRule, saveState, onAttentionCountChange }: { section: SettingsSection; payload: RecordValue; draft: RecordValue; setDraft: (value: RecordValue) => void; prompt: "developer" | "user"; view: "template" | "preview"; connector: string; auditRule: "template" | "consumer" | "audit"; saveState: "idle" | "saving" | "saved" | "error"; onAttentionCountChange: (count: number) => void }) {
  if (section === "status") return <StatusPanel />;
  if (section === "attention") return <AttentionPanel onCountChange={onAttentionCountChange} />;
  if (section === "prompts") return <PromptPanel payload={payload} prompt={prompt} view={view} draft={draft} setDraft={setDraft} saveState={saveState} />;
  if (section === "connectors") return <ConnectorPanel payload={payload} connector={connector} />;
  if (section === "agent-runtime") return <RuntimePanel payload={payload} draft={draft} setDraft={setDraft} saveState={saveState} />;
  if (section === "audit-rules") {
    const template = displayValue(draft.template ?? fieldsOf(payload).template);
    const preview = displayValue(record(payload.preview)[auditRule]);
    const ruleLabels = { template: "Template", consumer: "Consumer", audit: "Audit" } as const;
    const viewLabel = view === "template" ? "Template" : "Rendered preview";
    const panelLabel = view === "template" ? `${ruleLabels[auditRule]} template` : `${ruleLabels[auditRule]} rendered preview`;
    const templatePanel = auditRule === "template"
      ? <form onSubmit={(event) => event.preventDefault()}><TokenEditor id="audit-rules-template" label="Configurable rules" value={template} onChange={(next) => setDraft({ ...draft, template: next })} rows={16} /><SaveBar state={saveState} /></form>
      : <><p className="muted">当前 tab 使用同一份 Audit Rules template；切换到 Template tab 编辑。</p><pre className="prompt-preview">{template || "未提供模板"}</pre></>;
    const previewPanel = <><p className="muted">Rendered preview · current configuration</p><pre className="prompt-preview">{preview ? highlightRenderedPreview(template, preview) : "未提供预览"}</pre></>;
    return <SettingsCard><h2>Audit Rules</h2><p className="muted">Audit Rules 先定义可配置模板，再分别查看 Consumer 和 Audit wrapper 的最终渲染结果。Template 中的 <code>{"{{principal}}"}</code> 会使用当前配置显示名替换。</p><p className="muted">当前规则：{ruleLabels[auditRule]} · 当前视图：{viewLabel}</p><div className="settings-control-group"><span className="settings-control-label">规则类型</span><div className="settings-pill-row" role="tablist" aria-label="Audit Rule sections">{(["template", "consumer", "audit"] as const).map((key) => <Link key={key} role="tab" aria-selected={auditRule === key} className={auditRule === key ? "active" : ""} to={`/settings?tab=audit-rules&rule=${key}&view=${view}`}>{ruleLabels[key]}</Link>)}</div></div><div className="settings-control-group"><span className="settings-control-label">查看方式</span><div className="settings-pill-row settings-view-row" role="tablist" aria-label="Audit Rule view"><Link role="tab" aria-selected={view === "template"} className={view === "template" ? "active" : ""} to={`/settings?tab=audit-rules&rule=${auditRule}&view=template`}>Template</Link><Link role="tab" aria-selected={view === "preview"} className={view === "preview" ? "active" : ""} to={`/settings?tab=audit-rules&rule=${auditRule}&view=preview`}>Rendered preview</Link></div></div><div id="audit-rules-panel" role="tabpanel" aria-label={panelLabel}>{view === "template" ? templatePanel : previewPanel}</div></SettingsCard>;
  }
  if (section === "configuration") { const groups = Array.isArray(payload.groups) ? payload.groups.map(record) : []; const compatibility = Array.isArray(payload.compatibility) ? payload.compatibility.map(record) : []; return <SettingsCard><h2>Configuration</h2><p className="muted">所有影响服务行为的环境配置统一保存在 <code>.env</code>；每个配置项的说明和当前值保持在同一行。</p><form onSubmit={(event) => event.preventDefault()}><ConfigTable groups={groups} compatibility={compatibility} draft={draft} setDraft={setDraft} /><SaveBar state={saveState} /></form></SettingsCard>; }
  return <InfoPanel payload={payload} />;
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
  const [draft, setDraft] = useState<RecordValue>({});
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [attentionCount, setAttentionCount] = useState(0);
  useEffect(() => {
    if (section === "attention") return;
    let active = true;
    listAttention().then((page) => {
      if (active) setAttentionCount(page.items.reduce((total, item) => total + Math.max(0, Number(item.count || 0)), 0));
    }).catch(() => {
      if (active) setAttentionCount(0);
    });
    return () => { active = false; };
  }, [section]);
  useEffect(() => {
    if (section === "status" || section === "attention") return;
    const controller = new AbortController(); setState("loading"); setSaveState("idle");
    getSettings(section, controller.signal).then((response) => { setPayload(response.item); setDraft(fieldsOf(response.item)); setState("ready"); setError(""); }).catch((reason: unknown) => { if (controller.signal.aborted) return; setError(reason instanceof Error ? reason.message : "加载失败"); setState("error"); });
    return () => controller.abort();
  }, [section]);
  async function save() { if (!payload) return; setSaveState("saving"); try { const fields = section === "prompts" ? { template: displayValue(draft[`${prompt}_template`]) } : section === "audit-rules" ? { template: displayValue(draft.template) } : draft; await saveSettings(section, fields, section === "prompts" ? { prompt } : {}); setSaveState("saved"); } catch { setSaveState("error"); } }
  const content = state === "error" ? <SettingsCard><div className="page-state page-state-error" role="alert">{error}</div></SettingsCard> : state === "loading" && !payload && section !== "status" && section !== "attention" ? <SettingsCard><div className="page-state" role="status">正在加载…</div></SettingsCard> : <SettingsContent section={section} payload={payload || {}} draft={draft} setDraft={setDraft} prompt={prompt} view={view} connector={connector} auditRule={auditRule} saveState={saveState} onAttentionCountChange={setAttentionCount} />;
  return <main className="console-page settings-page" aria-labelledby="settings-page-title"><h1 id="settings-page-title" className="sr-only">Settings</h1><div className="settings-layout-react"><SectionNav section={section} attentionCount={attentionCount} /><div className="settings-content" onSubmit={(event) => { if ((event.target as HTMLFormElement).tagName === "FORM") { event.preventDefault(); void save(); } }}>{content}</div></div></main>;
}
