import { request } from "../api/console";

export interface ManagedSkill { id: number; name: string; display_name: string; created_at: string; }
export interface ManagedSkillExportReceipt { id: number; revision_id: number; sha256: string; path: string; created_at: string; }
export interface ManagedSkillExport { status: "exported" | "not_exported"; receipt: ManagedSkillExportReceipt | null; }
export interface ManagedSkillRevision { id: number; skill_id: number; revision_number: number; content: string; sha256: string; parent_revision_id: number | null; source: string; created_at: string; export?: ManagedSkillExport; }
export interface RuntimeSkillBinding { skill_id: number; revision_id: number; enabled: boolean; load_order: number; purpose: string; }
export interface RuntimeSkillConfig { id: number; parent_id: number | null; status: string; created_at: string; bindings: RuntimeSkillBinding[]; }
export interface RuntimeSkillLoadReceipt { id: number; config_id: number; pid: number; loaded_json: string; error: string; created_at: string; }
export interface FeedbackIterationCapability { enabled: boolean; config_id: number | null; status?: string; active?: { enabled: boolean; config_id: number | null; status?: string } | null; }

export function listManagedSkills(signal?: AbortSignal) { return request<{ items: ManagedSkill[] }>("/api/console/settings/managed-skills", { signal }); }
export function createManagedSkill(name: string, displayName: string) { return request<ManagedSkill>("/api/console/settings/managed-skills", { method: "POST", body: JSON.stringify({ name, display_name: displayName }) }); }
export function listManagedSkillRevisions(skillId: number, signal?: AbortSignal) { return request<{ items: ManagedSkillRevision[] }>(`/api/console/settings/managed-skills/${skillId}/revisions`, { signal }); }
export function createManagedSkillRevision(skillId: number, content: string, parentRevisionId: number | null) { return request<ManagedSkillRevision>(`/api/console/settings/managed-skills/${skillId}/revisions`, { method: "POST", body: JSON.stringify({ content, parent_revision_id: parentRevisionId }) }); }
export function exportManagedSkillRevision(revisionId: number) { return request<{ name: string; revision_id: number; sha256: string; path: string; export: ManagedSkillExport }>(`/api/console/settings/managed-skill-revisions/${revisionId}/export`, { method: "POST" }); }
export function getCurrentRuntimeSkillConfig(signal?: AbortSignal) { return request<{ pending_or_active: RuntimeSkillConfig | null; active: RuntimeSkillConfig | null }>("/api/console/settings/runtime-skill-configs/current", { signal }); }
export function createRuntimeSkillConfig(expectedParentId: number | null, bindings: RuntimeSkillBinding[]) { return request<RuntimeSkillConfig>("/api/console/settings/runtime-skill-configs", { method: "POST", body: JSON.stringify({ expected_parent_id: expectedParentId, bindings }) }); }
export function listRuntimeSkillLoadReceipts(configId: number, signal?: AbortSignal) { return request<{ items: RuntimeSkillLoadReceipt[] }>(`/api/console/settings/runtime-skill-configs/${configId}/load-receipts`, { signal }); }
export function getFeedbackIterationCapability(signal?: AbortSignal) { return request<FeedbackIterationCapability>("/api/console/settings/feedback-iteration", { signal }); }
export function setFeedbackIterationCapability(enabled: boolean) { return request<FeedbackIterationCapability>("/api/console/settings/feedback-iteration", { method: "POST", body: JSON.stringify({ enabled }) }); }

export interface RepositorySkill {
  repo_url: string; commit: string; skill_path: string; name: string; description: string;
  author: string; author_kind: "author" | "maintainer" | "unknown"; updated_at: string | null;
  available: boolean; unavailable_reason: string;
}
export interface SkillSearchResult { items: RepositorySkill[]; errors: { source: string; code: string; message: string }[]; }
export interface SkillInstallResult {
  name: string; skill_id: number; revision_id: number; sha256: string; package_sha256: string; source: string;
  install_status: "installed" | "already_installed"; activation_status: "pending_restart" | "active";
}
export function searchSkills(query: string, signal?: AbortSignal) {
  return request<SkillSearchResult>("/api/console/settings/skill-search", { method: "POST", body: JSON.stringify({ query }), signal });
}
export function installSkill(candidate: Pick<RepositorySkill, "repo_url" | "commit" | "skill_path">) {
  return request<SkillInstallResult>("/api/console/settings/skill-installs", { method: "POST", body: JSON.stringify({ repo_url: candidate.repo_url, commit: candidate.commit, skill_path: candidate.skill_path }) });
}
