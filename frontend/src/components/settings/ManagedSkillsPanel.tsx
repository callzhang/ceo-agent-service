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
      setRevisions(Object.fromEntries(revisionPairs)); setSelectedSkillId((current) => current ?? skillResult.items?.[0]?.id ?? null);
      const configIds = [...new Set([configResult.pending_or_active?.id, configResult.active?.id].filter((id): id is number => typeof id === "number"))];
      const receiptPairs = await Promise.all(configIds.map(async (id) => [id, (await listRuntimeSkillLoadReceipts(id, signal)).items || []] as const));
      if (signal?.aborted) return;
      const byConfig = Object.fromEntries(receiptPairs); setReceiptsByConfig(byConfig); setReceipts(configResult.pending_or_active ? byConfig[configResult.pending_or_active.id] || [] : []);
    } catch (reason) { if (!signal?.aborted) setError(errorMessage(reason, "Skills 加载失败")); }
    finally { if (!signal?.aborted) setLoading(false); }
  }

  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, []);
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

  return <section className="console-card settings-content-card">
    <div className="settings-card-heading"><div><h2>Skills</h2><p className="muted">Skill 修订不可变；选择修订后，会在下一次服务启动时加载。</p></div><span className="settings-path">Runtime managed skills</span></div>
    {loading && <div className="page-state" role="status">正在加载 Skills…</div>}{error && <p className="field-error" role="alert">{error}</p>}{message && <p className="save-success" role="status">{message}</p>}
    {!loading && <>
      <section className="managed-skills-group"><h3>新任务创建开关（不控制 Skill 加载）</h3><p className="muted">仅控制新任务创建，不控制 Skill 加载</p><div className="skills-feature-grid">{features.map((feature) => <article className="skills-feature-card" key={feature.feature_id}><div className="skills-feature-heading"><div><h4>{feature.name}</h4><p>{feature.description}</p></div><label className="skills-toggle"><span className="sr-only">{feature.name}</span><input type="checkbox" role="switch" aria-label={feature.name} checked={feature.enabled} disabled={toggling.has(feature.feature_id)} onChange={(event) => void changeFeature(feature, event.target.checked)} /><span aria-hidden="true" /></label></div></article>)}</div></section>
      <section className="managed-skills-group"><div className="skills-project-heading"><div><h3>业务 Skills</h3><p className="muted">当前配置：{config ? `#${config.id}（${config.status}）` : "尚未配置"}；活动配置：{activeConfig ? `#${activeConfig.id}` : "无"}</p></div><button type="button" className="primary-button" onClick={() => setNewSkill(true)}>新建本地 Skill</button></div>{newSkill && <div className="skill-editor"><label>Skill 名称<input aria-label="Skill 名称" value={newName} onChange={(event) => setNewName(event.target.value)} /></label><label>显示名称<input aria-label="显示名称" value={newDisplayName} onChange={(event) => setNewDisplayName(event.target.value)} /></label><button type="button" className="primary-button" disabled={saving || !newName.trim() || !newDisplayName.trim()} onClick={() => void createLocalSkill()}>创建本地 Skill</button></div>}<div className="managed-skill-grid">{skills.filter((skill) => skill.name !== "ceo-feedback-iteration").map((skill) => <article className={`skills-project-row ${selectedSkillId === skill.id ? "is-selected" : ""}`} key={skill.id}><div><strong>{skill.display_name}</strong><p className="muted">{skill.name}</p><p className="skills-references">{(revisions[skill.id] || []).length} 个不可变修订</p></div><button type="button" className="secondary-button" onClick={() => { setSelectedSkillId(skill.id); setCreating(false); setMode("preview"); }}>查看修订</button></article>)}</div>
      {selectedSkill && <div className="skill-editor"><div className="skills-project-heading"><div><h3>{selectedSkill.display_name}</h3><p className="muted">{currentRevision ? `最新 revision ${currentRevision.revision_number} · SHA-256: ${currentRevision.sha256} · 父修订：${currentRevision.parent_revision_id ?? "无"}` : "尚无修订；请保存第一个不可变修订。"}</p><p className="muted">{activeRevision ? `活动 revision ${activeRevision.revision_number} · ${matchingActiveReceipt ? `已由加载回执 #${matchingActiveReceipt.id} 确认` : "尚无匹配加载回执"}` : "当前没有活动 revision"}</p><p className="muted">{nextStartRevision ? `下次启动 revision ${nextStartRevision.revision_number} · ${config?.status}` : "下次启动未启用"}</p></div><button type="button" className="primary-button" disabled={saving} onClick={() => { setDraft(currentRevision?.content || draft); setCreating(true); setMode("edit"); }}>新建修订</button></div>{!config && <p className="field-error">尚未配置下次启动 Skill；无法启用修订。</p>}<div className="managed-revision-list">{selectedRevisions.map((revision) => <div key={revision.id}><strong>revision {revision.revision_number}</strong><span> SHA-256: <code>{revision.sha256}</code></span>{config && config.bindings.some((binding) => binding.revision_id === revision.id) ? <span className="skills-status">下次启动</span> : <button type="button" className="secondary-button" disabled={!config || activatingRevisionId !== null} onClick={() => void activate(revision)}>下次启动启用 revision {revision.revision_number}</button>}<span className="skills-status">导出状态：{revision.export?.status === "exported" ? `已导出 · 导出回执 #${revision.export.receipt?.id}` : "未导出"}</span>{revision.export?.status !== "exported" && <button type="button" className="secondary-button" disabled={exportingRevisionId !== null} onClick={() => void exportRevision(revision)}>导出 revision {revision.revision_number} 到仓库</button>}</div>)}</div>{creating && <><div className="settings-pill-row skill-editor-tabs" role="tablist" aria-label="Skill detail view"><button type="button" role="tab" id="managed-skill-preview-tab" aria-controls="managed-skill-preview-panel" tabIndex={mode === "preview" ? 0 : -1} aria-selected={mode === "preview"} className={mode === "preview" ? "active" : ""} onClick={() => setMode("preview")} onKeyDown={(event) => { if (["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(event.key)) { event.preventDefault(); setMode("edit"); document.getElementById("managed-skill-edit-tab")?.focus(); } }}>预览</button><button type="button" role="tab" id="managed-skill-edit-tab" aria-controls="managed-skill-edit-panel" tabIndex={mode === "edit" ? 0 : -1} aria-selected={mode === "edit"} className={mode === "edit" ? "active" : ""} onClick={() => setMode("edit")} onKeyDown={(event) => { if (["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(event.key)) { event.preventDefault(); setMode("preview"); document.getElementById("managed-skill-preview-tab")?.focus(); } }}>编辑</button></div>{mode === "preview" ? <div id="managed-skill-preview-panel" role="tabpanel" aria-labelledby="managed-skill-preview-tab" aria-label="Skill 预览" className="skill-markdown-preview"><ReactMarkdown remarkPlugins={[remarkGfm]}>{draft}</ReactMarkdown></div> : <div id="managed-skill-edit-panel" role="tabpanel" aria-labelledby="managed-skill-edit-tab" aria-label="Skill 编辑"><label className="skill-content-label" htmlFor="managed-skill-content">Skill 正文</label><textarea id="managed-skill-content" aria-label="Skill 正文" disabled={saving} value={draft} onChange={(event) => setDraft(event.target.value)} rows={16} /><div className="skill-editor-actions"><button type="button" className="primary-button" disabled={saving} onClick={() => void saveRevision()}>保存修订</button><button type="button" className="secondary-button" disabled={saving} onClick={() => { setCreating(false); setDraft(currentRevision?.content || ""); }}>取消</button></div></div>}</>}</div>}</section>
      <section className="managed-skills-group"><h3>启动加载回执</h3>{receipts.length ? <div className="managed-revision-list">{receipts.map((receipt) => <div key={receipt.id}>配置 #{receipt.config_id} · PID {receipt.pid} · {receipt.error || "已加载"} · {receipt.created_at}</div>)}</div> : <p className="muted">当前配置尚无启动加载回执。</p>}</section>
      <section className="managed-skills-group"><h3>系统能力</h3><article className="skills-feature-card"><h4>反馈迭代</h4><p>下次启动：{capability?.enabled ? "启用" : "关闭"}（{capability?.status || "未配置"}）；当前活动：{capability?.active ? (capability.active.enabled ? "启用" : "关闭") : "尚无加载回执"}。</p><label className="skills-toggle"><span>启用反馈迭代</span><input type="checkbox" role="switch" aria-label="启用反馈迭代" checked={capability?.enabled ?? false} disabled={saving || !capability} onChange={(event) => void changeFeedbackCapability(event.target.checked)} /><span aria-hidden="true" /></label></article></section>
    </>}
  </section>;
}
