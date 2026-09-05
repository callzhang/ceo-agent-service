import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getSkillFeatures, toggleSkillFeature, type SkillFeature } from "../../api/console";
import { createManagedSkillRevision, createRuntimeSkillConfig, getCurrentRuntimeSkillConfig, listManagedSkillRevisions, listManagedSkills, listRuntimeSkillLoadReceipts, type ManagedSkill, type ManagedSkillRevision, type RuntimeSkillBinding, type RuntimeSkillConfig, type RuntimeSkillLoadReceipt } from "../../api/skills";

function errorMessage(reason: unknown, fallback: string) { return reason instanceof Error && reason.message ? reason.message : fallback; }

export function ManagedSkillsPanel() {
  const [features, setFeatures] = useState<SkillFeature[]>([]);
  const [skills, setSkills] = useState<ManagedSkill[]>([]);
  const [revisions, setRevisions] = useState<Record<number, ManagedSkillRevision[]>>({});
  const [config, setConfig] = useState<RuntimeSkillConfig | null>(null);
  const [activeConfig, setActiveConfig] = useState<RuntimeSkillConfig | null>(null);
  const [receipts, setReceipts] = useState<RuntimeSkillLoadReceipt[]>([]);
  const [selectedSkillId, setSelectedSkillId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [creating, setCreating] = useState(false);
  const [mode, setMode] = useState<"preview" | "edit">("preview");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [activatingRevisionId, setActivatingRevisionId] = useState<number | null>(null);
  const [toggling, setToggling] = useState<Set<string>>(new Set());
  const toggleSequence = useRef(new Map<string, number>());

  async function load(signal?: AbortSignal) {
    setLoading(true); setError("");
    try {
      const [featureResult, skillResult, configResult] = await Promise.all([getSkillFeatures(signal), listManagedSkills(signal), getCurrentRuntimeSkillConfig(signal)]);
      if (signal?.aborted) return;
      setFeatures(featureResult.features || []); setSkills(skillResult.items || []); setConfig(configResult.pending_or_active); setActiveConfig(configResult.active);
      const revisionPairs = await Promise.all((skillResult.items || []).map(async (skill) => [skill.id, (await listManagedSkillRevisions(skill.id, signal)).items] as const));
      if (signal?.aborted) return;
      setRevisions(Object.fromEntries(revisionPairs)); setSelectedSkillId((current) => current ?? skillResult.items?.[0]?.id ?? null);
      setReceipts(configResult.pending_or_active ? (await listRuntimeSkillLoadReceipts(configResult.pending_or_active.id, signal)).items || [] : []);
    } catch (reason) { if (!signal?.aborted) setError(errorMessage(reason, "Skills 加载失败")); }
    finally { if (!signal?.aborted) setLoading(false); }
  }

  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, []);
  const selectedSkill = skills.find((skill) => skill.id === selectedSkillId) || null;
  const selectedRevisions = selectedSkill ? revisions[selectedSkill.id] || [] : [];
  const currentRevision = selectedRevisions.at(-1) || null;

  async function saveRevision() {
    if (!selectedSkill || !currentRevision || saving) return;
    setSaving(true); setError(""); setMessage("");
    try { const revision = await createManagedSkillRevision(selectedSkill.id, draft, currentRevision.id); setRevisions((current) => ({ ...current, [selectedSkill.id]: [...(current[selectedSkill.id] || []), revision] })); setDraft(revision.content); setCreating(false); setMode("preview"); setMessage(`已保存 revision ${revision.revision_number}`); }
    catch (reason) { setError(errorMessage(reason, "保存修订失败")); }
    finally { setSaving(false); }
  }

  async function activate(revision: ManagedSkillRevision) {
    if (!config || activatingRevisionId !== null) return;
    setActivatingRevisionId(revision.id); setError(""); setMessage("");
    const bindings: RuntimeSkillBinding[] = config.bindings.map((binding) => binding.skill_id === revision.skill_id ? { ...binding, revision_id: revision.id } : binding);
    if (!bindings.some((binding) => binding.skill_id === revision.skill_id)) bindings.push({ skill_id: revision.skill_id, revision_id: revision.id, enabled: true, load_order: bindings.length, purpose: "business" });
    try { const next = await createRuntimeSkillConfig(config.id, bindings); setConfig(next); setReceipts([]); setMessage("配置已更新，等待服务重启"); }
    catch (reason) { setError(errorMessage(reason, "更新下次启动配置失败")); }
    finally { setActivatingRevisionId(null); }
  }

  async function changeFeature(feature: SkillFeature, enabled: boolean) {
    if (toggling.has(feature.feature_id)) return;
    const sequence = (toggleSequence.current.get(feature.feature_id) || 0) + 1;
    toggleSequence.current.set(feature.feature_id, sequence); setToggling((current) => new Set(current).add(feature.feature_id)); setError(""); setMessage("");
    try { const saved = await toggleSkillFeature(feature.feature_id, enabled); if (toggleSequence.current.get(feature.feature_id) === sequence) { setFeatures((current) => current.map((item) => item.feature_id === feature.feature_id ? { ...item, enabled: saved.enabled, status: saved.status } : item)); setMessage("新任务创建开关已保存"); } }
    catch (reason) { if (toggleSequence.current.get(feature.feature_id) === sequence) setError(errorMessage(reason, "新任务创建开关保存失败")); }
    finally { if (toggleSequence.current.get(feature.feature_id) === sequence) setToggling((current) => { const next = new Set(current); next.delete(feature.feature_id); return next; }); }
  }

  return <section className="console-card settings-content-card">
    <div className="settings-card-heading"><div><h2>Skills</h2><p className="muted">Skill 修订不可变；选择修订后，会在下一次服务启动时加载。</p></div><span className="settings-path">Runtime managed skills</span></div>
    {loading && <div className="page-state" role="status">正在加载 Skills…</div>}{error && <p className="field-error" role="alert">{error}</p>}{message && <p className="save-success" role="status">{message}</p>}
    {!loading && <>
      <section className="managed-skills-group"><h3>新任务创建开关（不控制 Skill 加载）</h3><p className="muted">仅控制新任务创建，不控制 Skill 加载</p><div className="skills-feature-grid">{features.map((feature) => <article className="skills-feature-card" key={feature.feature_id}><div className="skills-feature-heading"><div><h4>{feature.name}</h4><p>{feature.description}</p></div><label className="skills-toggle"><span className="sr-only">{feature.name}</span><input type="checkbox" role="switch" aria-label={feature.name} checked={feature.enabled} disabled={toggling.has(feature.feature_id)} onChange={(event) => void changeFeature(feature, event.target.checked)} /><span aria-hidden="true" /></label></div></article>)}</div></section>
      <section className="managed-skills-group"><h3>运行时 Skill 修订</h3><p className="muted">当前配置：{config ? `#${config.id}（${config.status}）` : "尚未配置"}；活动配置：{activeConfig ? `#${activeConfig.id}` : "无"}</p><div className="managed-skill-grid">{skills.map((skill) => <article className={`skills-project-row ${selectedSkillId === skill.id ? "is-selected" : ""}`} key={skill.id}><div><strong>{skill.display_name}</strong><p className="muted">{skill.name}</p><p className="skills-references">{(revisions[skill.id] || []).length} 个不可变修订</p></div><button type="button" className="secondary-button" onClick={() => { setSelectedSkillId(skill.id); setCreating(false); setMode("preview"); }}>查看修订</button></article>)}</div>
      {selectedSkill && currentRevision && <div className="skill-editor"><div className="skills-project-heading"><div><h3>{selectedSkill.display_name}</h3><p className="muted">最新 revision {currentRevision.revision_number} · SHA-256: <code>{currentRevision.sha256}</code> · 父修订：{currentRevision.parent_revision_id ?? "无"}</p></div><button type="button" className="primary-button" disabled={saving} onClick={() => { setDraft(currentRevision.content); setCreating(true); setMode("edit"); }}>新建修订</button></div>{!config && <p className="field-error">尚未配置下次启动 Skill；无法启用修订。</p>}<div className="managed-revision-list">{selectedRevisions.map((revision) => <div key={revision.id}><strong>revision {revision.revision_number}</strong><span> SHA-256: <code>{revision.sha256}</code></span>{config && config.bindings.some((binding) => binding.revision_id === revision.id) ? <span className="skills-status">下次启动</span> : <button type="button" className="secondary-button" disabled={!config || activatingRevisionId !== null} onClick={() => void activate(revision)}>下次启动启用 revision {revision.revision_number}</button>}</div>)}</div>{creating && <><div className="settings-pill-row skill-editor-tabs" role="tablist" aria-label="Skill detail view"><button type="button" role="tab" id="managed-skill-preview-tab" aria-controls="managed-skill-preview-panel" tabIndex={mode === "preview" ? 0 : -1} aria-selected={mode === "preview"} className={mode === "preview" ? "active" : ""} onClick={() => setMode("preview")} onKeyDown={(event) => { if (["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(event.key)) { event.preventDefault(); setMode("edit"); document.getElementById("managed-skill-edit-tab")?.focus(); } }}>预览</button><button type="button" role="tab" id="managed-skill-edit-tab" aria-controls="managed-skill-edit-panel" tabIndex={mode === "edit" ? 0 : -1} aria-selected={mode === "edit"} className={mode === "edit" ? "active" : ""} onClick={() => setMode("edit")} onKeyDown={(event) => { if (["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(event.key)) { event.preventDefault(); setMode("preview"); document.getElementById("managed-skill-preview-tab")?.focus(); } }}>编辑</button></div>{mode === "preview" ? <div id="managed-skill-preview-panel" role="tabpanel" aria-labelledby="managed-skill-preview-tab" aria-label="Skill 预览" className="skill-markdown-preview"><ReactMarkdown remarkPlugins={[remarkGfm]}>{draft}</ReactMarkdown></div> : <div id="managed-skill-edit-panel" role="tabpanel" aria-labelledby="managed-skill-edit-tab" aria-label="Skill 编辑"><label className="skill-content-label" htmlFor="managed-skill-content">Skill 正文</label><textarea id="managed-skill-content" aria-label="Skill 正文" disabled={saving} value={draft} onChange={(event) => setDraft(event.target.value)} rows={16} /><div className="skill-editor-actions"><button type="button" className="primary-button" disabled={saving} onClick={() => void saveRevision()}>保存修订</button><button type="button" className="secondary-button" disabled={saving} onClick={() => { setCreating(false); setDraft(currentRevision.content); }}>取消</button></div></div>}</>}</div>}</section>
      <section className="managed-skills-group"><h3>启动加载回执</h3>{receipts.length ? <div className="managed-revision-list">{receipts.map((receipt) => <div key={receipt.id}>配置 #{receipt.config_id} · PID {receipt.pid} · {receipt.error || "已加载"} · {receipt.created_at}</div>)}</div> : <p className="muted">当前配置尚无启动加载回执。</p>}</section>
      <section className="managed-skills-group"><h3>系统能力</h3><article className="skills-feature-card"><h4>反馈迭代</h4><p>用于展示反馈迭代能力状态；不创建任务，也不在此页面执行反馈处理。</p><span className="skills-status">显示中</span></article></section>
    </>}
  </section>;
}
