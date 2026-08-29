import { useEffect, useState } from "react";
import { NavLink, useSearchParams } from "react-router-dom";

import { displayValue, getSettings, saveSettings } from "../api/console";
import { ConsolePageLayout } from "../components/layout/ConsolePageLayout";
import { SecretField } from "../components/forms/SecretField";
import { SnapshotBadge } from "../components/status/SnapshotBadge";

const sections = [
  ["status", "Status"], ["info", "Info"], ["configuration", "Configuration"], ["agent-runtime", "Agent Runtime"],
  ["prompts", "Prompts"], ["connectors", "Connectors"], ["audit-rules", "Audit Rules"], ["attention", "Attention"],
] as const;

function fieldEntries(value: Record<string, unknown>) {
  return Object.entries(value).filter(([key]) => !["secrets", "meta", "snapshot_at"].includes(key));
}

export function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const section = params.get("tab") || "status";
  const prompt = params.get("prompt") || "developer";
  const [payload, setPayload] = useState<Record<string, unknown> | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    getSettings(section, controller.signal).then((response) => {
      setPayload(response.item);
      const fields = response.item.fields;
      setDraft(fields && typeof fields === "object" && !Array.isArray(fields) ? Object.fromEntries(Object.entries(fields as Record<string, unknown>).map(([key, value]) => [key, displayValue(value)])) : {});
      setSnapshot(response.meta.snapshot_at);
      setState("ready");
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : "加载失败");
      setState("error");
    });
    return () => controller.abort();
  }, [section]);
  const label = sections.find(([key]) => key === section)?.[1] || "Settings";
  async function save() {
    setSaveState("saving");
    try { let fields: Record<string, string> = section === "prompts" ? { template: draft[`${prompt}_template`] || draft.template || "" } : { ...draft }; if (section === "agent-runtime") { fields = { ...fields, codex_api_token: draft.CEO_CODEX_API_KEY || "", friday_runtime_ticket: draft.CEO_FRIDAY_RUNTIME_TICKET || "", friday_session_token: draft.CEO_FRIDAY_SESSION_TOKEN || "" }; } await saveSettings(section, fields); setSaveState("saved"); } catch { setSaveState("error"); }
  }
  return (
    <ConsolePageLayout title={label} actions={<SnapshotBadge timestamp={snapshot} refreshing={state === "loading"} />}>
      <div className="settings-layout-react">
        <nav className="settings-nav-react" aria-label="Settings sections">
          {sections.map(([key, text]) => <NavLink key={key} to={`/settings?tab=${key}`} className={({ isActive }) => isActive || section === key ? "active" : ""} onClick={() => setParams({ tab: key })}>{text}</NavLink>)}
        </nav>
        <section className="console-card settings-content" aria-live="polite">
          {state === "loading" && <div className="page-state" role="status">正在加载…</div>}
          {state === "error" && <div className="page-state page-state-error" role="alert">{error}</div>}
          {state === "ready" && payload && <>
            {section === "prompts" && <div className="settings-tabs" role="tablist" aria-label="Prompt type"><button type="button" role="tab" aria-selected={prompt === "developer"} className={prompt === "developer" ? "active" : ""} onClick={() => setParams({ tab: "prompts", prompt: "developer" })}>Developer</button><button type="button" role="tab" aria-selected={prompt === "user"} className={prompt === "user" ? "active" : ""} onClick={() => setParams({ tab: "prompts", prompt: "user" })}>User</button></div>}
            <div className="settings-field-list">
              {fieldEntries((payload.fields && typeof payload.fields === "object" && !Array.isArray(payload.fields) ? payload.fields : payload) as Record<string, unknown>).filter(([key]) => section !== "prompts" || !["developer_template", "user_template"].includes(key)).map(([key, value]) => <div className="settings-field-row" key={key}><div><label htmlFor={`settings-${key}`}><strong>{key}</strong></label>{section === "audit-rules" && key === "template" || section === "prompts" && key.includes("template") ? <textarea id={`settings-${key}`} rows={12} value={draft[key] ?? displayValue(value)} onChange={(event) => setDraft((current) => ({ ...current, [key]: event.target.value }))} /> : <input id={`settings-${key}`} value={draft[key] ?? displayValue(value)} onChange={(event) => setDraft((current) => ({ ...current, [key]: event.target.value }))} />}<p className="muted">当前值：{section === "prompts" && key.includes("template") ? "由运行时注入变量；预览使用 sample context" : displayValue(value)}</p></div></div>)}
              {section === "prompts" && <div className="settings-field-row"><div><label htmlFor="prompt-template"><strong>{prompt === "user" ? "User" : "Developer"} template</strong></label><textarea id="prompt-template" rows={16} value={draft[`${prompt}_template`] || ""} onChange={(event) => setDraft((current) => ({ ...current, [`${prompt}_template`]: event.target.value }))} /><div className="prompt-variable-chips" aria-label="Available runtime variables"><span>由运行时注入</span><code>{"{{principal}}"}</code><code>{"{{conversation}}"}</code></div></div></div>}
            </div>
            {Array.isArray(payload.secrets) && <div className="settings-secrets">{payload.secrets.map((secret) => { const name = displayValue(secret); return <SecretField key={name} id={name} label={name} configured value={draft[name] || ""} onChange={(value) => setDraft((current) => ({ ...current, [name]: value }))} />; })}</div>}
            {!["status", "attention", "info", "connectors"].includes(section) && <div className="settings-save-row"><button type="button" className="primary-button" onClick={() => void save()} disabled={saveState === "saving"}>{saveState === "saving" ? "保存中…" : "保存"}</button>{saveState === "saved" && <span role="status">已保存</span>}{saveState === "error" && <span role="alert">保存失败，草稿仍保留</span>}</div>}
          </>}
        </section>
      </div>
    </ConsolePageLayout>
  );
}
