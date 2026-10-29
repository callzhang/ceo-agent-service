---
name: dingtalk-oa-approval
description: Use when reading or reviewing a DingTalk OA approval instance, current task, operation history, or linked materials.
---

# DingTalk OA approval integration

Use this operation Skill to locate the current approval instance and its active
task, read its form, process history, comments and attachments, and compose the
installed organization review Skill. Organization rules, template registries,
thresholds and decision examples are supplied by the private package.

Skill 明确指定为判断依据的规则卡、登记表或外部制度属于规范来源，必须读取并核验。
若当前 prompt 或适用 Skill 明确指定某个外部规则来源，则应读取该指定来源。

Read the actual provider state and source evidence. Preserve source identifiers
and timestamps. Use the current canonical task result and the applicable
organization Skill for the proposed decision. Keep execution within the current
Consumer, Audit and System Executor contract; this public integration does not
define company approval authority or decision thresholds.

Consumer prepares the complete candidate. Audit reviews the candidate using the
current source evidence and applicable installed business Skill. The System
Executor performs the approved controlled operation and records its provider
receipt. A proposal is not an executed approval.

For native platform operations, use the installed DingTalk operation documentation
and the configured service tools. The bundled `scripts/oa_openapi_detail.py` is a
read-only detail reader; run `python scripts/oa_openapi_detail.py --help` for its
supported arguments. Supply an actual source instance identifier, not a sample ID.
