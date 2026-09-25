# OA Event Coalescing and Reminder Results

## Goal

All DingTalk OA entry points must converge on one durable OA work item. The OA scheduled scan owns approval review. System notifications become context events, while a real chat reminder receives the final review result in its original conversation.

## Identity model

- `processInstanceId` identifies the OA case and is required for cross-entry correlation.
- `processInstanceId + taskId` identifies the executable approval node. A missing `taskId` is an unresolved case event, not a new executable task.
- A reply task is the service work item for an executable node. It can contain multiple immutable input events and multiple Consumer/Audit runs.
- A Codex session belongs to one Agent run and is never the identity of an OA case or node.

## Routing

1. Extract the OA process instance and optional task ID from the message, raw payload, or URL.
2. For a system OA notification, persist the event and let the scheduled scan resolve the current task and review it. Do not create a standalone Agent run.
3. For a chat reminder, persist a `chat_reminder` event containing the original conversation and message identity. Resolve its task when possible and attach it to the same OA node; do not run a separate full OA review.
4. When a scheduled scan resolves exactly one current task for the current user, it adopts all unresolved events and reminder targets for that process instance into the node work item.
5. If no current task or multiple current tasks can be established, retain the event at case level and wait for the next scan. No external approval action is allowed from an unresolved event.
6. A transaction and unique keys prevent a notification reader and the scheduled scanner from creating two work items for the same node.

## Reminder result delivery

After the scheduled OA review reaches a provider-confirmed result, the service replies in the original reminder conversation. The reply is keyed by the reminder event, the OA node, and the result attempt, so a retry cannot send it twice.

The message states the plain-language result and links to the OA. It uses the action actually confirmed by DingTalk: approved, returned for correction, rejected, commented while remaining pending, or still awaiting a human decision. A notification-only event never receives a chat reply.

If the OA review fails before a confirmed result, the reminder is retained as pending and the service does not claim an outcome. If the reminder sender or original message cannot be resolved, the result remains visible in History and Attention for manual delivery.

## Migration and compatibility

Existing `oa:<process>:` provisional keys remain case-level compatibility records. New notification events are adopted to the resolved node without creating another task; existing task history and Agent runs retain their IDs.

## Acceptance criteria

- A system OA notification with no `taskId` produces no standalone Consumer/Audit run.
- A chat reminder with no `taskId` is linked to the current OA node once the scan resolves it.
- A process with multiple active nodes remains case-level until a node is unambiguous.
- A scheduled review for a node has at most one active execution chain.
- A confirmed OA result sends exactly one reply per chat reminder target.
- The reply target is the original conversation and message, and the body reports the confirmed result without internal rule or Agent terms.
- Existing non-OA message triage and OA applicant notifications continue to use their existing delivery paths.
