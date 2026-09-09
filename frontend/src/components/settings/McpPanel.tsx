import { useEffect, useState } from "react";
import { getMcpSettings, saveMcpSettings, type McpServerEntry, type McpSettings } from "../../api/console";
import { StatusBadge } from "../status/StatusBadge";

function errorMessage(reason: unknown, fallback: string) { return reason instanceof Error && reason.message ? reason.message : fallback; }

function describeEntry(entry: McpServerEntry) {
  if (entry.url) return entry.url;
  if (entry.url_env) return `URL 来自环境变量 ${entry.url_env}`;
  if (entry.command) return [entry.command, ...(entry.args || [])].join(" ");
  if (entry.command_env) return `命令来自环境变量 ${entry.command_env}`;
  return "未提供 transport";
}

const AUTH_LABELS: Record<string, string> = { o_auth: "OAuth 已登录", not_logged_in: "未登录", unsupported: "无需登录", unknown: "未知" };

export function McpPanel() {
  const [settings, setSettings] = useState<McpSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveError, setSaveError] = useState("");
  const [newName, setNewName] = useState("");
  const [newTransport, setNewTransport] = useState<"url" | "command">("url");
  const [newLocation, setNewLocation] = useState("");
  const [newArgs, setNewArgs] = useState("");
  const [formError, setFormError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    getMcpSettings(controller.signal)
      .then((response) => { setSettings(response.item); setError(""); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(errorMessage(reason, "MCP 清单加载失败")); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);

  async function persist(servers: Record<string, McpServerEntry>, disabled: string[]) {
    setSaveState("saving"); setSaveError("");
    try {
      const response = await saveMcpSettings(servers, disabled);
      setSettings(response.item); setSaveState("saved");
    } catch (reason) {
      setSaveState("error"); setSaveError(errorMessage(reason, "保存失败"));
    }
  }

  if (loading) return <p className="muted">加载 MCP 清单…</p>;
  if (!settings) return <p className="field-error" role="alert">{error || "MCP 清单不可用"}</p>;

  const disabled = new Set(settings.disabled_servers);
  const toggleAgentAccess = (name: string, allowed: boolean) => {
    const next = new Set(disabled);
    if (allowed) next.delete(name); else next.add(name);
    void persist(settings.servers, [...next].sort());
  };
  const removeServer = (name: string) => {
    const { [name]: _removed, ...rest } = settings.servers;
    void persist(rest, settings.disabled_servers);
  };
  const addServer = (event: React.FormEvent) => {
    event.preventDefault();
    const name = newName.trim();
    const location = newLocation.trim();
    if (!name || !location) { setFormError("名称和地址/命令都必须填写"); return; }
    if (settings.servers[name]) { setFormError("已存在同名服务器"); return; }
    const entry: McpServerEntry = newTransport === "url"
      ? { url: location }
      : { command: location, ...(newArgs.trim() ? { args: newArgs.trim().split(/\s+/) } : {}) };
    setFormError("");
    void persist({ ...settings.servers, [name]: entry }, settings.disabled_servers.filter((item) => item !== name));
    setNewName(""); setNewLocation(""); setNewArgs("");
  };

  return <>
    <div className="settings-card-heading"><div><h2>MCP</h2><p className="muted">后台 Agent 每个 turn 可见的 MCP 服务器。关闭"后台 Agent 可用"会在服务发起的 Codex 命令里禁用该服务器；改动从下一个 Agent turn 起生效，不需要重启。</p></div><span className="settings-path" title={settings.manifest_path}>{settings.manifest_path}</span></div>
    {saveState === "saved" && <p className="save-success" role="status">已保存</p>}
    {saveState === "error" && <p className="save-error" role="alert">{saveError}</p>}

    <section className="managed-skills-group" aria-labelledby="codex-mcp-servers-title">
      <div className="skills-project-heading"><div><h3 id="codex-mcp-servers-title">Codex 全局服务器</h3><p className="muted">来自你的 Codex 配置（含桌面端插件）。这里只控制后台 Agent 能否使用，不修改你的 Codex 配置。</p></div></div>
      {settings.codex_inventory_error && <p className="field-error" role="alert">读取 Codex 配置失败：{settings.codex_inventory_error}</p>}
      <div className="wechat-target-list">
        {settings.codex_servers.map((server) => <label className="wechat-target-row" key={server.name}>
          <input type="checkbox" checked={server.agent_enabled} disabled={!server.enabled || saveState === "saving"} aria-label={`${server.name} 后台 Agent 可用`} onChange={(event) => toggleAgentAccess(server.name, event.target.checked)} />
          <span className="wechat-target-copy"><strong>{server.name}</strong><small>{server.transport_type} · {server.location || "未提供地址"} · {AUTH_LABELS[server.auth_status] || server.auth_status}{!server.enabled ? " · 已在 Codex 中禁用" : ""}</small></span>
          <StatusBadge value={server.agent_enabled ? "active" : "disabled"} />
        </label>)}
        {!settings.codex_servers.length && !settings.codex_inventory_error && <p className="wechat-empty">Codex 配置里没有 MCP 服务器。</p>}
      </div>
    </section>

    <section className="managed-skills-group" aria-labelledby="service-mcp-servers-title">
      <div className="skills-project-heading"><div><h3 id="service-mcp-servers-title">服务清单服务器</h3><p className="muted">由本服务自己声明并注入到每个 Agent turn 的服务器。密钥只能通过环境变量名引用。</p></div></div>
      <div className="wechat-target-list">
        {Object.entries(settings.servers).map(([name, entry]) => <div className="wechat-target-row" key={name}>
          <span className="wechat-target-copy"><strong>{name}</strong><small>{describeEntry(entry)}</small></span>
          <button type="button" className="secondary-button" disabled={saveState === "saving"} onClick={() => removeServer(name)}>移除</button>
        </div>)}
        {!Object.keys(settings.servers).length && <p className="wechat-empty">服务清单里还没有服务器。</p>}
      </div>
      <form className="runtime-fields" onSubmit={addServer} aria-label="添加服务清单服务器">
        <label>名称<input value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="例如 exa" /></label>
        <label>类型<select value={newTransport} onChange={(event) => setNewTransport(event.target.value as "url" | "command")}><option value="url">HTTP URL</option><option value="command">本地命令</option></select></label>
        <label>{newTransport === "url" ? "URL" : "命令"}<input value={newLocation} onChange={(event) => setNewLocation(event.target.value)} placeholder={newTransport === "url" ? "https://…/mcp" : "/usr/local/bin/my-mcp"} /></label>
        {newTransport === "command" && <label>参数（空格分隔）<input value={newArgs} onChange={(event) => setNewArgs(event.target.value)} placeholder="serve --stdio" /></label>}
        {formError && <p className="field-error" role="alert">{formError}</p>}
        <div className="settings-save-row"><button type="submit" className="primary-button" disabled={saveState === "saving"}>添加</button></div>
      </form>
    </section>
  </>;
}
