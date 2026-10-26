# DingTalk report operations

Follow the workflow in SKILL.md. Resolve the target and template with this
Skill's materials.py; dates, wiki names and three managed areas are Skill policy.
Save complete inputs and outputs in the task artifact directory.

## Sources

Use `read_scheduled_context` for trigger facts. Save `context.json` and run
`materials.py` with `consumer_run_skill_script`. Use the resulting explicit
window for every source query.

Complete these reads before deciding evidence is unavailable:

1. Call `read_service_activity` for the full window with
   `sources=["meetings"]`. Screen every returned meeting and record an
   inclusion or exclusion reason. Read every relevant full archive using
   `read_meeting_archive(meeting_id)`; when an archive is missing, try
   `list_dingtalk_minutes(start, end, scope="all")` and read relevant items
   with `read_dingtalk_minutes(task_uuid)`.
2. Call `recent_dingtalk_conversations(start, end)` for the same bounded
   window. Read relevant group conversation windows with
   `read_dingtalk_messages_in_window(conversation_id, start, end)` and record
   which conversations were read or excluded and why.
3. Search for each of the four business-line source reports and for relevant
   current company OKR, CRM, finance, product, release, quality and delivery
   evidence with `search_dingtalk_documents(query)`. Use the exact returned
   document identity to read full content with `read_dingtalk_document_full`
   or `read_dingtalk_drive_file`, as appropriate. Follow linked evidence that
   supports a material claim.
4. Read the previous report and any exact target/prior documents resolved by
   `materials.py`; do not treat the template or a search result snippet as
   current business evidence.

For each source class, distinguish `not searched`, `no results` after a
completed query, `read failed` after an invoked operation returns a concrete
error, and `read` with the source identity. Do not infer an unavailable
operation from missing initial context, incomplete prompt input or the model's
expectation. Do not return `operation_unavailable` for evidence that was never
searched. When queries complete with no result, or a source read genuinely
fails, preserve that coverage fact and continue the report using the contract's
missing-data rules; absent business-line submissions do not block publication.
If a required platform capability is genuinely absent, identify the exact
capability and the attempted tool/route evidence, then stop before claiming
complete coverage. Do not claim a search/read was complete unless its result
shows completion (including pagination where exposed).

## Rendering

Save report.json. Run scripts/render_dingtalk_jsonml.py with --before,
--report, --meeting-document and --output; optional --draft-output saves the
seven-section draft. The command performs renderer-field checks and JSONML
checks in one invocation. It does not enforce business coverage or a fixed
indicator list. Review those requirements under references/report-contract.md
and the existing Audit feedback cycle.

## Writing

consumer_document_write submits an exact artifact to an explicit node, or a
parent+workspace+title for creation. JSONML updates require expected_revision
from the current target read; native JSONML parsing and version-save occur
before update. A conflict requires fresh reads and rendering. Preserve existing
tracking rows, participant sections, topics and report links. The three managed
areas are CEO judgment, company metrics and appended new tracking rows.

## Evidence and completion

The write returns provider metadata and document identity. Retain this receipt;
no automatic content readback or full-body comparison is required. Artifact
availability and a local render alone do not establish an external write.
Report the real document identity, write receipt and any unresolved
business/material gap in the result. Inspect the target when execution status
is unknown before deciding whether another write is needed.
