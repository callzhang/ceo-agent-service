import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getSkillFeatures, toggleSkillFeature, type SkillFeature } from "../../api/console";
import { createManagedSkill, createManagedSkillRevision, createRuntimeSkillConfig, exportManagedSkillRevision, getCurrentRuntimeSkillConfig, getFeedbackIterationCapability, listManagedSkillRevisions, listManagedSkills, listRuntimeSkillLoadReceipts, setFeedbackIterationCapability, type FeedbackIterationCapability, type ManagedSkill, type ManagedSkillRevision, type RuntimeSkillBinding, type RuntimeSkillConfig, type RuntimeSkillLoadReceipt } from "../../api/skills";

function errorMessage(reason: unknown, fallback: string) { return reason instanceof Error && reason.message ? reason.message : fallback; }

export function ManagedSkillsPanel() {
  const [features, setFeatures] = useState<SkillFeature[]>([]);
  const [skills, setSkills] = useState<ManagedSkill[]>([]);
  const [revisions, setRevisions] = useState<Record<number, ManagedSkillRevision[]>>({});
  const [config, setConfig] = useState<RuntimeSkillConfig | null>(null);
  const [activeConfig, setActiveConfig] = useState<RuntimeSkillConfig | null>(null);
  const [receipts, setReceipts] = useState<RuntimeSkillLoadReceipt[]>([]);
  const [receiptsByConfig, setReceiptsByConfig] = useState<Record<number, RuntimeSkillLoadReceipt[]>>({});
  const [selectedFeatureId, setSelectedFeatureId] = useState<string | null>(null);
  const [selectedSkillId, setSelectedSkillId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [creating, setCreating] = useState(false);
  const [newSkill, setNewSkill] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDisplayName, setNewDisplayName] = useState("");
  const [capability, setCapability] = useState<FeedbackIterationCapability | null>(null);
  const [mode, setMode] = useState<"preview" | "edit">("preview");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [activatingRevisionId, setActivatingRevisionId] = useState<number | null>(null);
  const [exportingRevisionId, setExportingRevisionId] = useState<number | null>(null);
  const [toggling, setToggling] = useState<Set<string>>(new Set());
  const toggleSequence = useRef(new Map<string, number>());

  async function load(signal?: AbortSignal) {
    setLoading(true); setError("");
    try {
      const [featureResult, skillResult, configResult, capabilityResult] = await Promise.all([getSkillFeatures(signal), listManagedSkills(signal), getCurrentRuntimeSkillConfig(signal), getFeedbackIterationCapability(signal)]);
      if (signal?.aborted) return;
      setFeatures(featureResult.features || []); setSkills(skillResult.items || []); setConfig(configResult.pending_or_active); setActiveConfig(configResult.active); setCapability(capabilityResult);
      const revisionPairs = await Promise.all((skillResult.items || []).map(async (skill) => [skill.id, (await listManagedSkillRevisions(skill.id, signal)).items] as const));
      if (signal?.aborted) return;
      setRevisions(Object.fromEntries(revisionPairs));
      const configIds = [...new Set([configResult.pending_or_active?.id, configResult.active?.id].filter((id): id is number => typeof id === "number"))];
      const receiptPairs = await Promise.all(configIds.map(async (id) => [id, (await listRuntimeSkillLoadReceipts(id, signal)).items || []] as const));
      if (signal?.aborted) return;
      const byConfig = Object.fromEntries(receiptPairs); setReceiptsByConfig(byConfig); setReceipts(configResult.pending_or_active ? byConfig[configResult.pending_or_active.id] || [] : []);
    } catch (reason) { if (!signal?.aborted) setError(errorMessage(reason, "Skills 加载失败")); }
    finally { if (!signal?.aborted) setLoading(false); }
  }

  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, []);
  const selectedFeature = features.find((feature) => feature.feature_id === selectedFeatureId) || null;
  const visibleSkills = selectedFeature ? skills.filter((skill) => selectedFeature.skills.includes(skill.name)) : [];
  const selectedSkill = skills.find((skill) => skill.id === selectedSkillId) || null;
  const selectedRevisions = selectedSkill ? revisions[selectedSkill.id] || [] : [];
  const currentRevision = selectedRevisions.at(-1) || null;
  const activeBinding = activeConfig?.bindings.find((binding) => binding.skill_id === selectedSkillId && binding.enabled) || null;
  const nextStartBinding = config?.bindings.find((binding) => binding.skill_id === selectedSkillId && binding.enabled) || null;
  const activeRevision = selectedRevisions.find((revision) => revision.id === activeBinding?.revision_id) || null;
  const nextStartRevision = selectedRevisions.find((revision) => revision.id === nextStartBinding?.revision_id) || null;
  const matchingActiveReceipt = activeBinding && activeConfig ? (receiptsByConfig[activeConfig.id] || []).find((receipt) => {
    try { const loaded = JSON.parse(receipt.loaded_json) as Record<string, unknown>; return !receipt.error && loaded[String(activeBinding.skill_id)] === activeRevision?.sha256; } catch { return false; }
  }) || null : null;

  async function saveRevision() {
    if (!selectedSkill || saving) return;
    setSaving(true); setError(""); setMessage("");
    try { const revision = await createManagedSkillRevision(selectedSkill.id, draft, currentRevision?.id ?? null); setRevisions((current) => ({ ...current, [selectedSkill.id]: [...(current[selectedSkill.id] || []), revision] })); setDraft(revision.content); setCreating(false); setMode("preview"); setMessage(`已保存 revision ${revision.revision_number}`); }
    catch (reason) { setError(errorMessage(reason, "保存修订失败")); }
    finally { setSaving(false); }
  }

  async function createLocalSkill() {
    if (saving || !newName.trim() || !newDisplayName.trim()) return;
    setSaving(true); setError(""); setMessage("");
    try {
      const skill = await createManagedSkill(newName.trim(), newDisplayName.trim());
      setSkills((current) => [...current, skill]); setRevisions((current) => ({ ...current, [skill.id]: [] }));
      setSelectedSkillId(skill.id); setDraft(`---\nname: ${skill.name}\ndescription: ${skill.display_name}\nmetadata:\n  managed_by: ceo-agent-service\n---\n\n# ${skill.display_name}\n`);
      setCreating(true); setNewSkill(false); setMode("edit"); setMessage("本地 Skill 已创建；保存正文会生成第一个不可变修订。");
    } catch (reason) { setError(errorMessage(reason, "创建本地 Skill 失败")); }
    finally { setSaving(false); }
  }

  async function changeFeedbackCapability(enabled: boolean) {
    if (saving) return;
    setSaving(true); setError(""); setMessage("");
    try { const next = await setFeedbackIterationCapability(enabled); setCapability(next); setMessage("反馈迭代已更新为下次启动配置"); }
    catch (reason) { setError(errorMessage(reason, "更新反馈迭代状态失败")); }
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

  async function exportRevision(revision: ManagedSkillRevision) {
    if (exportingRevisionId !== null) return;
    setExportingRevisionId(revision.id); setError(""); setMessage("");
    try {
      const result = await exportManagedSkillRevision(revision.id);
      setRevisions((current) => ({ ...current, [revision.skill_id]: (current[revision.skill_id] || []).map((item) => item.id === revision.id ? { ...item, export: result.export } : item) }));
      setMessage(`已导出 revision ${revision.revision_number}`);
    } catch (reason) { setError(errorMessage(reason, "导出 Skill 失败")); }
    finally { setExportingRevisionId(null); }
  }

  async function changeFeature(feature: SkillFeature, enabled: boolean) {
    if (toggling.has(feature.feature_id)) return;
    const sequence = (toggleSequence.current.get(feature.feature_id) || 0) + 1;
    toggleSequence.current.set(feature.feature_id, sequence); setToggling((current) => new Set(current).add(feature.feature_id)); setError(""); setMessage("");
    try { const saved = await toggleSkillFeature(feature.feature_id, enabled); if (toggleSequence.current.get(feature.feature_id) === sequence) { setFeatures((current) => current.map((item) => item.feature_id === feature.feature_id ? { ...item, enabled: saved.enabled, status: saved.status } : item)); setMessage("新任务创建开关已保存"); } }
    catch (reason) { if (toggleSequence.current.get(feature.feature_id) === sequence) setError(errorMessage(reason, "新任务创建开关保存失败")); }
    finally { if (toggleSequence.current.get(feature.feature_id) === sequence) setToggling((current) => { const next = new Set(current); next.delete(feature.feature_id); return next; }); }
  }

  function toggleLabel(feature: SkillFeature) {
    return feature.feature_id === "wechat_auto_reply" ? "启用微信自动回复" : feature.name;
  }

  return <section className="console-card settings-content-card">
    <div className="settings-card-heading"><div><h2>Skills</h2><p className="muted">选择一个功能，只查看和编辑它关联的 Skills。功能开关只影响新任务。</p></div></div>
    {loading && <div className="page-state" role="status">正在加载 Skills…</div>}{error && <p className="field-error" role="alert">{error}</p>}{message && <p className="save-success" role="status">{message}</p>}
    {!loading && <>
      <section className="managed-skills-group"><div className="skills-feature-grid">{features.map((feature) => { const selected = selectedFeatureId === feature.feature_id; return <article className={`skills-feature-card ${selected ? "is-selected" : ""}`} key={feature.feature_id} role="button" tabIndex={0} aria-label={`选择 ${feature.name}`} aria-pressed={selected} onClick={() => { setSelectedFeatureId(feature.feature_id); setSelectedSkillId(skills.find((skill) => feature.skills.includes(skill.name))?.id ?? null); setCreating(false); setMode("preview"); }} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelectedFeatureId(feature.feature_id); setSelectedSkillId(skills.find((skill) => feature.skills.includes(skill.name))?.id ?? null); setCreating(false); setMode("preview"); } }}><div className="skills-feature-heading"><div><h4>{feature.name}</h4><p>{feature.description}</p></div><label className="skills-toggle" onClick={(event) => event.stopPropagation()}><span className="sr-only">{toggleLabel(feature)}</span><input type="checkbox" role="switch" aria-label={toggleLabel(feature)} checked={feature.enabled} disabled={toggling.has(feature.feature_id)} onChange={(event) => void changeFeature(feature, event.target.checked)} /><span className="skills-toggle-track" aria-hidden="true" /></label></div></article>; })}</div></section>
      {!selectedFeature && <div className="skills-selection-empty">选择一个功能以查看它关联的 Skills</div>}
      {selectedFeature && <section className="managed-skills-group"><div className="skills-project-heading"><div><h3>{selectedFeature.name} · Skills</h3><p className="muted">只显示该功能关联的 Skills。</p></div></div>{selectedFeature.feature_id === "wechat_auto_reply" && <section className="wechat-skill-configuration" aria-labelledby="wechat-skill-configuration-title"><div><h4 id="wechat-skill-configuration-title">自动回复配置</h4><p className="muted">此开关控制是否由新微信消息创建回复任务。关闭后继续读取消息并保留已选对象；已生成的待发送内容保持原状态。</p><a href="/settings?tab=connectors&connector=wechat">管理自动回复对象</a></div><label className="email-account-switch"><span>{selectedFeature.enabled ? "已开启" : "已关闭"}</span><input type="checkbox" role="switch" aria-label="启用微信自动回复" checked={selectedFeature.enabled} disabled={toggling.has(selectedFeature.feature_id)} onChange={(event) => void changeFeature(selectedFeature, event.target.checked)} /></label></section>}<div className="managed-skill-grid">{visibleSkills.map((skill) => <article className={`skills-project-row ${selectedSkillId === skill.id ? "is-selected" : ""}`} key={skill.id}><div><strong>{skill.display_name}</strong><p className="muted">{skill.name}</p></div><button type="button" className="secondary-button" onClick={() => { setSelectedSkillId(skill.id); setCreating(false); setMode("preview"); }}>查看 Skill</button></article>)}</div>
      {selectedSkill && <div className="skill-editor"><div className="skills-project-heading"><div><h3>{selectedSkill.display_name}</h3><p className="muted">{currentRevision ? `当前版本 revision ${currentRevision.revision_number}` : "尚无内容"}</p></div><button type="button" className="primary-button" disabled={saving} onClick={() => { setDraft(currentRevision?.content || draft); setCreating(true); setMode("edit"); }}>编辑 Skill</button></div><div className="settings-pill-row skill-editor-tabs" role="tablist" aria-label="Skill detail view"><button type="button" role="tab" id="managed-skill-preview-tab" aria-controls="managed-skill-preview-panel" tabIndex={mode === "preview" ? 0 : -1} aria-selected={mode === "preview"} className={mode === "preview" ? "active" : ""} onClick={() => { setDraft(currentRevision?.content || draft); setMode("preview"); }} onKeyDown={(event) => { if (["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(event.key)) { event.preventDefault(); setDraft(currentRevision?.content || draft); setCreating(true); setMode("edit"); document.getElementById("managed-skill-edit-tab")?.focus(); } }}>预览</button><button type="button" role="tab" id="managed-skill-edit-tab" aria-controls="managed-skill-edit-panel" tabIndex={mode === "edit" ? 0 : -1} aria-selected={mode === "edit"} className={mode === "edit" ? "active" : ""} onClick={() => { setDraft(currentRevision?.content || draft); setCreating(true); setMode("edit"); }} onKeyDown={(event) => { if (["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(event.key)) { event.preventDefault(); setMode("preview"); document.getElementById("managed-skill-preview-tab")?.focus(); } }}>编辑</button></div>{mode === "preview" ? <div id="managed-skill-preview-panel" role="tabpanel" aria-labelledby="managed-skill-preview-tab" aria-label="Skill 预览" className="skill-markdown-preview"><ReactMarkdown remarkPlugins={[remarkGfm]}>{currentRevision?.content || "暂无内容"}</ReactMarkdown></div> : <div id="managed-skill-edit-panel" role="tabpanel" aria-labelledby="managed-skill-edit-tab" aria-label="Skill 编辑"><label className="skill-content-label" htmlFor="managed-skill-content">Skill 正文</label><textarea id="managed-skill-content" aria-label="Skill 正文" disabled={saving} value={draft} onChange={(event) => setDraft(event.target.value)} rows={16} /><div className="skill-editor-actions"><button type="button" className="primary-button" disabled={saving} onClick={() => void saveRevision()}>保存 Skill</button><button type="button" className="secondary-button" disabled={saving} onClick={() => { setCreating(false); setMode("preview"); setDraft(currentRevision?.content || ""); }}>取消</button></div></div>}</div>}</section>}
    </>}
  </section>;
}
