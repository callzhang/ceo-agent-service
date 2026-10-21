# Eight Email classification source failures

## Scope and observations

Derek explicitly requested deployment, root-cause repair and recovery of the
eight failed Email classification tasks, preserving the prior bounded retry fix.
Initial current-main deployment was serialized with another authorized deploy;
production reached a79d8e279, the checkout was clean, PID 30806 was running, and
healthz, Attention and History returned HTTP 200.

The selected source tasks are the existing records 416045, 416046, 416064,
416241, 416257, 416276, 416277 and 416284. Read-only native IMAP probes confirmed
all eight saved UIDs exist in the same UIDVALIDITY generations. Seven raw
Message-ID header strings matched directly; the eighth also passed the native
normalized Message-ID and stable-identity check. No flags were changed: selects
were readonly and body reads used BODY.PEEK. Mail bodies and credentials were
not copied into this document.

## Confirmed causes

1. A current classification calls similar_owner_examples before the Agent. Its
   cold index read 956 owner examples, including 152 provider-only retired model
   inputs. A readonly baseline reached a historical DingTalk example with old
   UID 1612 in the saved notification folder, whose current provider observation
   was unavailable. The complete exception stack was:
   owner_labelled_examples -> read_owner_model_inputs ->
   EmailSourceReader.read_many/_read_locations -> fetch_messages ->
   _decode_selected_message -> source_data, raising
   LookupError: email source UID is unavailable. This was not the UID of any of
   the eight current tasks. The baseline failed after 157.519 seconds.
2. The shared connector creates its raw socket through
   ImapDeterministicProvider.connect, inheriting 20 seconds even though the
   readonly adapter's established default is 90 seconds. A live socket readback
   confirmed 20.0; the repaired factory's native socket readback confirmed 90.0.
   Historical timeout and DNS errors are retained; current connections to both
   accounts succeeded. No persistent DNS configuration fault was reproduced.
3. The classifier did not pass the existing shared source reader into its owner
   example build. The default reader constructed a separate connector registry,
   defeating the established one-connector-per-account process wiring. The
   classifier now passes the existing HIGH-priority source factory through the
   example index. A source-reader forwarding regression failed before wiring
   and passed after it.
4. Three current DingTalk messages (UIDs 33274, 33276 and 33281) have multipart
   BODYSTRUCTURE attachment filename values returned as multiple IMAP literals.
   The existing parser examined incomplete tuple prefixes as standalone lists,
   raising FETCH metadata is invalid / unterminated IMAP list, or returning the
   literal length marker as the filename in a bounded scan. RFC 9051 sections
   4.3 and 7.5.2 describe the native literal/string and FETCH representation:
   https://www.rfc-editor.org/rfc/rfc9051.html

## Repair and proof

The owner-example query omits only provider-only inputs with an explicit current
unavailable observation; inline/native inputs and unknown observations preserve
their previous reads. It does not delete labels or suppress failures of sources
that are still eligible. Availability changes alter the existing example stamp
and restore eligibility. A candidate readonly cold build completed all 907
eligible examples in 464.096 seconds, excluding 49 explicitly unavailable
provider-only observations. This is a correctness proof, not a latency claim;
the Gmail history relocation reads remain expensive on a cold process.

The shared read timeout is one constant used by the readonly constructor and
shared registry. The IMAP parser consumes literal byte lengths and preserves
literal payloads as bytes while reading protocol attributes. BODYSTRUCTURE
strings decode those bytes as UTF-8. Single and batched UID validation share the
complete FETCH record parser, retaining duplicate/wrong UID rejection; body text
spelling UID cannot authenticate a response record. No attachment contents are
fetched by this change, only the existing selected body sections and metadata.

The original five current messages passed complete source reads. After the
literal repair, the remaining three passed native complete source and stable
identity verification, with body lengths 72376, 77290 and 215 characters and
read times 2.504, 1.856 and 1.639 seconds. All eight have now passed accurate
readonly original reads. The eighth raw-header comparison difference was normal
header normalization, not another UID failure.

## Tests

The original source-eligibility and shared-timeout regressions failed before the
repair. Filename-literal and post-literal UID regressions also failed before the
parser change. Final 150 source/protocol, model-input lifecycle and similarity tests passed,
including all nine new source-failure cases and the prior identity, generation,
unsolicited-response, literal-UID and source-change tests. The 19 selected classification-worker and
connector tests passed; the one expected-kwargs test was updated solely for the
explicit 90-second argument. Before the literal extension, 29 source-failure,
model-input lifecycle and similarity tests passed. Ruff and diff checks passed.

## Publication and selected task recovery

Runtime publication, verified selected-task backup, one-task recovery readback,
remaining-task recovery and final outcomes are pending. Recovery will use the
existing EmailClassificationTaskAdapter.retry_failed_tasks entry point for the
exact selected identities. No direct provider mutation or manual classification
result will be used to impersonate a successful Agent run.
