# DingTalk report operations

Follow the workflow in SKILL.md. Resolve the target and template with this
Skill's materials.py; dates, wiki names and three managed areas are Skill policy.
Save complete inputs and outputs in the task artifact directory.

## Sources

Use read_scheduled_context for trigger facts. Save context.json and run
materials.py with consumer_run_skill_script. Use the resulting explicit window
in read_service_activity(sources=["meetings"]). Read selected complete archives
through read_meeting_archive(meeting_id), then native minutes tools for gaps.
Use dingtalk-chat for relevant message context and dingtalk-wiki/doc for live
business material. Record read failures and partial coverage as evidence.

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
