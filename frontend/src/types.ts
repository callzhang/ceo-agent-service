export type TaskState =
  | "idle"
  | "queued"
  | "running"
  | "waiting_confirmation"
  | "completed"
  | "stopped"
  | "failed";

export type TurnStatus = Exclude<TaskState, "idle">;

export type EventType =
  | "text_delta"
  | "thinking_summary"
  | "tool_started"
  | "tool_completed"
  | "file_changed"
  | "artifact_created"
  | "confirmation_required"
  | "status_changed"
  | "turn_completed"
  | "turn_failed";

export type ConfirmationStatus =
  | "pending"
  | "confirmed"
  | "cancelled"
  | "executed"
  | "failed";

export interface Task {
  id: string;
  title: string;
  runtime_kind: string;
  archived_at: string;
  state: TaskState;
  created_at: string;
  updated_at: string;
}

export interface Turn {
  id: string;
  task_id: string;
  client_request_id: string;
  user_text: string;
  status: TurnStatus;
  stop_requested: boolean;
  final_text: string;
  error_code: string;
  error_detail: string;
  started_at: string;
  completed_at: string;
  created_at: string;
  updated_at: string;
}

export interface WorkbenchEvent {
  id: number;
  turn_id: string;
  sequence: number;
  event_type: EventType;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Attachment {
  id: string;
  task_id: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  created_at: string;
}

export interface Artifact {
  id: string;
  turn_id: string;
  label: string;
  media_type: string;
  created_at: string;
  download_url: string;
}

export interface Confirmation {
  id: string;
  turn_id: string;
  action_kind: string;
  target: string;
  summary: string;
  risk: string;
  canonical_capability: string;
  canonical_operation: string;
  canonical_targets: string[];
  status: ConfirmationStatus;
  decision_requested: string;
  decision_requested_at: string;
  proposer_quiesced: boolean;
  created_at: string;
  decided_at: string;
}

export interface Timeline {
  task: Task;
  turns: Turn[];
  events: WorkbenchEvent[];
  attachments: Attachment[];
  artifacts: Artifact[];
  confirmations: Confirmation[];
  next_cursor: string;
  has_more: boolean;
  events_has_more: boolean;
  events_next_cursor: number;
  artifacts_has_more: boolean;
  artifacts_next_cursor: string;
  confirmations_has_more: boolean;
  confirmations_next_cursor: string;
  attachments_has_more: boolean;
  attachments_next_cursor: string;
}

export interface RuntimeCapabilities {
  kind: string;
  capabilities: {
    session_resume: boolean;
    streamed_text: boolean;
    structured_tools: boolean;
    image_input: boolean;
    model_selection: boolean;
    mcp_configuration: boolean;
    stoppable: boolean;
    recoverable: boolean;
  };
}

export interface WorkbenchStats {
  tasks: { total: number; active: number; archived: number };
  turns: Record<TurnStatus, number>;
  confirmations: Record<ConfirmationStatus, number>;
  events: Record<string, number>;
  attachments: number;
  artifacts: number;
  duration: {
    completed_count: number;
    total_seconds: number;
    average_seconds: number;
  };
}

export interface TaskPage {
  items: Task[];
  nextCursor: string;
}
