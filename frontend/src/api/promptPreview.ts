import { request } from "./console";

export type PromptPreviewRole = "consumer" | "audit";
export interface PromptPreviewRoute { name: string; runtime_kind: string; model: string; }
export interface PromptPreviewAttempt { runtime_attempt_id: number; route_name: string; rendered_at: string; submission_state: "prepared" | "invoked"; }
export interface PromptConfigurationFingerprints {
  developer_template?: string | null;
  developer_instructions?: string | null;
  user_template?: string | null;
  work_profile_instruction?: string | null;
}
export interface PromptPreviewItem {
  mode: "current" | "historical";
  status: "available" | "unavailable";
  role: PromptPreviewRole;
  runtime_kind: string;
  route_name: string;
  model: string;
  rendered_at: string;
  task_id: number | null;
  run_id: number | null;
  attempts: PromptPreviewAttempt[];
  runtime_attempt_id: number | null;
  execution_generation: string | null;
  proposal_revision: number | null;
  stage_index: number | null;
  submission_state: "preview" | "prepared" | "invoked";
  developer_instructions: string;
  task_prompt: string;
  submitted_input: string;
  runtime_context: string;
  reason: string;
  scope: string;
  routes: PromptPreviewRoute[];
  configuration_fingerprints?: PromptConfigurationFingerprints | null;
  task_source_configuration_fingerprints?: PromptConfigurationFingerprints | null;
  task_source_run_id?: number | null;
  task_source_rendered_at?: string | null;
}
export interface PromptPreviewQuery { role: PromptPreviewRole; route_name?: string; task_id?: number; run_id?: number; runtime_attempt_id?: number; }
export function getPromptPreview(params: PromptPreviewQuery, signal?: AbortSignal) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => { if (value !== undefined && value !== "") query.set(key, String(value)); });
  return request<{ item: PromptPreviewItem; meta: { snapshot_at: string } }>(`/api/console/settings/prompt-preview?${query}`, { signal });
}
