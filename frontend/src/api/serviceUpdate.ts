import { request } from "./console";

export interface UpdateSnapshot {
  status: string;
  local_commit: string;
  remote_commit: string;
  commits_behind: number;
  fingerprint: string;
}
export interface UpdateProgress {
  operation_id: string;
  status: string;
  target_commit: string;
  installed_commit: string;
  error: string;
}
export interface ServiceUpdateStatus {
  snapshot: UpdateSnapshot | null;
  operation: {operation_id: string; fingerprint: string} | null;
  progress: UpdateProgress | null;
  current_commit: string;
}
export interface ReleaseNotes {
  local_commit: string;
  target_commit: string;
  summary: string;
  summary_source: string;
  summary_error?: string;
  commits: {sha:string; subject:string; body:string}[];
}
export const getServiceUpdate = () => request<ServiceUpdateStatus>("/api/repository-upgrade/status");
export const checkServiceUpdate = (signal?: AbortSignal) => request<{snapshot:UpdateSnapshot}>("/api/repository-upgrade/check", {method:"POST",signal});
export const getReleaseNotes = (fingerprint: string, signal?: AbortSignal) => request<ReleaseNotes>("/api/repository-upgrade/notes", {method:"POST",body:JSON.stringify({fingerprint}),signal});
export const startServiceUpdate = (fingerprint: string, operation_id: string) => request<{operation_id:string;pid:number|null}>("/api/repository-upgrade/start", {method:"POST",body:JSON.stringify({fingerprint,operation_id})});
