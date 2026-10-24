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
| `dingtalk-oa` | `append_task` | `process_instance_id`, `task_id`, `appender_user_id` (a verified DingTalk user ID) | `type`: `before` or `parallel` | The principal's source task is running and a new running task owned by the named user appears; refused when that user already has a running task |
| `wechat` | `send_message` | `account_id`, `target_type` (`direct` or `group`), `target_id`, `conversation_id` of the WeChat target | `text` (exact prepared final body) | The delivery created from this approved action ends `sent`; an unconfirmed send is uncertain and settled by read-only reconciliation, never re-sent |
| `dingtalk-oa` | `comment` | `process_instance_id` | `content` | Provider comment ID; otherwise uncertain |
| `dingtalk-doc` | `create_document` | `name` | Markdown `content` | Provider `nodeId` plus exact document name and Markdown readback |
| `dingtalk-doc` | `create_doc_comment` | `node_id` | `content` | Provider `commentKey`; otherwise uncertain |
| `dingtang-okr` | `score_kr` | `period_id`, `objective_id`, current `batch_id`, `kr_id`, `evaluator_user_id` from the live score detail; evaluator must equal the logged-in page user | `score` on the 0-1 API scale, `score_description` with evidence | Exact period, objective, current batch, KR, evaluator, score, description and `complete` status in a fresh `getScoreDetail` readback |

For these registered OA operations, `process_instance_id`, `task_id`,
`target_activity_id`, `to_actioner_id` and `appender_user_id`, where required above, are exact
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

## Consumer purpose action names

Consumer purpose output uses action/reason/target/payload/evidence_refs. Purpose definitions select the available names and strict arguments before the turn. OA approve/reject/comment map to the registered same-named OA operations; return maps to revert_task, with exact target_activity_id and revert_action REVERT_FOR_APPROVAL or REVERT_FOR_RESUBMIT. General reply/react map to reply_to_message/add_message_emoji. A typed plan preserves the existing multi-action proposal and all action contracts above.

request_access is a Consumer purpose action that asks the verified responsible party through an OA comment or source-conversation reply. Its receipt proves the request was sent, not that access was granted. Only a verified request_access source candidate can anchor permission retries. While access is still unavailable, Consumer may return `retry` with empty target/payload and a reference to that request's `external_action_key`; the service strips one known `receipt:` or `prior_receipt_id=` label before exact key matching. This permission-wait retry is not an approval-result proposal, does not enter Audit, and has no provider dispatch. The service persists at most three later Consumer reads, 12 hours apart, and a 72-hour hard deadline from the original verified request's dispatch record, independent of sessions/revisions/restarts. All other executable actions retain the completion evidence specified above.

For field definitions, OA return and retry JSON examples, reservation semantics,
and recovery boundaries, see [Consumer purpose output](consumer-purpose-output.md).
