# Corpus v1 smoke: fixture defect, superseded by v2

Before any candidate evaluation, baseline smoke evaluated message-progress and
calendar-split-dst with native gpt-5.6-luna/medium. All four roles returned valid
strict structured output and emitted no tool events. Original raw results,
actual inputs and provider usage remain in baseline-smoke.v1.json under the private evaluation artifact root
recorded in README.md; exact file bytes and SHA were preserved on relocation.

Message Consumer preserved merged/queued/unconfirmed states. Calendar Consumer
preserved both date-specific timezone offsets and availability uncertainty in a
legitimate no_action informational answer. Both Audit turns correctly identified
the fixture's unsupported generic reply operation and payload.text. The service
registry requires reply_to_message and payload.content. These outcome mismatches
are fixture-discovery evidence, not prompt regression evidence.

V2 fixes the fixed candidate action fields using the actual registered contracts,
uses no_action fixed subjects for email drafting (no registered email-send handler
is present), and permits no_action informational calendar answers alongside a
proposal. OA payloads use remark for approve and content for comment. The scheduled
report notification targets the explicitly synthetic principal. Complete source
bindings and the 20 representative factual cases remain intact. V2 was frozen
before candidate evaluation, with canonical cases SHA
5041b0dfb2656fceea617ffedbae2913592878c7158917112937f0b5a89edea2.
