# Reviewed system action contracts

This registry describes the actions that require system business review for an
actual configured system task. Ordinary Consumer work is not made a reviewed
action merely because a tool writes. Consumer may write its task-generation
artifacts and use the existing bound report-document workflow, with the actual
tool result and content readback as evidence. These are ordinary work receipts,
not SystemExecutor receipts for a registered action.

Consumer cannot directly dispatch the registered reviewed operations below.
It supplies the complete proposal for Audit and SystemExecutor. Audit remains
read-only. A `summary`, including a historical quotation or an assertion of
completion, never creates an execution record. The parser validates the wire;
it does not scan prose for completion phrases. Audit assesses the complete
candidate against its sources and actual receipts. System completion comes
only from the registered handler's persisted dispatch and verification.

The Consumer proposes `ConsumerProposal.actions`; the Audit reviews the exact
candidate digest, and the service executes only its approved plan or selected
option. An action has `description`, a stable `action_identity`, `capability`,
`operation`, `target`, `payload`, and `effect: "external"`. Use a new
`action_identity` for a materially different intended external outcome. The
identity is stable across a revision that merely clarifies the same outcome.
The service refuses to reuse a completed action's receipt when its approved
capability, operation, target, payload, or effect changed.
For service-owned receipts, the executor resolves the exact persisted selected
branch before reuse. Historical receipts without that branch association are
refused when the prior choices differ under the same action identity.

These names and fields are the executable contract. A business description is
not an operation name. Do not add a CLI command, shell instruction, or free-form
API call to a plan. Every `target` identifies the current business object, not
an example or a previous instance. An unsupported operation fails explicitly.

| Capability | Operation | `target` | `payload` | Completion evidence |
| --- | --- | --- | --- | --- |
| `dingtalk-chat` | `reply_to_message` | `conversation_id`, `message_id` of the trigger | `content` (exact prepared final body) | Native reply receipt for the trigger and prepared delivery key |
| `dingtalk-chat` | `send_message` | Exactly one of `conversation_id`, `user_id`, `open_dingtalk_id` | `content` (exact prepared final body) | Native send receipt for the prepared delivery key |
| `dingtalk-chat` | `send_group_message` | `conversation_id` | `content` (exact prepared final body) | Native send receipt |
| `dingtalk-chat` | `send_direct_message` | `user_id` or `open_dingtalk_id`; `conversation_id` may identify the source but is not a destination | `content` (exact prepared final body) | Native send receipt to the named person |
| `dingtalk-chat` | `add_message_emoji` | `conversation_id`, `message_id` | `emoji` | Native reaction list for the exact message contains the exact emoji and current principal |
| `dingtalk-chat` | `add_message_text_emotion` | `conversation_id`, `message_id` | `text`, `emotion_id`, `emotion_name`, `background_id` | Provider reaction ID; otherwise uncertain |
| `dingtalk-chat` | `create_message_text_emotion` | `resource: "text_emotion_template"` | `text`, `emotion_name`, optional `background_id` | Provider emotion ID; otherwise uncertain |
| `dingtalk-calendar` | `respond_calendar_event` | `event_id` | `response_status` | Exact event's `self_response_status` after native response |
| `dingtalk-oa` | `approve`, `reject` | `process_instance_id`, `task_id` | `remark` | Exact owned task completed with `AGREE` or `REFUSE` |
| `dingtalk-oa` | `revert_task` | `process_instance_id`, `task_id`, `target_activity_id` | `revert_action`, `remark` | Exact owned task completed with `REDIRECT_PROCESS` |
| `dingtalk-oa` | `redirect_task` | `process_instance_id`, `task_id`, `to_actioner_id` | Optional `remark` | Source task redirected and a distinct running task owned by the named recipient |
| `dingtalk-oa` | `comment` | `process_instance_id` | `content` | Provider comment ID; otherwise uncertain |
| `dingtalk-doc` | `create_document` | `name` | Markdown `content` | Provider `nodeId` plus exact document name and Markdown readback |
| `dingtalk-doc` | `create_doc_comment` | `node_id` | `content` | Provider `commentKey`; otherwise uncertain |

For these registered OA operations, `process_instance_id`, `task_id`,
`target_activity_id` and `to_actioner_id`, where required above, are exact
nonempty string identifiers. The Consumer must source the process and task
identifiers from the current OA instance and its task list; a project ID is not
an OA process ID, and a numeric task ID must be represented as the provider's
exact string before Audit reviews the action. Audit checks that source binding,
while the action model and wire schema reject malformed identifier types before
review.

For message sends, the service first stores the composed final body. The
Consumer action must carry that same body. The service does not compose a new
body after Audit approval. A selected option has its own prepared delivery key.

The native reaction list exposes `result.messages[].openMessageId` and
`emotionReplyList[].emoji` with `replyUsers[]`, so an emoji can be read back
for the exact message and principal. The native OA records readback exposes
`operationType`, `operationResult`, `operationTime`, and `userId`, but no comment
body or comment ID. The document comment and text-emotion reads have no
confirmed unique correlation shape in the current native CLI schema. A generic
`success: true` without a domain ID or positive readback does not establish
completion. After an unknown dispatch, the service preserves the action as
uncertain and does not resend it on restart. Xiaoqing transcript and interview
uploads have no service-owned native client and are not registered system
actions.
