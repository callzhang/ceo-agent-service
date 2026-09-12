import { request, type ConsoleResource } from "./console";

export interface AttemptMetadata {
  label: string;
  value: string;
}

export interface AttemptToolUse {
  title: string;
  tool: string;
  call_id: string;
  relevance: string;
  source: string;
  args: unknown;
  format: string;
  output: string;
}

export interface AttemptAgentSession {
  role: string;
  label: string;
  session_id: string;
  url: string;
  tool_uses: AttemptToolUse[];
}

export interface AttemptUnsubscribeReceipt {
  outcome: string;
  evidence: string;
  result_text: string;
  receipt_id: string;
  entry_reference: string;
  entry_url: string;
  started_at: string;
  completed_at: string;
  steps: Array<{ sequence: number; operation: string; state: string }>;
}

export interface AttemptEmail {
  classification_id: string;
  classification_url: string;
  account_id: string;
  action_type: string;
  category: string;
  action_plan_id: string;
  stable_message_identity: string;
  subject: string;
  sender: string;
  folder: string;
  received_at: string;
  rfc_message_id: string;
  candidate_source: string;
  unsubscribe: AttemptUnsubscribeReceipt | null;
}

export interface AttemptRuntimeEntry {
  role: string;
  session_url: string;
  proposal_revision: number;
  turn_attempt: number;
  route: string;
  runtime: string;
  credential_mode: string;
  model: string;
  session_available: boolean;
  status: string;
  failure_code: string;
  failover_permitted: boolean;
  transcript_start: number;
  transcript_end: number;
  effect_started_at: string;
}

export interface AttemptFeedbackEvent {
  rating: string;
  rating_label: string;
  rating_stars: string;
  comment: string;
  source: string;
  received_at: string;
}

export interface AttemptDetail {
  id: number;
  title: string;
  type: string;
  conversation: { label: string; title: string; trigger_sender: string };
  status: {
    raw: string;
    subject: string;
    message: string;
    requires_decision: boolean;
    attention: { kind: string; reason: string; external_effect: string; retry_at: string };
  };
  metadata: AttemptMetadata[];
  trigger: { title: string; text: string };
  audit_explanation: { title: string; text: string };
  generated_reply: { title: string; text: string };
  references: Array<{ title: string; source: string; relevance: string }>;
  feedback: { reviewer_feedback: string; corrected_reply: string; feedback_url: string; events: AttemptFeedbackEvent[] };
  decision_options: Array<{ label: string; instruction: string; consequence: string; url: string }>;
  audit_summary: string;
  draft_reply: string;
  failure_reason: string;
  recovery_state: string;
  action_pills: Array<{ label: string; status: string }>;
  quality_warnings: string[];
  context_only_info: string;
  tool_uses: AttemptToolUse[];
  agent_execution_record: boolean;
  revision_count: number;
  oa: { process_instance_id: string; task_id: string; url: string; action: string; remark: string; result: unknown };
  calendar: { event_id: string; response_status: string; result: unknown };
  actions: {
    can_rerun: boolean;
    can_recall: boolean;
    can_submit_feedback: boolean;
    rerun_url: string;
    recall_url: string;
    feedback_url: string;
    consumer_url: string;
    audit_url: string;
    agent_url: string;
    dingtalk_url: string;
    wechat_open_url?: string;
    delivery_action_label?: string;
    delivery_action_url?: string;
    terminal: boolean;
    action_label: string;
  };
  email: AttemptEmail | null;
  agent_sessions: AttemptAgentSession[];
  runtime_attempts: AttemptRuntimeEntry[];
  created_at: string;
  updated_at: string;
}

export function getAttemptDetail(attemptId: string, signal?: AbortSignal) {
  return request<ConsoleResource<AttemptDetail>>(`/api/console/history/${encodeURIComponent(attemptId)}`, { signal });
}
