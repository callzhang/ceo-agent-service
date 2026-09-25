import { Check, GripVertical, Pencil, Plus, RotateCcw, Trash2 } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { createEmailAccount, displayValue, getSettings, getSkillDetail, getSkillFeatures, listAttention, listEmailAccounts, listWechat, listWechatTargets, saveSettings, saveSkill as saveSkillApi, saveWechatReplyScope, startConnectorLogin, testEmailAccount, toggleSkillFeature, updateEmailAccount, type EmailAccountItem, type EmailAccountPayload, type ProjectSkill, type SkillDetail, type SkillFeature, type WechatScopeTarget } from "../api/console";
import { TokenEditor } from "../components/editor/TokenEditor";
import { SecretField } from "../components/forms/SecretField";
import { SearchField } from "../components/filters/SearchField";
import { SelectField } from "../components/filters/SelectField";
import { StatusBadge } from "../components/status/StatusBadge";
import { AttentionPanel } from "./AttentionPage";
import { StatusPanel } from "./StatusPage";
import { ManagedSkillsPanel } from "../components/settings/ManagedSkillsPanel";
import { McpPanel } from "../components/settings/McpPanel";

type RecordValue = Record<string, unknown>;
type SettingsSection = "status" | "info" | "configuration" | "agent-runtime" | "prompts" | "connectors" | "audit-rules" | "skills" | "mcp" | "attention";
type PromptKind = "developer" | "user" | "profile";

const sections: Array<[SettingsSection, string]> = [
  ["status", "Status"], ["info", "Info"], ["configuration", "Configuration"], ["agent-runtime", "Agent Runtime"],
  ["prompts", "Prompts"], ["connectors", "Connectors"], ["audit-rules", "Audit Rules"], ["skills", "Skills"], ["mcp", "MCP"], ["attention", "Attention"],
];

function record(value: unknown): RecordValue {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as RecordValue : {};
}

function fieldsOf(payload: RecordValue) {
  return record(payload.fields);
}

function SectionNav({ section, attentionCount }: { section: SettingsSection; attentionCount: number }) {
  const activeLink = useRef<HTMLAnchorElement>(null);
  useEffect(() => {
    if (typeof activeLink.current?.scrollIntoView === "function") {
      activeLink.current.scrollIntoView({ behavior: "auto", block: "nearest", inline: "center" });
    }
  }, [section]);
  return <nav className="settings-nav-react" aria-label="Settings navigation">
    {sections.map(([key, label]) => <Link key={key} ref={section === key ? activeLink : undefined} to={`/settings?tab=${key}`} className={section === key ? "active" : ""} aria-current={section === key ? "page" : undefined}>{label}{key === "attention" && attentionCount > 0 && <span className="settings-nav-badge" aria-label={`${attentionCount} 个未解决问题`}>{attentionCount > 99 ? "99+" : attentionCount}</span>}</Link>)}
  </nav>;
}

function SettingsCard({ children }: { children: ReactNode }) {
  return <section className="console-card settings-content-card">{children}</section>;
}

function SaveBar({ state, error }: { state: "idle" | "saving" | "saved" | "error"; error?: string }) {
  return <div className="settings-save-row"><button type="submit" name="settings-save" className="primary-button" disabled={state === "saving"}>{state === "saving" ? "保存中…" : "保存"}</button>{state === "saved" && <span className="save-success" role="status">已保存</span>}{state === "error" && <span className="save-error" role="alert">{error || "保存失败，草稿仍保留"}</span>}</div>;
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
  if (!parts.some((part) => part && !/^\{\{[^{}]+\}\}$/.test(part))) return preview;
  const pattern = parts.map((part) => {
    if (/^\{\{[^{}]+\}\}$/.test(part)) {
      return "([\\s\\S]*?)";
    }
    return escapeRegExp(part);
  }).join("");
  const match = new RegExp(pattern).exec(preview);
  if (!match) return preview;
  let groupIndex = 1;
  const highlighted = parts.map((part, index) => {
    if (!/^\{\{[^{}]+\}\}$/.test(part)) return <span key={index}>{part}</span>;
    const value = match[groupIndex++] || "";
    return <mark key={index} title={`运行时变量 ${part}`}>{value}</mark>;
  });
  const prefix = preview.slice(0, match.index);
  const suffix = preview.slice(match.index + match[0].length);
  return <>{prefix && <span>{prefix}</span>}{highlighted}{suffix && <span>{suffix}</span>}</>;
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

function PromptPanel({ payload, prompt, view, draft, setDraft, saveState, saveError }: { payload: RecordValue; prompt: PromptKind; view: "template" | "preview"; draft: RecordValue; setDraft: (value: RecordValue) => void; saveState: "idle" | "saving" | "saved" | "error"; saveError: string }) {
  const isProfile = prompt === "profile";
  const templateKey = isProfile ? "profile" : `${prompt}_template`;
  const rawTemplate = draft[templateKey] ?? fieldsOf(payload)[templateKey];
  const value = typeof rawTemplate === "string" ? rawTemplate : displayValue(rawTemplate);
  const preview = displayValue(record(payload.preview)[prompt]);
  const label = isProfile ? "Distilled work profile" : prompt === "developer" ? "Developer Prompt" : "User Prompt";
  const path = isProfile ? "work_profile.md" : prompt === "developer" ? "developer_prompt.md" : "user_prompt.md";
  const viewTabs = <div className="settings-pill-row settings-view-row" role="tablist" aria-label="Prompt view"><Link role="tab" aria-selected={view === "template"} className={view === "template" ? "active" : ""} to={`/settings?tab=prompts&prompt=${prompt}&view=template`}>Template</Link><Link role="tab" aria-selected={view === "preview"} className={view === "preview" ? "active" : ""} to={`/settings?tab=prompts&prompt=${prompt}&view=preview`}>Rendered preview</Link></div>;
  return <SettingsCard>
    <div className="settings-card-heading"><div><h2>Prompts</h2><p className="muted">{label} · {isProfile ? "在新的 Consumer 或 Audit 运行中追加到 Developer Prompt。" : "模板由服务端读取，预览使用当前运行时上下文。"}</p></div><span className="settings-path">{path}</span></div>
    <div className="settings-pill-row" role="tablist" aria-label="Prompt sections"><Link role="tab" aria-selected={prompt === "developer"} className={prompt === "developer" ? "active" : ""} to="/settings?tab=prompts&prompt=developer&view=template">Developer Prompt</Link><Link role="tab" aria-selected={prompt === "user"} className={prompt === "user" ? "active" : ""} to="/settings?tab=prompts&prompt=user&view=template">User Prompt</Link><Link role="tab" aria-selected={isProfile} className={isProfile ? "active" : ""} to="/settings?tab=prompts&prompt=profile&view=template">Distilled work profile</Link></div>
    <div className="prompt-editor-shell" id="prompt-panel" role="tabpanel" aria-label={view === "template" ? "Template" : "Rendered preview"}>{view === "template" ? <form onSubmit={(event) => event.preventDefault()}><div className="prompt-editor-toolbar">{viewTabs}</div><TokenEditor id="prompt-template" label="Template" value={value} onChange={(next) => setDraft({ ...draft, [templateKey]: next })} autoResize /><p className="muted prompt-runtime-note">{isProfile ? "保存后仅影响后续新建运行。" : <>运行时注入变量：<code>{"{{principal}}"}</code> <code>{"{{conversation}}"}</code>。这些变量不需要手动填写。</>}</p><SaveBar state={saveState} error={saveError} /></form> : <><div className="prompt-editor-toolbar">{viewTabs}</div><pre className="prompt-preview">{preview ? (isProfile ? preview : highlightRenderedPreview(value, preview)) : "未提供预览"}</pre></>}</div>
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
  const [hasSearched, setHasSearched] = useState(false);
  const [searchTotal, setSearchTotal] = useState(0);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [searchState, setSearchState] = useState<"idle" | "loading" | "error">("idle");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState("");
  const [searchError, setSearchError] = useState("");
  const [autoReplyEnabled, setAutoReplyEnabled] = useState<boolean | null>(null);
  const [autoReplyState, setAutoReplyState] = useState<"loading" | "ready" | "saving" | "error">("loading");
  const [autoReplyError, setAutoReplyError] = useState("");

  async function loadScope(signal?: AbortSignal) {
    setLoadState("loading");
    setError("");
    try {
      const response = await listWechat("/api/console/wechat/conversations", signal);
      if (signal?.aborted) return;
      const scopes = response.items.map(toWechatTarget).filter((item): item is WechatScopeTarget => item !== null);
      const chosen = scopes.filter((item) => item.enabled !== false);
      setAccountId(scopes.find((item) => item.account_id)?.account_id || "");
      setSelected(new Map(chosen.map((item) => [scopeKey(item), item])));
      setSavedKeys(new Set(chosen.map(scopeKey)));
      setTargets([]);
      setLoadState("ready");
    } catch (reason: unknown) {
      if (signal?.aborted) return;
      setError(reason instanceof Error ? reason.message : "回复范围加载失败");
      setLoadState("error");
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void loadScope(controller.signal);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    Promise.resolve(getSkillFeatures(controller.signal)).then((response) => {
      if (controller.signal.aborted) return;
      const feature = response.features.find((item) => item.feature_id === "wechat_auto_reply");
      if (!feature) throw new Error("微信自动回复功能不可用");
      setAutoReplyEnabled(feature.enabled);
      setAutoReplyState("ready");
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setAutoReplyError(reason instanceof Error ? reason.message : "自动回复状态加载失败");
      setAutoReplyState("error");
    });
    return () => controller.abort();
  }, []);

  const selectedTargets = Array.from(selected.values());
  const availableTargets = targets.filter((target) => !selected.has(scopeKey(target)));
  const dirty = selected.size !== savedKeys.size || selectedTargets.some((item) => !savedKeys.has(scopeKey(item)));
  const syncLabel = loadState === "loading" ? "正在同步" : loadState === "error" ? "加载失败" : dirty ? "有未保存更改" : saveState === "saved" ? "已保存" : "已同步";
  const syncClass = loadState === "error" ? "is-error" : dirty ? "is-dirty" : "";

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
      setSearchTotal(response.meta.total || next.length);
      setHasSearched(true);
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

  async function toggleAutoReply() {
    if (autoReplyEnabled === null || autoReplyState === "saving") return;
    const next = !autoReplyEnabled;
    setAutoReplyState("saving");
    setAutoReplyError("");
    try {
      const result = await toggleSkillFeature("wechat_auto_reply", next);
      setAutoReplyEnabled(result.enabled);
      setAutoReplyState("ready");
    } catch (reason: unknown) {
      setAutoReplyError(reason instanceof Error ? reason.message : "自动回复开关保存失败");
      setAutoReplyState("error");
    }
  }

  return <div className="wechat-scope-panel" id="wechat-reply-scope-panel">
    <div className="wechat-scope-heading"><div><h3>微信自动回复对象</h3><p className="muted">只处理这里明确选中的好友和群聊。好友触发模式为接收任意消息，群聊触发模式为提及当前账号。</p></div><span className={`wechat-scope-state ${syncClass}`} role="status" aria-label="回复范围同步状态">{syncLabel}</span></div>
    <section className="wechat-auto-reply-control" aria-labelledby="wechat-auto-reply-title">
      <div><h4 id="wechat-auto-reply-title">自动回复</h4><p className="muted">关闭后仍会保留微信读取和已选对象，但不会从新消息创建回复任务。已生成的待发送消息保持原状态。</p></div>
      <label className="email-account-switch"><span>{autoReplyEnabled ? "已开启" : "已关闭"}</span><input type="checkbox" role="switch" aria-label="启用微信自动回复" checked={autoReplyEnabled === true} disabled={autoReplyState === "loading" || autoReplyState === "saving" || autoReplyEnabled === null} onChange={() => void toggleAutoReply()} /></label>
    </section>
    {autoReplyState === "loading" && <p className="muted" role="status">正在读取自动回复状态…</p>}
    {autoReplyError && <p className="field-error" role="alert">{autoReplyError}</p>}
    {loadState === "loading" && <div className="page-state" role="status">正在加载已保存的回复范围…</div>}
    {loadState === "error" && <div className="page-state page-state-error wechat-load-error" role="alert"><span>{error}</span><button type="button" className="secondary-button" onClick={() => void loadScope()}>重试加载回复范围</button></div>}
    {loadState === "ready" && <>
      <div className="wechat-selected-block"><div className="wechat-section-label">当前回复范围 <span>{selectedTargets.length} 个对象</span></div>{selectedTargets.length ? <div className="wechat-target-list">{selectedTargets.map((target) => <WechatTargetRow key={scopeKey(target)} target={target} selected onToggle={toggleTarget} />)}</div> : <p className="wechat-empty">尚未选择对象。搜索并勾选后，点击“保存回复范围”。</p>}</div>
      <div className="wechat-target-picker"><div className="wechat-section-label">添加或调整对象</div><form className="wechat-search-row" onSubmit={(event) => { event.preventDefault(); void searchTargets(); }}><SearchField id="wechat-target-search" label="搜索好友或群聊" value={search} placeholder="按名称或 ID 搜索" onChange={(value) => { setSaveState("idle"); setSearch(value); setTargets([]); setSearchTotal(0); setHasSearched(false); setSearchError(""); }} onClear={() => { setSaveState("idle"); setSearch(""); setTargets([]); setSearchTotal(0); setHasSearched(false); setSearchError(""); }} /><button type="submit" className="secondary-button" disabled={searchState === "loading"}>{searchState === "loading" ? "搜索中…" : "搜索"}</button></form>{searchError && <p className="field-error" role="alert">{searchError}</p>}{hasSearched && availableTargets.length > 0 && <><p className="muted wechat-search-summary" role="status">共匹配 {searchTotal} 个，当前显示 {availableTargets.length} 个可添加对象。</p><div className="wechat-target-list">{availableTargets.map((target) => <WechatTargetRow key={scopeKey(target)} target={target} selected={false} onToggle={toggleTarget} />)}</div></>}{hasSearched && targets.length > 0 && !availableTargets.length && <p className="wechat-empty">共匹配 {searchTotal} 个，均已在当前回复范围。</p>}{hasSearched && !targets.length && <p className="wechat-empty">没有找到匹配的好友或群聊。</p>}{!hasSearched && <p className="wechat-empty">输入名称或 ID 后搜索；留空可浏览全部对象。</p>}</div>
      <div className="wechat-scope-actions"><button type="button" className="primary-button" onClick={() => void saveScope()} disabled={!dirty || saveState === "saving"}>{saveState === "saving" ? "保存中…" : "保存回复范围"}</button>{saveState === "saved" && <span className="save-success" role="status">回复范围已保存</span>}{saveState === "error" && <span className="save-error" role="alert">保存失败，当前选择仍保留</span>}</div>
    </>}
    {error && loadState === "ready" && <p className="field-error" role="alert">{error}</p>}
  </div>;
}

interface EmailAccountDraft {
  account_id: string;
  display_name: string;
  email_address: string;
  imap_host: string;
  imap_port: string;
  imap_tls: boolean;
  imap_username: string;
  imap_secret: string;
  imap_secret_configured: boolean;
  enabled: boolean;
  scan_folders: string;
  agent_lookback_days: number;
  model_lookback_days: number;
  scan_read_state: "unread" | "all";
}

const DEFAULT_AGENT_LOOKBACK_DAYS = 30;
const DEFAULT_MODEL_LOOKBACK_DAYS = 365;

function newEmailAccountDraft(): EmailAccountDraft {
  return {
    account_id: "",
    display_name: "",
    email_address: "",
    imap_host: "",
    imap_port: "993",
    imap_tls: true,
    imap_username: "",
    imap_secret: "",
    imap_secret_configured: false,
    enabled: true,
    scan_folders: "INBOX",
    agent_lookback_days: DEFAULT_AGENT_LOOKBACK_DAYS,
    model_lookback_days: DEFAULT_MODEL_LOOKBACK_DAYS,
    scan_read_state: "unread",
  };
}

function emailAccountDraft(account: EmailAccountItem): EmailAccountDraft {
  return {
    account_id: account.account_id,
    display_name: account.display_name,
    email_address: account.email_address,
    imap_host: account.imap_host,
    imap_port: String(account.imap_port),
    imap_tls: account.imap_tls,
    imap_username: account.imap_username,
    imap_secret: "",
    imap_secret_configured: account.imap_secret_configured,
    enabled: account.enabled,
    scan_folders: account.scan_folders.join(", "),
    agent_lookback_days: account.agent_lookback_days,
    model_lookback_days: account.model_lookback_days,
    scan_read_state: account.scan_read_state,
  };
}

function emailAccountId(address: string) {
  let result = "";
  let separated = false;
  for (const character of address.trim().toLowerCase()) {
    const code = character.charCodeAt(0);
    const allowed = (code >= 48 && code <= 57) || (code >= 97 && code <= 122);
    if (allowed) {
      result += character;
      separated = false;
    } else if (result && !separated) {
      result += "_";
      separated = true;
    }
  }
  while (result.endsWith("_")) result = result.slice(0, -1);
  return (result.length >= 2 ? result : "mail_account").slice(0, 64);
}

function accountPayload(draft: EmailAccountDraft): EmailAccountPayload | null {
  const imapPort = Number(draft.imap_port);
  const scanFolders = draft.scan_folders.split(",").map((folder) => folder.trim()).filter(Boolean);
  if (
    !draft.display_name.trim()
    || !draft.email_address.trim()
    || !draft.imap_host.trim()
    || !draft.imap_username.trim()
    || !Number.isInteger(imapPort)
    || imapPort < 1
    || imapPort > 65535
    || !scanFolders.length
  ) return null;
  return {
    account_id: draft.account_id || emailAccountId(draft.email_address),
    display_name: draft.display_name.trim(),
    email_address: draft.email_address.trim(),
    imap_host: draft.imap_host.trim(),
    imap_port: imapPort,
    imap_tls: draft.imap_tls,
    imap_username: draft.imap_username.trim(),
    ...(draft.imap_secret.trim() ? { imap_secret: draft.imap_secret } : {}),
    enabled: draft.enabled,
    scan_folders: scanFolders,
    agent_lookback_days: draft.agent_lookback_days,
    model_lookback_days: draft.model_lookback_days,
    scan_read_state: draft.scan_read_state,
  };
}

function savedAccountPayload(account: EmailAccountItem): EmailAccountPayload {
  return {
    account_id: account.account_id,
    display_name: account.display_name,
    email_address: account.email_address,
    imap_host: account.imap_host,
    imap_port: account.imap_port,
    imap_tls: account.imap_tls,
    imap_username: account.imap_username,
    enabled: account.enabled,
    scan_folders: account.scan_folders,
    agent_lookback_days: account.agent_lookback_days,
    model_lookback_days: account.model_lookback_days,
    scan_read_state: account.scan_read_state,
  };
}

function EmailAccountsPanel() {
  const [accounts, setAccounts] = useState<EmailAccountItem[]>([]);
  const [draft, setDraft] = useState<EmailAccountDraft | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [busyAccountId, setBusyAccountId] = useState("");
  const [error, setError] = useState("");
  const [restartRequired, setRestartRequired] = useState(false);
  const [connectionStates, setConnectionStates] = useState<Record<string, string>>({});

  useEffect(() => {
    const controller = new AbortController();
    listEmailAccounts(controller.signal).then((response) => {
      if (controller.signal.aborted) return;
      setAccounts(response.items);
      setState("ready");
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : "邮箱账户加载失败");
      setState("error");
    });
    return () => controller.abort();
  }, []);

  const replaceAccount = (next: EmailAccountItem) => {
    setAccounts((current) => [...current.filter((account) => account.account_id !== next.account_id), next].sort((left, right) => left.display_name.localeCompare(right.display_name, "zh-CN")));
  };

  async function saveAccount() {
    if (!draft || busyAccountId) return;
    const payload = accountPayload(draft);
    if (!payload) {
      setError("请完整填写 IMAP 账户信息；端口需为 1–65535，且至少填写一个扫描文件夹。");
      return;
    }
    if (!draft.account_id && !payload.imap_secret) {
      setError("新增邮箱时请填写 IMAP 密码。");
      return;
    }
    setBusyAccountId(payload.account_id);
    setError("");
    try {
      const response = draft.account_id
        ? await updateEmailAccount(draft.account_id, payload)
        : await createEmailAccount(payload);
      replaceAccount(response.item);
      setRestartRequired(response.restart_required);
      setDraft(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "邮箱账户保存失败");
    } finally {
      setBusyAccountId("");
    }
  }

  async function toggleAccount(account: EmailAccountItem) {
    if (busyAccountId) return;
    setBusyAccountId(account.account_id);
    setError("");
    try {
      const response = await updateEmailAccount(account.account_id, { ...savedAccountPayload(account), enabled: !account.enabled });
      replaceAccount(response.item);
      setRestartRequired(response.restart_required);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "邮箱启用状态保存失败");
    } finally {
      setBusyAccountId("");
    }
  }

  async function testConnection(account: EmailAccountItem) {
    if (busyAccountId) return;
    setBusyAccountId(account.account_id);
    setError("");
    setConnectionStates((current) => ({ ...current, [account.account_id]: "正在测试 IMAP 连接…" }));
    try {
      const response = await testEmailAccount(account.account_id);
      const label = response.diagnostics.imap.ok ? "IMAP 连接成功" : response.diagnostics.imap.code === "secret_not_configured" ? "请先保存 IMAP 密码" : "IMAP 连接失败";
      setConnectionStates((current) => ({ ...current, [account.account_id]: label }));
    } catch (reason: unknown) {
      setConnectionStates((current) => ({ ...current, [account.account_id]: "IMAP 连接失败" }));
      setError(reason instanceof Error ? reason.message : "连接测试失败");
    } finally {
      setBusyAccountId("");
    }
  }

  return <div className="email-accounts-panel">
    <div className="email-accounts-heading"><div><h3>邮箱账户</h3><p className="muted">可以添加多个 IMAP 邮箱。这里只读取和管理邮件；SMTP 与邮件回复保持禁用。</p></div><button type="button" className="primary-button" disabled={Boolean(busyAccountId)} onClick={() => { setDraft(newEmailAccountDraft()); setError(""); }}>添加邮箱</button></div>
    {restartRequired && <div className="page-state email-restart-notice" role="status">配置已保存，请重启服务使其生效。</div>}
    {error && <div className="page-state page-state-error" role="alert">{error}</div>}
    {state === "loading" && <div className="page-state" role="status">正在加载邮箱账户…</div>}
    {state === "error" && <button type="button" className="secondary-button" onClick={() => window.location.reload()}>重新加载</button>}
    {state === "ready" && <div className="email-account-list">
      {accounts.length ? accounts.map((account) => <article className="email-account-card" key={account.account_id}>
        <div className="email-account-summary"><div><h4>{account.display_name}</h4><p>{account.email_address}</p><p className="muted">{account.imap_host}:{account.imap_port} · {account.scan_folders.join("、")}</p><p className="muted">Agent {account.agent_lookback_days} 天 · 模型 {account.model_lookback_days} 天 · Agent 处理{account.scan_read_state === "all" ? "未读和已读" : "仅未读"}</p></div><label className="email-account-switch"><span>启用</span><input type="checkbox" role="switch" aria-label={`启用${account.display_name}`} checked={account.enabled} disabled={Boolean(busyAccountId)} onChange={() => void toggleAccount(account)} /></label></div>
        <div className="email-account-status-row"><span>{account.imap_secret_configured ? "已保存密码" : "尚未设置密码"}</span><span>{connectionStates[account.account_id] || "尚未测试连接"}</span></div>
        {account.unverified_categories?.length ? <p className="field-error" role="status">这些分类在本邮箱里没有验证到文件夹，已暂停：{account.unverified_categories.join("、")}。确认密码和文件夹权限后重新保存邮箱即可恢复。</p> : null}
        <div className="email-account-actions"><button type="button" className="secondary-button" disabled={Boolean(busyAccountId)} aria-label={`编辑${account.display_name}`} onClick={() => { setDraft(emailAccountDraft(account)); setError(""); }}>编辑</button><button type="button" className="secondary-button" disabled={Boolean(busyAccountId)} aria-label={`测试${account.display_name}连接`} onClick={() => void testConnection(account)}>测试连接</button></div>
      </article>) : <p className="wechat-empty">还没有邮箱账户。点击“添加邮箱”开始配置。</p>}
    </div>}
    {draft && <form className="email-account-form" aria-label={draft.account_id ? `编辑${draft.display_name}` : "添加邮箱"} onSubmit={(event) => { event.preventDefault(); void saveAccount(); }}>
      <div className="email-account-form-heading"><div><h4>{draft.account_id ? "编辑邮箱" : "添加邮箱"}</h4><p className="muted">只需填写收信信息。系统不会连接 SMTP，也不会回复邮件。</p></div><button type="button" className="secondary-button" disabled={Boolean(busyAccountId)} onClick={() => setDraft(null)}>取消</button></div>
      <div className="email-account-form-grid">
        <label><span>邮箱名称</span><input aria-label="邮箱名称" value={draft.display_name} onChange={(event) => setDraft({ ...draft, display_name: event.target.value })} /></label>
        <label><span>邮箱地址</span><input aria-label="邮箱地址" type="email" value={draft.email_address} onChange={(event) => { const nextAddress = event.target.value; setDraft({ ...draft, email_address: nextAddress, imap_username: !draft.imap_username || draft.imap_username === draft.email_address ? nextAddress : draft.imap_username }); }} /></label>
        <label><span>IMAP 服务器</span><input aria-label="IMAP 服务器" value={draft.imap_host} placeholder="例如 imap.example.com" onChange={(event) => setDraft({ ...draft, imap_host: event.target.value })} /></label>
        <label><span>IMAP 端口</span><input aria-label="IMAP 端口" type="number" min="1" max="65535" value={draft.imap_port} onChange={(event) => setDraft({ ...draft, imap_port: event.target.value })} /></label>
        <label><span>IMAP 用户名</span><input aria-label="IMAP 用户名" value={draft.imap_username} onChange={(event) => setDraft({ ...draft, imap_username: event.target.value })} /></label>
        <div><SecretField id="email-imap-secret" label="IMAP 密码" value={draft.imap_secret} onChange={(value) => setDraft({ ...draft, imap_secret: value })} />{draft.imap_secret_configured && <p className="field-help">密码已保存；留空不会修改。</p>}</div>
        <label><span>扫描文件夹</span><input aria-label="扫描文件夹" value={draft.scan_folders} placeholder="INBOX, Receipts" onChange={(event) => setDraft({ ...draft, scan_folders: event.target.value })} /><small>多个文件夹用英文逗号分隔。</small></label>
        <label className="email-scan-window"><span>Agent 回溯：最近 {draft.agent_lookback_days} 天</span><input aria-label="Agent 回溯天数" type="range" min="1" max="365" step="1" value={draft.agent_lookback_days} onChange={(event) => setDraft({ ...draft, agent_lookback_days: Number(event.target.value) })} /><small>尚无上线模型时，这个窗口内符合读取范围的邮件会交给 Agent 分类。</small></label>
        <label className="email-scan-window"><span>模型回溯：最近 {draft.model_lookback_days} 天</span><input aria-label="模型回溯天数" type="range" min="1" max="3650" step="1" value={draft.model_lookback_days} onChange={(event) => setDraft({ ...draft, model_lookback_days: Number(event.target.value) })} /><small>模型上线后优先处理这个窗口内的全部未分类邮件；确定结果自动整理，不确定的结果进入“待确认”，不会交给 Agent。</small></label>
        <label className="email-scan-read-state"><span>Agent 处理范围</span><span className="email-account-switch"><span>{draft.scan_read_state === "all" ? "未读和已读" : "仅未读"}</span><input type="checkbox" role="switch" aria-label="同时处理已读邮件" checked={draft.scan_read_state === "all"} onChange={(event) => setDraft({ ...draft, scan_read_state: event.target.checked ? "all" : "unread" })} /></span><small>{draft.scan_read_state === "all" ? "Agent 也会处理已读邮件；已读状态保持不变。" : "Agent 只处理未读邮件；模型上线后不受这个设置影响。"}</small></label>
      </div>
      <div className="email-account-options"><label><input type="checkbox" checked={draft.imap_tls} onChange={(event) => setDraft({ ...draft, imap_tls: event.target.checked })} /> 使用 SSL/TLS</label><label><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} /> 启用此邮箱</label></div>
      <button type="submit" className="primary-button" disabled={Boolean(busyAccountId)}>{busyAccountId ? "正在保存…" : "保存邮箱"}</button>
    </form>}
  </div>;
}

function ConnectorTabs({ connector }: { connector: string }) {
  return <div className="settings-pill-row" role="tablist" aria-label="Connector sections"><ConnectorTab value="dingtalk" label="DingTalk" active={connector === "dingtalk"} /><ConnectorTab value="lark" label="Lark" active={connector === "lark"} /><ConnectorTab value="fxiaoke" label="纷享销客 CLI" active={connector === "fxiaoke"} /><ConnectorTab value="wechat" label="WeChat" active={connector === "wechat"} /><ConnectorTab value="email" label="Email" active={connector === "email"} /></div>;
}

function ConnectorPanel({ payload, connector }: { payload: RecordValue; connector: string }) {
  const [loginState, setLoginState] = useState<"idle" | "starting" | "started">("idle");
  const item = record(payload[connector]);
  const state = displayValue(item.state || item.status || "unknown");
  const commands = Array.isArray(item.commands) ? item.commands : [];
  if (connector === "email") return <SettingsCard><div className="settings-card-heading"><div><h2>Connectors</h2><p className="muted">在这里维护多个收信邮箱。保存的密码不会显示在页面或接口响应中。</p></div></div><ConnectorTabs connector={connector} /><div id="connector-panel" role="tabpanel" aria-label="Email connector"><EmailAccountsPanel /></div></SettingsCard>;
  if (connector === "wechat") return <SettingsCard><div className="settings-card-heading"><div><h2>Connectors</h2><p className="muted">WeChat 自动回复范围在当前页面直接维护。</p></div></div><ConnectorTabs connector={connector} /><div id="connector-panel" role="tabpanel" aria-label="WeChat connector"><div className="connector-heading"><h3>WeChat connector</h3></div><p className="muted">连接和能力检查请在 Tutorial 完成；下方回复范围编辑不会自动发送消息。</p><WechatReplyScopePanel /></div></SettingsCard>;
  const connectorLabel = connector === "dingtalk" ? "DingTalk connector" : connector === "fxiaoke" ? "纷享销客 CLI" : "Lark connector";
  async function login() { setLoginState("starting"); try { await startConnectorLogin(connector); setLoginState("started"); } catch { setLoginState("idle"); } }
  const loginNeeded = state !== "ready";
  return <SettingsCard><div className="settings-card-heading"><div><h2>Connectors</h2><p className="muted">External connector status, live probes, and local CLI readiness.</p></div><StatusBadge value={state} /></div><ConnectorTabs connector={connector} /><div id="connector-panel" role="tabpanel" aria-label={`${connector} connector`}><div className="connector-heading"><h3>{connectorLabel}</h3><StatusBadge value={state} /></div><p className="muted">只显示当前连接器的 readiness、live probe 和登录状态。</p>{loginNeeded && <div className="connector-login-action"><button type="button" className="primary-button" disabled={loginState === "starting"} onClick={() => void login()}>{loginState === "starting" ? "正在启动登录…" : loginState === "started" ? "已启动，完成授权后刷新" : "一键登录 / 启动"}</button>{loginState === "started" && <span className="muted">CLI 已启动，网页授权完成后点击刷新。</span>}</div>}<div className="connector-state-grid"><StateItem label="Reason" value={displayValue(item.reason_code)} /><StateItem label="Login" value={displayValue(item.login || "not requested")} /><StateItem label="Last success" value={displayValue(item.last_success || (state === "ready" ? "本次检查" : "尚无成功记录"))} /><StateItem label="Detail" value={displayValue(item.detail || "没有额外说明。")} /></div><h4>Checks</h4><div className="connector-commands">{commands.length ? commands.map((command, index) => <code key={index}>{Array.isArray(command) ? command.map(displayValue).join(" ") : displayValue(command)}</code>) : <span className="muted">未执行</span>}</div></div></SettingsCard>;
}

function ConnectorTab({ value, label, active }: { value: string; label: string; active: boolean }) {
  return <Link role="tab" aria-selected={active} className={active ? "active" : ""} to={`/settings?tab=connectors&connector=${value}`}>{label}</Link>;
}

function StateItem({ label, value }: { label: string; value: string }) { return <div className="connector-state-item"><span>{label}</span><strong>{value}</strong></div>; }

function skillErrorMessage(reason: unknown, fallback: string) {
  if (reason instanceof Error && reason.message) return reason.message;
  return fallback;
}

function LegacySkillsPanel() {
  const [features, setFeatures] = useState<SkillFeature[]>([]);
  const [skills, setSkills] = useState<ProjectSkill[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [selectedFeatureId, setSelectedFeatureId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [draft, setDraft] = useState("");
  const [detailState, setDetailState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [detailError, setDetailError] = useState("");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [editorMode, setEditorMode] = useState<"preview" | "edit">("preview");
  const [toggleMessage, setToggleMessage] = useState("");
  const draftCache = useRef(new Map<string, string>());
  const detailRequest = useRef<{ id: number; controller: AbortController | null }>({ id: 0, controller: null });
  const [toggleBusy, setToggleBusy] = useState<Set<string>>(new Set());

  async function load(signal?: AbortSignal) {
    setState("loading"); setError("");
    try {
      const result = await getSkillFeatures(signal);
      if (signal?.aborted) return;
      const nextFeatures = Array.isArray(result.features) ? result.features : [];
      setFeatures(nextFeatures);
      setSelectedFeatureId((current) => current ?? nextFeatures[0]?.feature_id ?? null);
      setSkills(Array.isArray(result.skills) ? result.skills : []);
      setState("ready");
    } catch (reason) {
      if (signal?.aborted) return;
      setState("error"); setError(skillErrorMessage(reason, "Skills 加载失败"));
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, []);

  async function openSkill(name: string) {
    if (expanded === name) { setExpanded(null); return; }
    if (detail && draft !== detail.content) draftCache.current.set(detail.name, draft);
    detailRequest.current.controller?.abort();
    const id = detailRequest.current.id + 1;
    const controller = new AbortController();
    detailRequest.current = { id, controller };
    setExpanded(name); setDetail(null); setDraft(draftCache.current.get(name) || ""); setDetailError(""); setSaveState("idle"); setEditorMode("preview"); setDetailState("loading");
    try {
      const result = await getSkillDetail(name, controller.signal);
      if (detailRequest.current.id !== id) return;
      setDetail(result); setDraft(draftCache.current.get(name) || result.content); setDetailState("ready");
    } catch (reason) {
      if (controller.signal.aborted || detailRequest.current.id !== id) return;
      setDetailState("error"); setDetailError(skillErrorMessage(reason, "Skill 读取失败"));
    }
  }

  async function saveSkill() {
    if (!detail) return;
    setSaveState("saving"); setDetailError("");
    try {
      const result = await saveSkillApi(detail.name, draft, detail.sha256);
      setDetail(result); setDraft(result.content); draftCache.current.delete(result.name); setSaveState("saved");
      setSkills((current) => current.map((skill) => skill.name === result.name ? { ...skill, description: result.description, sha256: result.sha256, status: "ready" } : skill));
    } catch (reason) {
      setSaveState("error");
      const status = typeof reason === "object" && reason !== null && "status" in reason ? Number((reason as { status?: unknown }).status) : 0;
      setDetailError(status === 409 ? "保存冲突：Skill 内容已被其他修改，请重新读取后再保存；当前草稿已保留。" : status === 422 ? "Skill 内容校验失败；当前草稿已保留。" : status >= 500 ? "Skill 保存失败；当前草稿已保留。" : skillErrorMessage(reason, "Skill 保存失败；当前草稿已保留。"));
    }
  }

  async function changeFeature(feature: SkillFeature, enabled: boolean) {
    setToggleMessage("");
    setToggleBusy((current) => new Set(current).add(feature.feature_id));
    setFeatures((current) => current.map((item) => item.feature_id === feature.feature_id ? { ...item, enabled } : item));
    try {
      const result = await toggleSkillFeature(feature.feature_id, enabled);
      setFeatures((current) => current.map((item) => item.feature_id === feature.feature_id ? { ...item, enabled: result.enabled, status: result.status } : item));
      setToggleMessage("功能开关已保存；开关只影响新任务。");
    } catch (reason) {
      setFeatures((current) => current.map((item) => item.feature_id === feature.feature_id ? { ...item, enabled: feature.enabled } : item));
      setError(skillErrorMessage(reason, "功能开关保存失败；当前状态已恢复。"));
    } finally {
      setToggleBusy((current) => { const next = new Set(current); next.delete(feature.feature_id); return next; });
    }
  }

  const selectedFeature = features.find((feature) => feature.feature_id === selectedFeatureId) || null;
  const showUnscopedSkills = features.length === 0;
  const visibleSkills = selectedFeature ? skills.filter((skill) => selectedFeature.skills.includes(skill.name)) : showUnscopedSkills ? skills : [];

  return <SettingsCard>
    <div className="settings-card-heading"><div><h2>Skills</h2><p className="muted">管理功能开关和项目内共享 Skills。开关只影响新任务；共享 skill 编辑影响引用功能。</p></div><span className="settings-path">Project skills</span></div>
    {state === "loading" && <div className="page-state" role="status">正在加载 Skills…</div>}
    {state === "error" && <div className="page-state page-state-error" role="alert">{error}<button type="button" className="secondary-button" onClick={() => void load()}>重试</button></div>}
    {state === "ready" && <>
      {error && <p className="field-error" role="alert">{error}</p>}{toggleMessage && <p className="save-success" role="status">{toggleMessage}</p>}
      <div className="skills-feature-grid">{features.map((feature) => { const selected = selectedFeatureId === feature.feature_id; return <article className={`skills-feature-card ${selected ? "is-selected" : ""}`} key={feature.feature_id} tabIndex={0} aria-selected={selected} onClick={() => setSelectedFeatureId(feature.feature_id)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelectedFeatureId(feature.feature_id); } }}><div className="skills-feature-heading"><div><h3>{feature.name}</h3><p>{feature.description}</p></div><label className="skills-toggle" onClick={(event) => event.stopPropagation()}><span className="sr-only">{feature.name}</span><input type="checkbox" role="switch" aria-label={feature.name} checked={feature.enabled} disabled={toggleBusy.has(feature.feature_id)} onChange={(event) => void changeFeature(feature, event.target.checked)} /><span className="skills-toggle-track" aria-hidden="true" /></label></div><div className="skills-feature-meta"><span className={`skills-status ${feature.status}`}>{feature.status === "ready" ? "Ready" : "Incomplete"}</span><span>{feature.enabled ? "ON" : "OFF"}</span></div><div className="skills-associated"><span>关联 Skills</span><div>{feature.skills.map((name) => <button type="button" className="skill-link" key={name} onClick={(event) => { event.stopPropagation(); void openSkill(name); }}>{name}</button>)}</div></div></article>; })}</div>
      <div className="skills-project-heading"><div><h3>{selectedFeature ? `${selectedFeature.name} · 关联 Skills` : showUnscopedSkills ? "全部 project skills" : "选择一个能力"}</h3><p className="muted">{selectedFeature ? "仅显示当前能力关联的 Skills。非法 skill 行不影响其他内容。" : showUnscopedSkills ? "非法 skill 行不影响其他内容。" : "点击上方能力 tile，查看它关联的 Skills。"}</p></div>{selectedFeature && <button type="button" className="secondary-button" onClick={() => setSelectedFeatureId(null)}>清除选择</button>}</div>
      {(selectedFeature || showUnscopedSkills) && <div className="skills-project-list">{visibleSkills.map((skill) => <article className={`skills-project-row ${skill.status === "invalid" ? "is-invalid" : ""}`} key={skill.name}><div><strong>{skill.name}</strong>{skill.status === "invalid" ? <p className="field-error">{skill.error || "Skill 无法读取"}</p> : <p className="muted">{skill.description || "未提供描述"}</p>}<div className="skills-references">{selectedFeature ? `引用功能：${selectedFeature.feature_id}` : skill.referenced_by?.length ? `引用功能：${skill.referenced_by.join("、")}` : "暂无引用功能"}</div></div>{skill.status !== "invalid" && <button type="button" className="secondary-button" onClick={() => void openSkill(skill.name)}>{expanded === skill.name ? "收起" : `查看 ${skill.name}`}</button>}{expanded === skill.name && <div className="skill-editor" aria-label={`${skill.name} 编辑器`}>{detailState === "loading" && <p role="status">正在读取 Skill…</p>}{detailState === "error" && <p className="field-error" role="alert">{detailError}</p>}{detailState === "ready" && detail && <><p className="muted">共享 skill 编辑影响引用功能。SHA-256: <code>{detail.sha256}</code></p><div className="settings-pill-row skill-editor-tabs" role="tablist" aria-label="Skill detail view"><button type="button" role="tab" id="skill-preview-tab" aria-controls="skill-preview-panel" tabIndex={editorMode === "preview" ? 0 : -1} aria-selected={editorMode === "preview"} className={editorMode === "preview" ? "active" : ""} onClick={() => setEditorMode("preview")} onKeyDown={(event) => { if (event.key === "ArrowRight" || event.key === "ArrowDown") { event.preventDefault(); setEditorMode("edit"); document.getElementById("skill-edit-tab")?.focus(); } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") { event.preventDefault(); setEditorMode("edit"); document.getElementById("skill-edit-tab")?.focus(); } }}>预览</button><button type="button" role="tab" id="skill-edit-tab" aria-controls="skill-edit-panel" tabIndex={editorMode === "edit" ? 0 : -1} aria-selected={editorMode === "edit"} className={editorMode === "edit" ? "active" : ""} onClick={() => setEditorMode("edit")} onKeyDown={(event) => { if (event.key === "ArrowLeft" || event.key === "ArrowUp" || event.key === "ArrowRight" || event.key === "ArrowDown") { event.preventDefault(); setEditorMode("preview"); document.getElementById("skill-preview-tab")?.focus(); } }}>编辑</button></div>{editorMode === "preview" ? <div id="skill-preview-panel" role="tabpanel" aria-labelledby="skill-preview-tab" aria-label="Skill 预览" className="skill-markdown-preview"><ReactMarkdown remarkPlugins={[remarkGfm]}>{draft}</ReactMarkdown></div> : <div id="skill-edit-panel" role="tabpanel" aria-labelledby="skill-edit-tab" aria-label="Skill 编辑"><label className="skill-content-label" htmlFor="skill-content">Skill 内容</label><textarea id="skill-content" aria-label="Skill 内容" value={draft} onChange={(event) => { setDraft(event.target.value); setSaveState("idle"); }} rows={16} /><div className="skill-editor-actions"><button type="button" className="primary-button" onClick={() => void saveSkill()} disabled={saveState === "saving"}>{saveState === "saving" ? "保存中…" : "保存 Skill"}</button><button type="button" className="secondary-button" onClick={() => { setDraft(detail.content); draftCache.current.delete(detail.name); setSaveState("idle"); setDetailError(""); }}>取消</button>{saveState === "saved" && <span className="save-success" role="status">已保存</span>}{detailError && <span className="save-error" role="alert">{detailError}</span>}</div></div>}</>}</div>}</article>)}</div>}
    </>}
  </SettingsCard>;
}

const CODEX_MODEL_OPTIONS = [
  { value: "gpt-6-astra", label: "GPT-6 Astra" },
  { value: "gpt-6-sol", label: "GPT-6 Sol" },
  { value: "gpt-6-luna", label: "GPT-6 Luna" },
  { value: "gpt-5.5", label: "GPT-5.5" },
  { value: "gpt-5.6-sol", label: "GPT-5.6 Sol" },
  { value: "gpt-5.6-terra", label: "GPT-5.6 Terra" },
  { value: "gpt-5.6-luna", label: "GPT-5.6 Luna" },
];

// The Claude CLI takes an alias or a full model id; these are the aliases the
// service ships with. A value outside the list still shows, as "当前配置".
const CLAUDE_MODEL_OPTIONS = [
  { value: "opus", label: "Opus" },
  { value: "sonnet", label: "Sonnet" },
  { value: "haiku", label: "Haiku" },
];

// The API route addresses models by id, not by the local CLI's alias.
const CLAUDE_API_MODEL_GROUPS = [
  { label: "别名（跟随本机 CLI）", options: CLAUDE_MODEL_OPTIONS },
  { label: "Claude 模型 id", options: [
    { value: "claude-opus-5-5", label: "Opus 5.5" },
    { value: "claude-opus-5", label: "Opus 5" },
    { value: "claude-sonnet-5", label: "Sonnet 5" },
    { value: "claude-fable-5-1", label: "Fable 5.1" },
    { value: "claude-haiku-4-5-20251001", label: "Haiku 4.5" },
  ] },
];

// The service refuses anything outside this set, so the page offers only these.
const REASONING_EFFORT_OPTIONS = [
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "xhigh", label: "Extra high" },
];

const COMPATIBLE_MODEL_GROUPS = [
  { label: "OpenAI", options: CODEX_MODEL_OPTIONS },
  { label: "MiniMax", options: [
    { value: "MiniMax-M3", label: "MiniMax M3" },
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

const RUNTIME_ROUTE_LABELS: Record<string, string> = {
  codex_oauth: "Codex OAuth",
  codex_api: "Codex API",
  claude_oauth: "Claude OAuth",
  claude_api: "Claude API",
  friday_runtime: "Friday Runtime",
};

function routeOrder(configured: string) {
  return configured.split(",").map((name) => name.trim()).filter(Boolean);
}

const ADDED_RUNTIME_KINDS: Array<{ value: string; label: string; hint: string; needsBaseUrl: boolean; needsToken: boolean }> = [
  { value: "codex_oauth", label: "Codex CLI · 登录", hint: "复用本机 Codex 登录，只换模型", needsBaseUrl: false, needsToken: false },
  { value: "codex_api", label: "Codex CLI · API", hint: "自带地址、模型和 Token", needsBaseUrl: true, needsToken: true },
  { value: "claude_oauth", label: "Claude CLI · 登录", hint: "复用本机 Claude 登录，只换模型", needsBaseUrl: false, needsToken: false },
  { value: "claude_api", label: "Claude CLI · API", hint: "自带模型和 Token", needsBaseUrl: false, needsToken: true },
];

function addedKind(value: string) {
  return ADDED_RUNTIME_KINDS.find((item) => item.value === value);
}

function addedRoutePrefix(name: string) {
  return `CEO_RUNTIME_${name.toUpperCase()}_`;
}

function AddRuntimeForm({ onAdd, onCancel, taken, restorable, onRestore }: { onAdd: (route: { name: string; kind: string; baseUrl: string; model: string; token: string }) => void; onCancel: () => void; taken: string[]; restorable: string[]; onRestore: (name: string) => void }) {
  const [name, setName] = useState("");
  // Most added routes bring their own provider, so start on that kind.
  const [kind, setKind] = useState("codex_api");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const selected = addedKind(kind);
  const needsBaseUrl = selected?.needsBaseUrl ?? false;
  const needsToken = selected?.needsToken ?? false;
  const submit = () => {
    const trimmed = name.trim();
    if (!/^[a-z][a-z0-9_]*$/.test(trimmed)) { setError("名称只能用小写字母、数字和下划线，且以字母开头"); return; }
    if (taken.includes(trimmed)) { setError("这个名称已经用过了"); return; }
    if (!model.trim()) { setError("请填写模型名"); return; }
    if (needsToken && !token.trim()) { setError("请填写 API Token"); return; }
    if (needsBaseUrl && !baseUrl.trim()) { setError("请填写 API Base URL"); return; }
    setError("");
    onAdd({ name: trimmed, kind, baseUrl: baseUrl.trim(), model: model.trim(), token: token.trim() });
    setName(""); setBaseUrl(""); setModel(""); setToken("");
  };
  return <section className="runtime-card runtime-card-wide runtime-add-card">
    <div className="runtime-card-head"><div><h3>新增 runtime</h3><p>{selected?.hint ?? "同一种类型可以添加多条，各自独立配置"}</p></div></div>
    {restorable.length > 0 && <div className="runtime-restore-row">
      <span className="muted">删除过的内置线路：</span>
      {restorable.map((name) => <button key={name} type="button" className="secondary-button runtime-inline-button" aria-label={`恢复 ${RUNTIME_ROUTE_LABELS[name] ?? name}`} onClick={() => onRestore(name)}><RotateCcw size={14} aria-hidden="true" />{RUNTIME_ROUTE_LABELS[name] ?? name}</button>)}
    </div>}
    <div className="runtime-fields">
      <label className="runtime-field"><span>名称</span><input aria-label="新增 runtime 名称" value={name} placeholder="例如 qwen_gpu4" onChange={(event) => setName(event.target.value)} /></label>
      <SelectField id="added-runtime-kind" label="类型" value={kind} onChange={setKind}>{ADDED_RUNTIME_KINDS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</SelectField>
      {needsBaseUrl && <label className="runtime-field"><span>API Base URL</span><input aria-label="新增 runtime API Base URL" type="url" value={baseUrl} placeholder="http://100.93.145.69:8900/v1" onChange={(event) => setBaseUrl(event.target.value)} /></label>}
      <label className="runtime-field"><span>模型</span><input aria-label="新增 runtime 模型" value={model} placeholder="qwen3.8-27b" onChange={(event) => setModel(event.target.value)} /></label>
      {needsToken && <SecretField id="added-runtime-token" label="新增 runtime API Token" value={token} onChange={setToken} />}
    </div>
    {error && <p className="field-error" role="alert">{error}</p>}
    <div className="runtime-add-actions"><button type="button" className="secondary-button" onClick={onCancel}>取消</button><button type="button" className="secondary-button runtime-inline-button" onClick={submit}><Plus size={14} aria-hidden="true" />添加</button></div>
  </section>;
}

function RuntimeRouteCard({ title, description, enabled, locked, wide, unavailable, readOnly, onToggle, onDelete, onRename, children }: { title: string; description: string; enabled: boolean; locked?: boolean; wide?: boolean; unavailable?: string; readOnly?: boolean; onToggle?: (next: boolean) => void; onDelete?: () => void; onRename?: (next: string) => void; children?: ReactNode }) {
  const blocked = Boolean(unavailable);
  // A rename lands when the field is left, not on every keystroke: renaming
  // per character would carry the settings through every partial name.
  const [nameDraft, setNameDraft] = useState(title);
  useEffect(() => { setNameDraft(title); }, [title]);
  const commitName = () => {
    const next = nameDraft.trim();
    if (!next || next === title) { setNameDraft(title); return; }
    onRename?.(next);
  };
  return <section className={`${wide ? "runtime-card runtime-card-wide" : "runtime-card"}${blocked ? " runtime-card-unavailable" : ""}`}>
    <div className="runtime-card-head">
      <div className="runtime-card-title">
        {onRename
          ? <input className="runtime-card-name" aria-label={`${title} 名称`} value={nameDraft} onChange={(event) => setNameDraft(event.target.value)} onBlur={commitName} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); commitName(); } }} />
          : <h3>{onDelete && <GripVertical className="runtime-card-grip" size={14} aria-hidden="true" />}{title}</h3>}
        <p>{description}</p>
      </div>
      {locked
        ? <span className="runtime-chip is-on" title="主路由不能关闭">始终启用</span>
        : <div className="runtime-card-actions">
            <label className="runtime-switch">
              <input type="checkbox" role="switch" aria-label={`启用 ${title}`} checked={enabled && !blocked} disabled={blocked || !onToggle} onChange={(event) => onToggle?.(event.target.checked)} />
              <span>{blocked ? "不可用" : enabled ? "已启用" : "未启用"}</span>
            </label>
            {onDelete && <button type="button" className="secondary-button runtime-icon-button" aria-label={`删除 ${title}`} title="删除这张卡" onClick={onDelete}><Trash2 size={15} aria-hidden="true" /></button>}
          </div>}
    </div>
    {blocked && <p className="runtime-card-unavailable-reason" role="status">{unavailable}</p>}
    {children && <fieldset className="runtime-fieldset" disabled={blocked || readOnly}><div className="runtime-fields">{children}</div></fieldset>}
  </section>;
}

function RuntimePanel({ payload, draft, setDraft, saveState, saveError }: { payload: RecordValue; draft: RecordValue; setDraft: (value: RecordValue) => void; saveState: "idle" | "saving" | "saved" | "error"; saveError: string }) {
  const value = (key: string) => displayValue(draft[key] ?? fieldsOf(payload)[key]);
  const raw = (key: string) => rawValue(draft, payload, key);
  const input = (key: string, label: string, type = "text") => <label className="runtime-field"><span>{label}</span><input type={type} value={value(key)} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} /></label>;
  const update = (key: string, next: string) => setDraft({ ...draft, [key]: next });
  const routes = routeOrder(raw("CEO_AGENT_RUNTIME_ROUTES"));
  const enabled = (name: string) => routes.includes(name);
  const builtIn = Object.keys(RUNTIME_ROUTE_LABELS);
  const addedRoutes = routes.filter((name) => !builtIn.includes(name));
  // A payload without this field predates the check; the service still refuses
  // the route when the CLI is missing, so do not grey the card out on a guess.
  const fridayCli = (payload as RecordValue)?.friday_cli as RecordValue | undefined;
  const fridayCliAvailable = fridayCli ? Boolean(fridayCli.available) : true;
  // The submitted order is the failover order. Enabling a built-in route puts
  // it in its canonical place among the other built-ins without disturbing an
  // order the operator has arranged.
  const toggleRoute = (name: string, next: boolean) => {
    if (!next) { update("CEO_AGENT_RUNTIME_ROUTES", routes.filter((route) => route !== name).join(",")); return; }
    const after = builtIn.slice(builtIn.indexOf(name) + 1);
    const at = routes.findIndex((route) => after.includes(route));
    const ordered = [...routes];
    ordered.splice(at === -1 ? ordered.length : at, 0, name);
    update("CEO_AGENT_RUNTIME_ROUTES", ordered.join(","));
  };
  const moveRoute = (from: number, to: number) => {
    const ordered = [...routes];
    const [moved] = ordered.splice(from, 1);
    ordered.splice(to, 0, moved);
    update("CEO_AGENT_RUNTIME_ROUTES", ordered.join(","));
  };
  const addRoute = (route: { name: string; kind: string; baseUrl: string; model: string; token: string }) => {
    const prefix = addedRoutePrefix(route.name);
    setDraft({
      ...draft,
      CEO_AGENT_RUNTIME_ROUTES: [...routes, route.name].join(","),
      [`${prefix}KIND`]: route.kind,
      [`${prefix}BASE_URL`]: route.baseUrl,
      [`${prefix}MODEL`]: route.model,
      [`${prefix}API_KEY`]: route.token,
    });
  };
  const removeRoute = (name: string) => update("CEO_AGENT_RUNTIME_ROUTES", routes.filter((route) => route !== name).join(","));
  const hidden = routeOrder(raw("CEO_AGENT_RUNTIME_HIDDEN_ROUTES"));
  const shown = (name: string) => !hidden.includes(name);
  // Switching a route off keeps its card and settings; deleting it takes the
  // card away and drops the credential that belongs to that route.
  const deleteBuiltIn = (name: string) => setDraft({
    ...draft,
    CEO_AGENT_RUNTIME_ROUTES: routes.filter((route) => route !== name).join(","),
    CEO_AGENT_RUNTIME_HIDDEN_ROUTES: [...hidden, name].join(","),
  });
  const restoreBuiltIn = (name: string) => setDraft({
    ...draft,
    CEO_AGENT_RUNTIME_ROUTES: [...routes, name].join(","),
    CEO_AGENT_RUNTIME_HIDDEN_ROUTES: hidden.filter((route) => route !== name).join(","),
  });
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [dragged, setDragged] = useState("");
  const renameRoute = (from: string, to: string) => {
    const before = addedRoutePrefix(from);
    const after = addedRoutePrefix(to);
    const carried: RecordValue = {};
    for (const suffix of ["KIND", "BASE_URL", "MODEL", "API_KEY"]) {
      carried[`${after}${suffix}`] = raw(`${before}${suffix}`);
      carried[`${before}${suffix}`] = "";
    }
    setDraft({
      ...draft,
      ...carried,
      CEO_AGENT_RUNTIME_ROUTES: routes.map((route) => (route === from ? to : route)).join(","),
    });
  };
  const builtInCard = (name: string) => {
    const common = {
      enabled: enabled(name),
      onToggle: editing ? (next: boolean) => toggleRoute(name, next) : undefined,
      onDelete: editing ? () => deleteBuiltIn(name) : undefined,
      readOnly: !editing,
    };
    if (name === "codex_oauth") return <RuntimeRouteCard title="Codex OAuth" description="默认的本机 OAuth 路由" enabled locked readOnly={!editing}>
      <ModelSelect id="codex-model" label="Model" value={raw("CEO_CODEX_MODEL")} groups={[{ label: "Codex / OpenAI", options: CODEX_MODEL_OPTIONS }]} onChange={(next) => update("CEO_CODEX_MODEL", next)} />
      <ModelSelect id="codex-effort" label="Thinking strength" value={raw("CEO_CODEX_MODEL_REASONING_EFFORT")} groups={[{ label: "Thinking strength", options: REASONING_EFFORT_OPTIONS }]} onChange={(next) => update("CEO_CODEX_MODEL_REASONING_EFFORT", next)} />
    </RuntimeRouteCard>;
    if (name === "codex_api") return <RuntimeRouteCard title="Codex API" description="OAuth 不可用时的备用路由" {...common}>
      {input("CEO_CODEX_API_BASE_URL", "API Base URL", "url")}
      <ModelSelect id="codex-api-model" label="Fallback model" value={raw("CEO_CODEX_API_MODEL")} groups={COMPATIBLE_MODEL_GROUPS} onChange={(next) => update("CEO_CODEX_API_MODEL", next)} />
      <SecretField id="codex-api-token" label="API Token" configured={Boolean(raw("CEO_CODEX_API_KEY"))} value={raw("CEO_CODEX_API_KEY")} onChange={(next) => update("CEO_CODEX_API_KEY", next)} />
    </RuntimeRouteCard>;
    if (name === "claude_oauth") return <RuntimeRouteCard title="Claude OAuth" description="复用本机 Claude Code 登录的路由" {...common}>
      <ModelSelect id="claude-model" label="Model" value={raw("CEO_CLAUDE_MODEL")} groups={[{ label: "Claude", options: CLAUDE_MODEL_OPTIONS }]} onChange={(next) => update("CEO_CLAUDE_MODEL", next)} />
      <ModelSelect id="claude-effort" label="Thinking strength" value={raw("CEO_CLAUDE_MODEL_REASONING_EFFORT")} groups={[{ label: "Thinking strength", options: REASONING_EFFORT_OPTIONS }]} onChange={(next) => update("CEO_CLAUDE_MODEL_REASONING_EFFORT", next)} />
    </RuntimeRouteCard>;
    if (name === "claude_api") return <RuntimeRouteCard title="Claude API" description="Claude 登录不可用时的 API 路由" {...common}>
      <ModelSelect id="claude-api-model" label="Model" value={raw("CEO_CLAUDE_API_MODEL") || raw("CEO_CLAUDE_MODEL")} groups={CLAUDE_API_MODEL_GROUPS} onChange={(next) => update("CEO_CLAUDE_API_MODEL", next)} />
      <SecretField id="claude-api-token" label="Claude API Token" configured={Boolean(raw("CEO_CLAUDE_API_KEY"))} value={raw("CEO_CLAUDE_API_KEY")} onChange={(next) => update("CEO_CLAUDE_API_KEY", next)} />
    </RuntimeRouteCard>;
    return <RuntimeRouteCard title="Friday Runtime" description="通过 Friday 自带 CLI 运行，无需配置" {...common} unavailable={fridayCliAvailable ? undefined : "未检测到 Friday 桌面版。Friday 的 CLI 随桌面版一起安装，本服务不单独安装；请先安装 Friday.app 再启用这条线路。"} />;
  };
  const addedCard = (name: string) => {
    const prefix = addedRoutePrefix(name);
    const kindLabel = addedKind(raw(`${prefix}KIND`))?.label ?? raw(`${prefix}KIND`);
    return <RuntimeRouteCard
      title={name}
      description={kindLabel}
      enabled={enabled(name)}
      onToggle={editing ? (next: boolean) => toggleRoute(name, next) : undefined}
      onDelete={editing ? () => removeRoute(name) : undefined}
      onRename={editing ? (next: string) => renameRoute(name, next) : undefined}
      readOnly={!editing}
    >
      {addedKind(raw(`${prefix}KIND`))?.needsBaseUrl && <label className="runtime-field"><span>API Base URL</span><input aria-label={`${name} API Base URL`} type="url" value={value(`${prefix}BASE_URL`)} onChange={(event) => update(`${prefix}BASE_URL`, event.target.value)} /></label>}
      <label className="runtime-field"><span>模型</span><input aria-label={`${name} 模型`} value={value(`${prefix}MODEL`)} onChange={(event) => update(`${prefix}MODEL`, event.target.value)} /></label>
      {addedKind(raw(`${prefix}KIND`))?.needsToken && <SecretField id={`added-${name}-token`} label={`${name} API Token`} configured={Boolean(raw(`${prefix}API_KEY`))} value={raw(`${prefix}API_KEY`)} onChange={(next) => update(`${prefix}API_KEY`, next)} />}
    </RuntimeRouteCard>;
  };
  const offRoutes = builtIn.filter((name) => shown(name) && !routes.includes(name));
  const listed = [...routes, ...offRoutes];
  const dropOn = (name: string) => {
    if (!dragged || dragged === name) return;
    const from = routes.indexOf(dragged);
    const to = routes.indexOf(name);
    if (from === -1 || to === -1) return;
    moveRoute(from, to);
    setDragged("");
  };
  return <SettingsCard>
    <div className="settings-card-heading">
      <div><p className="eyebrow">Settings / Agent Runtime</p><h2>Agent Runtime</h2><p className="muted">按顺序尝试，前一条不可用时自动切到下一条。保存后重启主服务才会生效。</p></div>
      <div className="runtime-header-actions">
        {editing && <button type="button" className="secondary-button runtime-inline-button" onClick={() => setAdding(true)}><Plus size={14} aria-hidden="true" />新增 runtime</button>}
        <button type="button" className="secondary-button runtime-inline-button" aria-label={editing ? "完成编辑" : "编辑线路"} onClick={() => { setEditing(!editing); setAdding(false); }}>{editing ? <><Check size={14} aria-hidden="true" />完成</> : <><Pencil size={14} aria-hidden="true" />编辑</>}</button>
      </div>
    </div>
    <form onSubmit={(event) => event.preventDefault()}>
      <ol className="runtime-flow">
        {listed.map((name) => {
          const position = routes.indexOf(name);
          const active = position >= 0;
          return <li
            key={name}
            className={`runtime-flow-item${active ? "" : " is-off"}${dragged === name ? " is-dragging" : ""}`}
            draggable={editing && active}
            onDragStart={() => setDragged(name)}
            onDragEnd={() => setDragged("")}
            onDragOver={(event) => { if (editing && active) event.preventDefault(); }}
            onDrop={(event) => { event.preventDefault(); dropOn(name); }}
          >
            <div className="runtime-flow-rail" aria-hidden="true"><span className="runtime-flow-rank">{active ? position + 1 : "–"}</span></div>
            {builtIn.includes(name) ? builtInCard(name) : addedCard(name)}
          </li>;
        })}
        {adding && <li className="runtime-flow-item is-new">
          <div className="runtime-flow-rail" aria-hidden="true"><span className="runtime-flow-rank">+</span></div>
          <AddRuntimeForm onAdd={(route) => { addRoute(route); setAdding(false); }} onCancel={() => setAdding(false)} taken={routes} restorable={hidden} onRestore={(name) => { restoreBuiltIn(name); setAdding(false); }} />
        </li>}
      </ol>
      <div className="runtime-save-bar"><SaveBar state={saveState} error={saveError} /></div>
    </form>
  </SettingsCard>;
}

function SettingsContent({ section, payload, draft, setDraft, prompt, view, connector, auditRule, saveState, saveError, onAttentionCountChange }: { section: SettingsSection; payload: RecordValue; draft: RecordValue; setDraft: (value: RecordValue) => void; prompt: PromptKind; view: "template" | "preview"; connector: string; auditRule: "template" | "consumer" | "audit"; saveState: "idle" | "saving" | "saved" | "error"; saveError: string; onAttentionCountChange: (count: number) => void }) {
  if (section === "status") return <StatusPanel />;
  if (section === "attention") return <AttentionPanel onCountChange={onAttentionCountChange} />;
  if (section === "skills") return <ManagedSkillsPanel />;
  if (section === "mcp") return <SettingsCard><McpPanel /></SettingsCard>;
  if (section === "prompts") return <PromptPanel payload={payload} prompt={prompt} view={view} draft={draft} setDraft={setDraft} saveState={saveState} saveError={saveError} />;
  if (section === "connectors") return <ConnectorPanel payload={payload} connector={connector} />;
  if (section === "agent-runtime") return <RuntimePanel payload={payload} draft={draft} setDraft={setDraft} saveState={saveState} saveError={saveError} />;
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
  const candidateSection = rawSection === "config" ? "configuration" : rawSection;
  const section = sections.some(([key]) => key === candidateSection) ? candidateSection as SettingsSection : "status";
  const prompt: PromptKind = params.get("prompt") === "user" ? "user" : params.get("prompt") === "profile" ? "profile" : "developer";
  const view = params.get("view") === "preview" ? "preview" : "template";
  const connector = params.get("connector") || "dingtalk";
  const auditRule = (["template", "consumer", "audit"] as const).includes(params.get("rule") as never) ? params.get("rule") as "template" | "consumer" | "audit" : "template";
  const [payload, setPayload] = useState<RecordValue | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [draft, setDraft] = useState<RecordValue>({});
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveError, setSaveError] = useState("");
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
    if (section === "skills" || section === "mcp") { setState("ready"); setPayload(null); setError(""); return; }
    if (section === "status" || section === "attention") return;
    const controller = new AbortController(); setState("loading"); setSaveState("idle"); setSaveError("");
    getSettings(section, controller.signal).then((response) => { setPayload(response.item); setDraft(fieldsOf(response.item)); setState("ready"); setError(""); }).catch((reason: unknown) => { if (controller.signal.aborted) return; setError(reason instanceof Error ? reason.message : "加载失败"); setState("error"); });
    return () => controller.abort();
  }, [section]);
  async function save() { if (!payload) return; setSaveState("saving"); setSaveError(""); try { const promptKey = prompt === "profile" ? "profile" : `${prompt}_template`; const rawPrompt = draft[promptKey] ?? fieldsOf(payload)[promptKey]; const fields = section === "prompts" ? { template: typeof rawPrompt === "string" ? rawPrompt : displayValue(rawPrompt) } : section === "audit-rules" ? { template: displayValue(draft.template) } : draft; await saveSettings(section, fields, section === "prompts" ? { prompt } : {}); setSaveState("saved"); } catch (reason: unknown) { setSaveState("error"); setSaveError(reason instanceof Error && reason.message ? reason.message : "保存失败，草稿仍保留"); } }
  const content = state === "error" ? <SettingsCard><div className="page-state page-state-error" role="alert">{error}</div></SettingsCard> : state === "loading" && !payload && section !== "status" && section !== "attention" && section !== "skills" && section !== "mcp" ? <SettingsCard><div className="page-state" role="status">正在加载…</div></SettingsCard> : <SettingsContent section={section} payload={payload || {}} draft={draft} setDraft={setDraft} prompt={prompt} view={view} connector={connector} auditRule={auditRule} saveState={saveState} saveError={saveError} onAttentionCountChange={setAttentionCount} />;
  return <main className="console-page settings-page" aria-labelledby="settings-page-title"><h1 id="settings-page-title" className="sr-only">Settings</h1><div className="settings-layout-react"><SectionNav section={section} attentionCount={attentionCount} /><div className="settings-content" onSubmit={(event) => { const form = event.target as HTMLFormElement; if (form.tagName === "FORM" && form.elements.namedItem("settings-save")) { event.preventDefault(); void save(); } }}>{content}</div></div></main>;
}
