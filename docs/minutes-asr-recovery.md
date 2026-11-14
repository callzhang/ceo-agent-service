# Internal Minutes ASR recovery procedure

This is an internal file task created by `sync-minutes-once` after a typed
summary-generation failure or retryable summary-read error persisted through one
workflow retry. The normal provider retry uses a 300-second read timeout. It does
not regenerate, overwrite or represent DingTalk's official summary as repaired.

When the scanner has read a complete, nonempty official DingTalk transcript,
the coordinator instead uses it to produce an internal summary. It binds
the Workbench turn to the unique archive and transcript hash, then checks
the association and original transcript again before publishing the labeled
supplement. The ASR procedure below applies when a complete official
transcript is unavailable.

Read the supplied metadata file and its original working directory (given in the
user request). `metadata.json` binds the task UUID, actual audio SHA-256 and actual
duration to the original DWS download. The work directory contains
`original-media`, `download-receipt.json` and optional job checkpoints.

1. If `asr-complete.json` exists, read the completed transcript and prepare the
   summary from it. The service rechecks its original native tool receipt.
2. Otherwise, if `upload-result.json` exists, call the configured native
   `ai_services.get_audio_transcription` MCP tool with that exact `job_id`.
   Call it directly as a native MCP tool; do not wrap it in code execution
   or print/serialize the full tool response through another tool.
   Poll that same job until done; do not upload again while it is running.
   Only an explicit expired/missing-job response permits replacement.
3. For a new job call native `ai_services.create_audio_upload` (language can be
   detected automatically). From the service root run the upload helper with
   `metadata.json`'s `python_executable`:

   ```text
   <python_executable> -m app.minutes_auto_recovery upload --work-dir <original-work-dir> --upload-url <native-upload-url>
   ```

   This streams the original recording as multipart `file`, records the actual
   server job ID against the frozen source metadata in the service cursor, then
   saves the local checkpoint before returning. Audio bytes never go through the
   model. Poll that ID using native `ai_services.get_audio_transcription` calls,
   so the service can reconstruct the actual tool response from this native turn.
4. Return only a concise supplementary Markdown summary in the recording's
   language, grounded in the completed transcript: subject, important facts,
   decisions and outstanding work. Mark uncertain details. Speaker labels are
   anonymous; do not infer identities. Do not return operational commentary as the
   summary, fabricate transcript data, write result checkpoints yourself, change
   code, send messages, modify DingTalk or manually publish the archive.

The service requires a completed native Agent turn and real ASR result, validates
source download identity/hash/time boundaries with the existing archive recovery
function, and appends an explicitly attributed internal summary and full ASR
transcript. It preserves the official text and failure explanation. Only an
atomically published file with a matching readback enters `archived_ids`. Audio
and temporary checkpoints are deleted after publication. Native Workbench task,
turn and runtime records remain the processing history.

A pending turn is not successful synchronization. Its real IDs and working
directory remain in the Minutes scanner cursor; the next pass resumes it rather
than submitting another job. Failed native turns consume the existing bounded
attempt budget; exhausted or stopped tasks retain a real failure.

## Native MCP connection setup

The configured MCP server is `ai_services`. Each allowed native CLI uses its own
normal credential store; a working desktop connector does not establish a
background Claude connection. If the native CLI reports authorization required,
complete that CLI's own MCP login once. Do not copy tokens between clients or
replace the connection with an ad hoc HTTP credential proxy. A connection error
stays a real failed recovery and consumes no fake archive success.

If a native Agent ends normally without producing the required ASR/summary, the
service records the result failure in the cursor and uses a new bounded native
turn on the next sync. An existing completed ASR is reconstructed from the real
previous turn and reused. Explicit stops and exhausted budgets remain failed;
their temporary recording is removed. A process crash before saving a returned
upload job ID may require repeating that pure transcription submission; this is
not an exactly-once upload guarantee. No business operation is replayed.

An archive I/O interruption resumes the same completed native turn and its exact summary; it does not spend a new Agent attempt or generate a different supplement. Only an invalid/missing Agent result requires a new turn.

ASR receipts are read directly from the completed turn's native runtime references with the existing unified trajectory reader. UI tool-event rows are not required; a zero-length superseded route does not hide the following valid native result.

The recovery turn requests direct native transport for the AI Services namespace.
The archive reader prefers the native structured result to its display text.
A text-only response that was truncated is invalid evidence; it never publishes
an archive and the next sync can use the existing bounded recovery budget.

## Durable completion after interruption

The scanner cursor retains the frozen source metadata and actual upload job
identities. Before transmitting bytes, the helper checks that its metadata still
matches that source; it persists the real HTTP response before exposing the job
ID. The archive coordinator accepts only a completed native ASR call for one of
those recorded upload jobs. A model-written result file is never completion
evidence. If the source has multiple recorded uploads, the latest one with a
completed native result is used; unrelated jobs are not archive evidence.

A completed ASR/summary is finalized before evaluating the budget for new Agent
turns. If temporary audio was already cleaned, the coordinator downloads the
original source again read-only and the existing publisher checks it against the
frozen audio hash. This creates no new ASR or Agent generation. It does not reset
the budget or alter the old native runs. Reacquired temporary files are cleaned
after the actual archive publication and readback.
