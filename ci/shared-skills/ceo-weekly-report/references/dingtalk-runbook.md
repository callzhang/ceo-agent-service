# DingTalk Runbook

## Preconditions

Use one authenticated `dws` profile for all reads, identity resolution, and
writes. Work from the dated run directory because `@file` arguments must be
relative to the current directory. Preserve raw structured results and inspect
`complete`, pagination, failures, and verification fields.

## 1. Resolve Documents

The service's materials command has already found the target and previous
meeting documents by title. Fetch each by the node ID it returned:

```bash
dws doc +fetch --node <node-id-from-materials> --detail full --scope full --format json
```

Record title, node ID, URL, and revision in `manifest.json`. Save the parsed
top-level JSONML element as `target-before.jsonml`. When the target does not
exist yet, create it first in the year page the materials name: copy the
previous meeting document with `dws wiki +node-copy` into the same year page,
rename the copy to the target title, and clear the previous week's content of
the three places the report writes (see SKILL.md). Never create a second
document with the same title.

## 2. Inventory and Read AI Minutes

The materials command already lists the week's meetings with their archived
transcript paths; read those files for the relevant ones. Use the commands
below only to cover a meeting the materials lack or whose archive is missing.

```bash
dws minutes +search --start <beijing-start-rfc3339> --end <beijing-end-rfc3339> --scope all --page-all --format json
dws minutes +detail --ids <comma-separated-relevant-task-uuids> --artifacts basic,summary,keywords,transcript,todos --transcript-output file --output-dir minutes-transcripts --format json
```

Verify `complete=true` for the mine/shared inventory and every selected
transcript. Preserve every screened record and its inclusion or exclusion
reason. Relevant records require the complete transcript, not only the AI
summary.

## 3. Read Derek's Messages and Context

```bash
dws chat +search-msg --senders "Derek Zen" --start <beijing-start-rfc3339> --end <beijing-end-rfc3339> --page-all --order asc --format json
dws chat +thread-replies --message-id <selected-root-message-id> --page-all --order asc --format json
```

Use `dws chat +chat-messages` for sufficient preceding and following context in
non-threaded conversations. Every selected message ID needs a context-read
entry. Identity-unverified results cannot support a definitive Derek judgment.

## 4. Build and Validate Locally

Create `evidence.json`, `previous-issues.json`, and `report.json`, then run from
the installed Skill directory:

```bash
python3 -m scripts.validate_run --manifest <run-dir>/manifest.json --report <run-dir>/report.json --previous-issues <run-dir>/previous-issues.json --output <run-dir>/validation.json
python3 -m scripts.render_dingtalk_jsonml --before <run-dir>/target-before.jsonml --report <run-dir>/report.json --meeting-document --output <run-dir>/target-after.jsonml --draft-output <run-dir>/draft.jsonml
```

Check the rendered body locally before any write. The document service rejects
the whole overwrite for one malformed node, and `dws doc +update --dry-run`
does not look at the body:

```bash
python3 -m scripts.jsonml_check <run-dir>/target-after.jsonml <run-dir>/draft.jsonml
dws doc +script --command parse --doc-format jsonml --content @target-after.jsonml --format json
```

Both must pass (`"assessment": "passed"` for the second). `cockpit` and
`discussion` items are plain strings; `ceo_judgment` items are
`{status, text}` and `root_causes` items `{text}`; an object anywhere else
fails validation.

`target-after.jsonml` is the meeting document with only the three report
places filled; `draft.jsonml` is the complete seven-section report kept in the
run directory. Do not continue unless validation exits `0` with
`publishable: true`. Inspect the rendered document and verify every person
node has a resolved DingTalk `id` and official `name`.

## 5. Save a Recoverable Version

From the run directory:

```bash
dws doc +version-save --node <exact-target-node> --format json --yes
```

Record the returned version identity before writing. The scheduled weekly run
is authorized to write the target meeting document; a manual request to
update it is the same authorization. Without either, stop at the validated
local draft.

## 6. Guarded Write

Re-fetch the target immediately before writing. Any identity or revision change
blocks the write until the new content is reconciled and validated.

```bash
dws doc +update --node <exact-target-node> --command overwrite --content @target-after.jsonml --doc-format jsonml --expected-revision <verified-revision> --format json --yes
```

Perform one complete write. If the commit status is unknown, read back first;
never repeat an unknown external write.

## 7. Full Readback

```bash
dws doc +fetch --node <exact-target-node> --detail full --scope full --format json
```

Save the response as `readback.json` and verify: title and node unchanged;
revision advanced; everything outside the three written places equal to
`target-before.jsonml`; 12 fixed metrics present; prior open issue IDs
retained or closed with proof; every new issue present in the tracking table
once; native mentions valid; no literal Markdown markers, private fields,
guessed actuals, or unclassified root causes. An API success response without matching readback is not completion.

## Failure Policy

Block publication when the materials command fails, the revision changed
under the write, or a write cannot be read back. Minutes or messages that
could not be paginated completely are recorded in the coverage warning and
the report still publishes. Keep the evidence and failure ledger and report
the exact missing dependency.

Continue with a visible coverage warning when a business line has not
submitted its report, a linked document, metric actual/definition, or forecast
support is absent. Use `无数据`
or `证据缺失`, add a native mention and checkpoint, and do not extrapolate.
