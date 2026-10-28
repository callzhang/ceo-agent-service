import { useEffect, useRef, useState, type ReactElement } from "react";
import { installSkill, searchSkills, type RepositorySkill, type SkillInstallResult, type SkillSearchResult } from "../../api/skills";

function identity(skill: RepositorySkill) { return JSON.stringify([skill.repo_url, skill.commit, skill.skill_path]); }
function modifiedAt(value: string | null) {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? `更新于 ${date.toLocaleString()}` : "更新时间未知";
}

export function SkillSearchPanel({onInstalled}: {onInstalled: (result: SkillInstallResult) => void}): ReactElement {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<SkillSearchResult | null>(null);
  const [error, setError] = useState("");
  const [searching, setSearching] = useState(false);
  const [installing, setInstalling] = useState<Set<string>>(new Set());
  const [installed, setInstalled] = useState<Record<string, SkillInstallResult>>({});
  const [installErrors, setInstallErrors] = useState<Record<string, string>>({});
  const requestId = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const busy = useRef(new Set<string>());
  useEffect(() => () => { requestId.current++; controller.current?.abort(); }, []);

  async function search() {
    if (!query.trim()) return;
    const id = ++requestId.current;
    controller.current?.abort();
    controller.current = new AbortController();
    setSearching(true); setError(""); setResult(null);
    try {
      const found = await searchSkills(query.trim(), controller.current.signal);
      if (id === requestId.current) setResult(found);
    } catch (reason) {
      if (id === requestId.current) setError(reason instanceof Error ? reason.message : "搜索失败");
    } finally { if (id === requestId.current) setSearching(false); }
  }

  async function install(skill: RepositorySkill) {
    const key = identity(skill);
    if (!skill.available || busy.current.has(key) || installed[key]) return;
    busy.current.add(key); setInstalling(new Set(busy.current));
    setInstallErrors(current => ({...current, [key]: ""}));
    try {
      const saved = await installSkill(skill);
      setInstalled(current => ({...current, [key]: saved}));
      onInstalled(saved);
    } catch (reason) {
      setInstallErrors(current => ({...current, [key]: reason instanceof Error ? reason.message : "安装失败"}));
    } finally { busy.current.delete(key); setInstalling(new Set(busy.current)); }
  }

  return <section className="managed-skills-group skill-search-panel" aria-label="搜索和安装 Skills">
    <form className="skill-search-form" onSubmit={event => { event.preventDefault(); void search(); }}>
      <label htmlFor="skill-search-query">搜索 Skills</label>
      <div className="skill-search-controls"><input id="skill-search-query" type="text" placeholder="输入 repo URL 或公开 Skill 名称" value={query} onChange={event => setQuery(event.target.value)} /><button type="submit" className="primary-button" disabled={!query.trim()}>搜索</button></div>
    </form>
    {searching && <p role="status">正在搜索 Skills…</p>}
    {error && <p role="alert" className="field-error">{error}</p>}
    {result && <>
      {result.items.length === 0 && result.errors.length === 0 && <p>没有找到 Skills</p>}
      {result.errors.map((item, index) => <p role="alert" className="field-error" key={`${item.source}-${index}`}>{item.source}: {item.message}</p>)}
      <div className="managed-skill-grid">{result.items.map((skill, index) => {
        const key = identity(skill), saved = installed[key];
        return <article className="skills-project-row" aria-label={skill.name} key={`${key}-${index}`}>
          <div><strong>{skill.name}</strong><p className="skill-search-description" title={skill.description}>{skill.description || "说明未提供"}</p>
            <p className="muted">{skill.author ? `${skill.author_kind === "author" ? "作者" : "维护者"}：${skill.author}` : "作者未提供"}</p>
            <p className="muted">{modifiedAt(skill.updated_at)}</p><p className="muted">{skill.repo_url}</p>
            {skill.unavailable_reason && <p className="field-error">{skill.unavailable_reason}</p>}
            {installErrors[key] && <p role="alert" className="field-error">{installErrors[key]}</p>}
          </div>
          <button type="button" className="secondary-button" disabled={!skill.available || installing.has(key) || !!saved} onClick={() => void install(skill)}>{saved ? saved.activation_status === "active" ? "已安装 · 已加载" : "已安装 · 待加载" : installing.has(key) ? "Installing…" : "Install"}</button>
        </article>;
      })}</div>
    </>}
  </section>;
}
