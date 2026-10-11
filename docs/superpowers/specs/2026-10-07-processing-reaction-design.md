# DingTalk processing reaction

Derek approved the design in chat on 2026-10-07. For accepted incoming DingTalk chat triggers, add a text reaction 处理中 and remove it after verified reply delivery, no-action completion, failure, human handoff, or trigger replacement. Retries retain the reaction. Progress failures never block business work. Synthetic service and calendar triggers do not receive reactions.

Persist per-source reaction intent and the reusable text emotion in existing service_state. Recover cleanup during producer/consumer passes using the authoritative reply task and sent reply records. Preserve source identity when a pending task is replaced. Use the native DWS add/remove text emotion APIs as the authenticated account. No new audit, permission, or effect gates.

Validate focused tests for success, no reply, retry, replacement, interruption and reaction failures. Deploy only a pushed main commit and read back runtime health and queue state. Native reaction display must be verified separately from mocked tests.
