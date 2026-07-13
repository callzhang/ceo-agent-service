# Meeting Alignment Agent Design

Date: 2026-07-14

## Goal

Add an automatic post-meeting alignment capability for meetings attended by
Derek. Ten minutes after a meeting ends, the service reads the complete
available DingTalk AI Minutes material and stays silent unless it finds either:

- a core discussion topic on which materially different views were expressed;
- a need to explain a view Derek expressed because the later discussion did not
  fully restore its meaning.

When triggered, the service sends one concise alignment message. For a
multi-person meeting it automatically selects the best matching DingTalk group
and truly mentions the relevant people. For a one-to-one meeting it sends the
message directly to the other participant.

## Product Boundaries

### In scope

- Discover completed AI Minutes records on a recurring schedule.
- Wait until ten minutes after the recorded meeting end time.
- Require Derek to appear in the participant list before analyzing the meeting.
- Read the complete paginated transcript, meeting summary, participant data,
  and other necessary Minutes metadata.
- Detect core disagreements, distinguish aligned from unresolved topics, and
  generate a Derek viewpoint explanation when needed.
- Use Derek's work profile, Friday Memory, and historical cases to help explain
  a view that Derek expressed in the meeting.
- Search and rank DingTalk groups, always selecting the highest-ranked
  sendable group for a multi-person meeting.
- Resolve people to real DingTalk identities and send real mentions.
- Send directly to the other participant only for one-to-one meetings.
- Prevent duplicate processing and duplicate delivery.
- Show Meeting Alignment Agent runs in History using the same interaction and
  presentation contract as the reply agent.

### Out of scope

- Sending ordinary meeting minutes when there is no disagreement and no need
  for a Derek viewpoint explanation.
- Treating silence, lack of further objection, or a unilateral announcement as
  alignment.
- Adding a view, commitment, or conclusion that Derek did not express in the
  meeting.
- Sending a multi-person meeting result by direct message when no suitable group
  is available.
- Editing or retracting a successfully sent message automatically when the
  transcript later changes.
- Replacing the existing reply agent or task agent.

## Architecture

Use the existing producer-consumer operating model, with a new recurring
producer, a new agent type, an independent job queue, and the existing main
launchd service.

```text
Meeting cron producer
  -> meeting_alignment_jobs
  -> Meeting Alignment Agent consumer
  -> deterministic delivery validation and execution
  -> History and audit records
```

The capability runs inside `com.ceo-agent-service.main`. It does not add a
second launchd service or a system crontab entry.

### Meeting cron producer

The producer periodically lists DingTalk AI Minutes records. It performs only
deterministic eligibility and queueing work:

1. Read the stable meeting ID, participant list, meeting status, and recorded
   end time.
2. Revisit the Minutes record on a later poll when participant data is not yet
   available; do not guess participation.
3. Match Derek through the authenticated current-user identity and configured
   principal aliases, not through a hardcoded display name. Ignore the meeting
   unless that identity appears in its participant list.
4. Upsert one `waiting` job keyed by the stable meeting ID after Derek's
   participation is confirmed.
5. Promote the job to `pending` only when the meeting is explicitly ended and
   ten minutes have passed since its recorded end time.

The producer does not detect disagreement, generate content, select a group, or
send a message. Missing end status or end time leaves a confirmed-participation
job in `waiting`. Missing participant data leaves the Minutes record eligible
for discovery on a later poll without creating a job. The producer does not
infer any of these values.

### Meeting Alignment Agent consumer

The consumer claims an eligible job and invokes a new Meeting Alignment Agent.
The agent reads the complete transcript, summary, participant information, and
necessary historical context. It returns a schema-validated decision containing:

- `action`: `no_action` or `send`;
- the trigger reason;
- core topics, views, reasons, and alignment state;
- a Derek viewpoint explanation when applicable;
- the minimum sufficient set of key trade-off questions;
- the people responsible for answering or confirming each section;
- group candidate evidence and the selected target;
- the final message;
- an audit summary and confidence information.

The agent may use DWS to recover meeting and group context. It may use Derek's
work profile and `memory_recall` to find relevant historical cases, but the
meeting transcript remains authoritative for which Derek view may be explained.

### Delivery executor

The delivery executor validates the agent's structured output and performs the
external side effect. It:

- confirms that the selected group is sendable;
- resolves names to unique DingTalk identities;
- supplies real mention identifiers and visible mention names;
- sends the message;
- persists the send result or queries an ambiguous send result before retrying.

The agent does not declare a send successful. Only the delivery result can move
a job to `sent`.

## Meeting Eligibility and Timing

A meeting is eligible only when all of the following are true:

- the Minutes record has a stable meeting ID;
- the Minutes record explicitly says the meeting ended;
- a recorded end time is available;
- at least ten minutes have passed since that end time;
- Derek is in the participant list;
- no successful send or terminal `no_action` result already exists for the
  meeting.

Actual Derek speech is not an eligibility requirement. A meeting Derek attended
may still contain a disagreement worth surfacing even if Derek did not speak.

The first eligible analysis is terminal when it produces `no_action` or a
successful send. Later transcript changes do not automatically reopen it.

## Semantic Decision Rules

### Core disagreement

A core disagreement is a material difference that affects a decision, goal,
resource allocation, ownership, timing, delivery standard, or risk. Wording
differences, repeated confirmations, ordinary exploration, and complementary
observations do not trigger a message.

For every core topic, the agent reconstructs:

- each materially different view;
- the reasons and constraints behind it;
- meaningful responses or concessions;
- the final state of the topic.

### Aligned topics

A topic is aligned only when all relevant sides explicitly agree, commit, or
restate the conclusion consistently. The following are not sufficient:

- silence;
- absence of continued objection;
- the meeting ending;
- a host or senior decision-maker announcing a conclusion without the relevant
  sides confirming it.

For an aligned topic, the message briefly presents the original views and
reasons, then states the final conclusion and why the parties converged.

### Unresolved topics

For an unresolved topic, the message fairly presents each view and its reasons,
then identifies the constraints that cannot all be satisfied simultaneously.

The agent may ask multiple key questions. The questions must be the minimum
sufficient set needed to complete alignment. Each question must expose a
distinct trade-off: what is chosen, what is sacrificed, and what consequence is
accepted. Once all questions are answered, the answers should directly produce
a conclusion rather than start another general discussion.

Each question names and mentions the people best placed and authorized to
answer it. Not every mentioned person needs to answer every question.

### Derek viewpoint explanation

Use the user-facing label `Derek 的观点输出解读`.

This section is generated when Derek expressed a view in the meeting and later
discussion did not fully restore its meaning. It must:

- faithfully state the view Derek expressed in this meeting;
- identify the layer later discussion omitted or distorted without accusing a
  person of failing to understand;
- explain the view in simpler language;
- use an apt analogy and a concrete example when they improve understanding;
- mention the people whose later discussion omitted or distorted the view, or
  the main discussants when those people cannot be identified confidently.

The agent may use Derek's consistent historical reasoning and other historical
cases to supply background, analogies, and examples. Those sources may explain
the expressed view but may not create a new position, promise, or conclusion for
this meeting. The output distinguishes meeting facts, agent synthesis, and
historical examples.

When an unresolved disagreement and a Derek viewpoint explanation both apply,
the service sends one message: explain Derek's view first, then ask the key
trade-off questions.

When an aligned disagreement and a Derek viewpoint explanation both apply, the
same single message includes the viewpoint explanation, the aligned conclusion,
and the reason for convergence. Multiple core topics are likewise consolidated
into one meeting message.

## Target Selection

### One-to-one meetings

When the participant list contains only Derek and one other person, send the
message directly to the other participant. Do not search for a group.

### Multi-person meetings

For a multi-person meeting, generate group candidates from available evidence,
including:

- an explicit group, message, calendar, or Minutes association;
- discussion of the meeting title or core topic before and after the meeting;
- participant activity in the same group;
- overlap between the organizer or main speakers and recent group participants;
- temporal proximity between group discussion and the meeting;
- group title and recent conversation context.

The agent ranks candidates using the evidence and records the basis for the
ranking. It does not use a fixed business-keyword list to decide group
ownership. It always selects the highest-ranked sendable group, even when the
association is weak. If the initial search yields no group, it expands the
search to recently accessible groups.

If no sendable group can be found at all, the job retries. It does not fall back
to a direct message.

## Mention Resolution

The content decision first identifies the relevant viewpoint representatives,
decision-makers, question owners, or people involved in the Derek viewpoint
explanation. The delivery path then resolves each person to a unique DingTalk
identity using the directory, department, title, group context, and meeting
participant evidence.

- Aligned topics mention the main viewpoint representatives.
- Unresolved questions mention the person or people best placed to answer each
  question.
- A Derek viewpoint explanation mentions people whose later discussion omitted
  or distorted the view; if unclear, it mentions the main discussants.
- A person is mentioned only once even if responsible for multiple sections.
- A visible `@Name` without a real DingTalk mention identifier is not treated as
  a successful mention.
- The service never guesses between ambiguous identities.
- An unresolved identity is recorded in the audit result but does not block
  delivery to the selected target with the identities that were resolved.

## Message Format

Send one concise message per meeting. Omit sections that do not apply.

```markdown
会后对齐｜<会议标题>

讨论焦点
<the material disagreement in one sentence>

各方观点
- @A: <view and reason>
- @B: <view and reason>

最终对齐
<explicit conclusion>

形成这个结论的原因
<facts, constraints, or concessions that produced alignment>
```

For unresolved topics, replace the alignment sections with:

```markdown
目前尚未对齐
<the unresolved trade-off without judging who is right>

需要回答的关键问题
1. @A <minimum necessary trade-off question>
2. @B @C <another independently necessary trade-off question>
```

When needed, insert this before the questions:

```markdown
Derek 的观点输出解读
<plain-language statement of the expressed view>

可以这样理解：
<apt analogy>

例如：
<current or historical concrete case>

容易被忽略的一层是：
<meaning omitted or distorted in later discussion>
```

The tone is neutral and direct. It does not write that a named person "did not
understand" or "blocked progress." Every question clearly identifies who should
answer it.

## Persistence and Job States

Use an independent `meeting_alignment_jobs` queue rather than overloading
`reply_tasks` or `work_summary_inputs`. One row per stable meeting ID stores the
source reference and current delivery state.

Required states are:

- `waiting`: discovered but not yet eligible;
- `pending`: eligible and ready for a consumer;
- `processing`: claimed by a consumer;
- `no_action`: analyzed with no message required;
- `ready_to_send`: agent output validated and awaiting delivery;
- `sent`: delivery confirmed;
- `retry`: a retryable stage failed;
- `failed`: a non-retryable validation or contract failure occurred.

Store meeting metadata, participant identities, a transcript digest, compact
evidence excerpts, semantic results, target ranking evidence, final message,
send result, attempts, and stage-specific errors. Do not copy the full
transcript into the database.

Each Agent invocation creates an immutable run record with its Codex session ID,
transcript start and end lines, schema-validated decision, audit summary, and
status. Retries create new runs and never overwrite prior failures.

## History and Audit UI

Meeting Alignment Agent runs use the same History experience as reply agent
runs. Do not create a separate meeting-history product or a substantially
different detail layout.

The implementation should reuse the reply-agent History contract:

- the same chronological feed and card presentation;
- the same status pills and time presentation;
- the same search, filtering, pagination, and auto-refresh behavior;
- the same detail-page structure;
- the same Codex session link and local session rendering;
- the same behavior when the local Codex session file is unavailable.

Meeting-specific values populate the equivalent fields: meeting title as the
source title, the detected trigger as the input summary, the alignment message
as the reply/output, and the selected group or direct recipient as the delivery
target. The structured record additionally exposes participant evidence,
target ranking, mention resolution, and send results in the existing audit
detail pattern.

Meeting runs must not be inserted as fake reply attempts. Their persistence
remains semantically separate, while a shared History event representation and
shared renderers make both run types appear and behave consistently.

`no_action`, `sent`, `retry`, and `failed` runs are all visible. A missing Codex
session file does not hide the structured run record.

## Idempotency and Error Handling

- The stable meeting ID is the enqueue deduplication key.
- Only one consumer may claim a job at a time.
- A terminal `no_action` or confirmed `sent` job is not processed again.
- Complete transcript pagination is required; a partial read retries instead of
  producing a partial judgment.
- A malformed agent decision fails schema validation and is not sent.
- An unresolvable group search retries and never switches a multi-person meeting
  to direct delivery.
- Ambiguous identities are not guessed.
- When a send returns an identifier but completion is unclear, query its status
  before retrying.
- When no reliable send identifier exists and the outcome is ambiguous, record
  the ambiguity for audit and avoid blindly sending a duplicate.
- Meeting pipeline failures do not block the reply agent, task agent, or their
  queues.

## Testing

### Unit and schema tests

- Meeting eligibility, Derek participant matching, ten-minute timing, and
  stable-ID deduplication.
- Complete transcript pagination.
- Agent decision schema validation.
- Job state transitions, exclusive claiming, retry behavior, and terminal
  states.
- Target validation and real mention construction.
- Shared History event adaptation and reply-agent-equivalent rendering.

### Semantic evaluations

- Material disagreement versus ordinary exploration or wording differences.
- Explicit agreement, commitment, or consistent restatement versus silence,
  unilateral announcement, or lack of further objection.
- Aligned and unresolved output shapes.
- Multiple necessary trade-off questions without unnecessary questions.
- Derek viewpoint explanation with a useful analogy and example.
- Historical context that explains the expressed view without inventing a new
  position or commitment.
- Combined unresolved disagreement and Derek viewpoint explanation.

### Integration tests

- AI Minutes discovery through queue creation and consumer claim.
- Full material retrieval, structured decision, group ranking, identity
  resolution, and message delivery using mocked DWS boundaries.
- One-to-one direct delivery and multi-person group delivery.
- No-group retry without direct-message fallback.
- Ambiguous send result verification and duplicate prevention.
- History list, detail, search, filtering, and Codex session linkage.

### Runtime verification

After implementation commits that change runtime behavior:

1. Run focused unit, semantic evaluation, integration, and end-to-end tests.
2. Restart `com.ceo-agent-service.main`.
3. Verify launchd reports a new running process.
4. Verify there is no unresolved `failed` or `processing` backlog in the reply,
   task, or meeting-alignment queues.
5. Verify a Meeting Alignment Agent run appears in History with the same user
   experience as a reply-agent run.

## Acceptance Criteria

- Only meetings with Derek in the participant list and ended for at least ten
  minutes enter analysis.
- A meeting with no core disagreement and no need for a Derek viewpoint
  explanation ends in visible `no_action` history and sends nothing.
- Alignment requires explicit agreement, commitment, or consistent restatement
  by all relevant sides.
- Aligned output summarizes views, the conclusion, and the reason for
  convergence.
- Unresolved output fairly summarizes views and asks the minimum sufficient set
  of decision-completing trade-off questions.
- Derek viewpoint output explains a view expressed in the meeting and may use
  history for explanation without adding a new position or commitment.
- A multi-person meeting is sent to the highest-ranked sendable group and never
  falls back to direct messaging.
- A one-to-one meeting is sent directly to the other participant.
- Resolved people receive real DingTalk mentions.
- One meeting is successfully sent at most once across repeated scans, retries,
  and process restarts.
- Every Agent invocation is visible in History through the same interaction and
  presentation contract as reply agent history.
- The meeting pipeline can fail independently without blocking existing reply
  and task processing.
