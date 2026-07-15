# Meeting Scope Filter and Controlled Replay Design

Date: 2026-07-15

## Goal

Extend Meeting Alignment so it ignores two classes of recordings that should
not produce post-meeting alignment messages:

- actual candidate interview sessions;
- recordings whose actual duration is less than ten minutes.

After the change is deployed, run the ten most recent DingTalk AI Minutes
records through the production decision and delivery path, send every result
that meets the existing send contract, and monitor all ten records to a final,
auditable outcome.

## Scope and Definitions

### Actual candidate interview

An actual candidate interview is a session in which one or more interviewers
question or assess a candidate for a role. Recruitment stand-ups, hiring-plan
discussions, talent-profile discussions, candidate pipeline reviews, and hiring
requirement alignment are ordinary business meetings and remain eligible.

Interview classification uses the complete meeting source available to the
Meeting Alignment Agent: title, summary, participants, and transcript. It does
not use a fixed title keyword list. This prevents recruiting-related business
meetings from being discarded merely because their titles mention hiring.

When a source is an actual candidate interview, the agent returns terminal
`no_action` immediately with an audit summary that identifies the scope
exclusion. It must not search for a group, resolve mentions, construct an
alignment message, or send anything.

### Short recording

Duration is calculated from the normalized Minutes start and end timestamps.
A recording is short when:

```text
end_time - start_time < 10 minutes
```

Exactly ten minutes remains eligible. The producer applies this deterministic
gate before calendar matching and before queue creation. A short recording does
not invoke the Meeting Alignment Agent and does not create a meeting job.

The list-level `durationMicros` field may be retained as supporting evidence,
but normalized start/end timestamps are authoritative because the existing
source contract already validates them across Minutes list and info responses.

## Normal Production Flow

The existing producer-consumer architecture remains unchanged.

1. The producer reads and normalizes Minutes metadata.
2. If the actual duration is less than ten minutes, it records the discovery
   outcome for the current producer/replay report and stops processing that
   record.
3. The producer applies the existing activation, ended-state, calendar-match,
   Derek-attendance, ten-minute-settle, and stable-ID rules.
4. The consumer reads the complete source.
5. The agent first classifies whether the source is an actual candidate
   interview. If yes, it returns `no_action` without any delivery discovery.
6. Otherwise the existing disagreement, alignment, Derek viewpoint, target,
   mention, and delivery contracts apply unchanged.

The new ten-minute duration gate is distinct from the existing ten-minute
settling delay: one excludes recordings shorter than ten minutes; the other
waits ten minutes after an eligible meeting ends before analysis.

## Controlled Replay of the Ten Most Recent Records

Add an explicit one-shot CLI command for bounded replay. It must not alter the
persisted activation watermark and must not widen the recurring producer's
lookback.

The command accepts:

- `--limit`, required to be a positive bounded integer; this run uses `10`;
- `--offset`, a non-negative position within the newest bounded page; this run
  uses `limit=1, offset=0` and then `limit=9, offset=1` for a non-overlapping
  small-batch rollout;
- the existing database, workspace, and corpus settings;
- the existing dry-run/live-send settings. The approved run uses live send.

The command performs these steps:

1. Fetch exactly the most recent `limit` raw Minutes list records in source
   order.
2. Produce one replay result for every selected record, even when no meeting
   job is created.
3. Apply the short-duration, source-integrity, unique-calendar-match, and
   Derek-attendance gates.
4. For an eligible stable meeting ID:
   - never reopen a confirmed `sent` job;
   - never resend a job whose saved delivery evidence confirms success;
   - allow a pre-activation `no_action` job with no send evidence to be reopened
     explicitly for this replay;
   - allow an absent job to be created;
   - preserve existing immutable run records.
5. Analyze and, in live mode, deliver through the normal consumer and delivery
   executor. No separate manual-send implementation is allowed.
6. Poll retryable work until every selected record reaches a final replay
   outcome or the normal bounded attempt limit is exhausted.

The replay command is intentionally explicit and bounded. The recurring
service never reopens terminal `no_action` jobs on its own.

## Replay Results and Audit

Each of the ten raw records receives exactly one report outcome:

- `short_recording`;
- `source_incomplete`;
- `calendar_not_unique`;
- `derek_not_attendee`;
- `candidate_interview`;
- `no_action`;
- `sent`;
- `already_sent`;
- `failed`.

Eligible agent invocations continue to create immutable
`meeting_alignment_runs` and appear in the shared History UI. Deterministic
pre-queue exclusions such as `short_recording` remain in the bounded replay
report rather than creating fake agent runs.

For a candidate interview, the normal meeting job and a real `no_action` run
remain visible in History because transcript-level classification requires an
agent invocation. The audit summary must make clear that it was excluded as an
actual candidate interview, not that the transcript lacked disagreement.

The command emits a machine-readable summary containing meeting ID, title,
duration, final outcome, job ID when present, run ID when present, delivery
target when sent, error when failed, and whether the outcome was produced by a
deterministic gate or the agent.

## Delivery and Safety

Replay delivery uses all existing safeguards:

- multi-party meetings send only to the highest-ranked sendable group;
- multi-party meetings never fall back to direct messaging;
- strict one-to-one meetings send only to the other participant;
- an ad-hoc call without a calendar event is one-to-one only when the complete
  transcript proves exactly Derek and one uniquely resolved employee;
- real mention identities are resolved before delivery;
- no reaction or DING is added;
- confirmed sends reuse the reply-agent notification bridge and open the
  delivered DingTalk conversation when clicked;
- persisted `ready_to_send` state precedes an external send;
- ambiguous send results are reconciled by identifier and never blindly resent;
- confirmed `sent` jobs are immutable for replay.

Before the live replay, back up the production SQLite database. Start with one
selected eligible record as a small-batch proof, verify its actual persisted
and external outcome, and only then process the remainder of the ten.

## Failure Handling and Monitoring

- A short recording is a successful skip, not an error.
- A candidate interview is terminal `no_action`, not an error.
- Missing or ambiguous source/calendar evidence produces a replay exclusion and
  does not guess.
- Retryable Minutes, Codex, group-discovery, identity, or delivery failures use
  the existing bounded retry behavior.
- Non-retryable invariant failures remain visible as `failed` with evidence.
- A failure in one replay item does not stop the remaining selected items.
- Reply and task queues remain isolated from replay work.

Monitoring is complete only when:

- all ten raw records have a report outcome;
- no selected meeting job remains `waiting`, `pending`, `processing`, `retry`,
  or `ready_to_send`;
- every external send has confirmed delivery evidence;
- no duplicate send occurred;
- no new unresolved reply or meeting queue backlog remains;
- the main launchd service is running and the audit web endpoint returns HTTP
  200.

## Testing

### Unit tests

- 9 minutes 59.999 seconds is skipped before calendar lookup.
- exactly 10 minutes remains eligible.
- duration uses validated normalized timestamps.
- interview scope distinguishes an actual candidate interview from recruitment
  planning and requirement-alignment meetings.
- candidate interview decisions contain no target, mentions, questions, or
  final message.
- replay limit validation and exact newest-record selection.
- replay never reopens `sent` or confirmed-send evidence.
- replay may reopen only explicitly selected, unsent historical `no_action`
  jobs.

### Integration tests

- short recordings do not create jobs or invoke the agent.
- candidate interviews create a visible `no_action` run and perform no group
  discovery or send.
- eligible replay items use the normal consumer and delivery executor.
- each selected raw record receives one report outcome.
- one replay-item failure does not prevent later items from completing.

### Runtime verification

1. Run focused producer, consumer, agent, store, CLI, delivery, and History
   tests.
2. Run the complete tracked non-live suite and meeting semantic evaluations.
3. Commit runtime changes.
4. Back up the live SQLite database.
5. Restart `com.ceo-agent-service.main` and verify a new running PID.
6. Replay one eligible selected record as the small batch and inspect its live
   DB, History, and delivery evidence.
7. Replay the remaining records to complete the newest ten.
8. Monitor until all acceptance conditions are met.

## Acceptance Criteria

- Recordings shorter than ten minutes never enter the meeting queue.
- Exactly ten-minute recordings remain eligible.
- Actual candidate interviews never search for a group or send a message.
- Recruitment planning and hiring-requirement meetings are not classified as
  candidate interviews merely because they discuss recruiting.
- The recurring service retains its activation watermark and never performs an
  accidental historical backfill.
- The controlled command evaluates exactly the ten newest raw Minutes records.
- Confirmed prior sends are never reopened or duplicated.
- Eligible historical `no_action` jobs can be explicitly and safely replayed
  without deleting immutable History.
- Every selected record has a final replay outcome.
- Every qualifying `send` result is delivered through the existing production
  executor and appears in History.
- Completion leaves no unresolved meeting or reply backlog and no unexplained
  new service error.
