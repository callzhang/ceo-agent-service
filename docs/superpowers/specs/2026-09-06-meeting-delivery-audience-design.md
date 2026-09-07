# Meeting Delivery Audience Design

## Status

Proposed and approved for planning. This document changes only the meeting
follow-up audience decision. It does not add a new review, confirmation, or
content-redaction policy.

## Problem

The current fallback treats a missing calendar match as permission to build a
complete participant roster from recognised transcript speakers. A transcript
that recognises Derek and one frequent speaker can therefore be treated as a
1:1 meeting even when the discussion is a multi-person customer or delivery
meeting. The service then sends the follow-up as a direct message.

This is the wrong decision boundary. A calendar is useful evidence about
attendees, but the delivery audience must be decided chiefly from the business
content and the group that owns the resulting work.

## Required Outcome

1. A business meeting follow-up is delivered to the business group that most
   directly owns the meeting's decisions, actions, delivery, or customer
   communication.
2. A direct message is permitted only when both conditions are true:
   - reliable evidence proves the meeting is strictly one-to-one; and
   - the content is personal, individual, or otherwise non-business work.
3. A meeting with incomplete attendee evidence must never become a direct
   message merely because the transcript identified only one other speaker.
4. When several business groups are plausible, the agent selects the best
   group itself from evidence. This is not a `needs_human` condition.
5. When the discussion is business-related and no selected business group is
   sendable, the service sends the follow-up to the meeting organizer using
   the stable organizer identity already present in the meeting source.

## Evidence Model

### Attendee evidence

Use the following information to establish only whether a meeting is reliably
strict one-to-one:

1. A uniquely matched calendar event with exactly Derek and one other attendee.
2. A stable meeting-member source, when the provider exposes one, with exactly
   those same two people.
3. Transcript speaker labels are positive evidence that a person spoke. They
   are never evidence that no one else attended.

If the first two sources are absent, incomplete, or conflicting, the audience
decision must treat the attendee roster as incomplete.

### Business-group evidence

The agent searches and ranks groups using the meeting's customer, project,
product module, decision, named owner, delivery milestone, deployment, test,
and customer-communication references. A chosen group must have concrete
evidence that it currently carries the same work, such as ongoing project
messages, owner coordination, a delivery plan, test activity, or a customer
follow-up.

The agent records the candidate groups, the selected group, and the exact
business-continuity evidence in its audit summary. Group title similarity,
partial attendee overlap, and recent activity alone are insufficient.

## Audience Decision

```text
Classify the meeting content.
|
|-- Business-related
|   |-- Discover and rank business groups from delivery evidence.
|   |-- A best supported sendable group exists -> send there.
|   `-- No selected group is sendable -> direct-message the meeting organizer
|       using the source's stable user_id or open_dingtalk_id.
|
`-- Personal / non-business
    |-- Reliable complete evidence proves strict 1:1 -> direct-message the
    |   other participant.
    `-- Otherwise -> do not send automatically.
```

This deliberately makes content classification precede the calendar roster.
The calendar cannot convert a project, customer, product, or delivery meeting
into a personal message merely by listing two attendees.

## Failure Handling

- Calendar lookup failure or no matching event: continue with business-group
  discovery; retain the roster as incomplete.
- Calendar ambiguity: do not choose a calendar event solely to make a direct
  route possible; continue with business-group discovery.
- Group discovery returns several candidates: rank them and let the agent
  choose the strongest one, preserving the comparison evidence.
- The selected group's metadata is incomplete or it is unsendable: send to the
  organizer identified by the meeting source's stable `user_id` or
  `open_dingtalk_id`.
- Never resolve this fallback from a display name or directory search. If the
  meeting source has no stable organizer identifier, keep the delivery retryable
  and do not send.
- The group route and organizer route share the same stable business delivery
  key. A retry or later run therefore reuses the completed provider result
  instead of sending the same follow-up twice.

## Regression Cases

1. A transcript identifies Derek and one speaker, but the content is an Audi
   customer-delivery discussion and a project group has active matching work:
   send to that group, not the speaker.
2. The same condition for a Jianghuai customer requirement and delivery group:
   send to that group, not the speaker.
3. A calendar-confirmed two-person project meeting: send to the evidenced
   business group, not a direct message.
4. A calendar-confirmed two-person personal feedback conversation: direct
   message the other participant.
5. A business meeting with multiple viable groups: send to the best-ranked
   group and persist why it outranked the others.
6. A business meeting with no sendable group and a stable organizer identity:
   send exactly once to that organizer.
7. A selected group whose sendability check fails and whose organizer has no
   stable identifier: do not guess from the name; retain the delivery for retry.

## Non-goals

- This design does not use personal, HR, compensation, performance, or
  sensitive-content labels as an automatic privacy block.
- It does not rewrite the meeting summary solely to change its delivery
  audience.
- It does not create groups or ask a human to choose among otherwise evidenced
  candidate groups.
