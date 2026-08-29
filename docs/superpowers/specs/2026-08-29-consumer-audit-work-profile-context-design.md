# Consumer/Audit Work Profile Context Design

Date: 2026-08-29
Status: Approved principle; implementation pending written-spec review

## 1. Problem

The ordinary message-processing runtime completes its Consumer Agent A and
Audit Agent B lifecycle without supplying either role with the configured work
profile. The profile file and `work_profile_instruction()` already exist, but
the ordinary Consumer/Audit prompt builders do not use them. The meeting
alignment Agent is currently the only runtime path that passes this profile to
an Agent.

This omission allows a structurally valid, safe, and executable candidate to
pass Audit even when it does not represent how the configured principal would
engage with the message. Attempt `8308` demonstrated the failure: substantive
financial analysis and supplementary materials received only a factual receipt
confirmation. That response avoided unsupported endorsement, but it did not
provide feedback or continue the discussion.

The bug is not the absence of a finance-specific template. The runtime omitted
an existing source of user-specific judgment from both roles that decide and
review the reply.

## 2. Design principle

The configured work profile, complete conversation context, and inspected
materials jointly guide whether and how the principal should respond.

A receipt acknowledgment may be appropriate as an opening or an interim state,
but receipt alone does not complete a response when the incoming message
contains substantive material that calls for engagement from the principal's
role. What constitutes appropriate engagement remains an Agent judgment based
on the current profile and evidence. The service must not prescribe a fixed
reply structure, a number of questions, a required conclusion, or a
domain-specific script.

The profile remains subordinate to hard boundaries: it cannot authorize an
external action, invent facts, relax personnel or approval safeguards, or
override the typed Consumer/Audit result contracts.

## 3. Runtime data flow

The existing lifecycle remains unchanged:

```text
message and material context
          |
          v
Consumer Agent A -> typed candidate -> Audit Agent B -> execute or feedback
```

The fix adds the same current profile input to both decision points:

```text
configured work profile ---------> Consumer Agent A
message and material context ----> Consumer Agent A
                                      |
                                      v
                                typed candidate
                                      |
configured work profile ----------> Audit Agent B
message, evidence, and candidate --> Audit Agent B
                                      |
                                      v
                              execute or feedback
```

`work_profile_instruction()` remains the single renderer for profile content
and its usage boundaries. Consumer and Audit receive the rendered instruction
for each new runtime turn so a profile update applies without duplicating its
content into another configuration or prompt asset.

Audit uses the same profile input as Consumer. It does not rewrite the
candidate. When the candidate is safe but fails to engage with the substantive
input in a way consistent with the profile and context, Audit returns the
existing canonical `feedback_provided` result with a concrete observation and
requested revision. Consumer then produces the replacement revision through
the existing feedback lifecycle.

## 4. Components

### 4.1 Profile rendering

Continue to use `app.prompt.work_profile_instruction()` and the configured
`CEO_WORK_PROFILE_PATH`. Do not create a second profile reader, cache, database
projection, or domain-specific persona layer.

The rendered instruction must continue to tell the Agent to translate the
profile into judgment order, follow-up style, expression, and boundaries rather
than quote profile sections or evidence identifiers.

### 4.2 Consumer instructions

Add the rendered work-profile instruction to the authoritative Consumer
developer instructions used by ordinary message tasks. Consumer still receives
the existing task context, dynamic Skill protocol, role boundary, Audit rules,
and typed schema.

The profile is guidance for forming the candidate, not an additional result
field and not a service-side classifier.

### 4.3 Audit instructions

Add the same rendered work-profile instruction to the authoritative Audit
developer instructions. Audit must consider whether a candidate actually
responds to the message in a way supported by the current profile and context,
in addition to its existing checks for evidence, target, authority, risk, and
execution correctness.

Audit must use `feedback_provided` when the business meaning or completeness of
the reply needs revision. It must not edit or replace the candidate itself.

### 4.4 Message-triage principle

Clarify the repository `ceo-message-triage` Skill at the semantic level:

- choose the smallest response that genuinely satisfies the message in the
  principal's role and current context;
- distinguish an incoming acknowledgment, which often needs no text reply,
  from an outgoing receipt-only response to substantive material;
- do not treat receipt confirmation as completion merely because it is safe;
- let the current work profile and inspected evidence determine the substance
  and form of the response.

Do not add keyword lists, regular expressions, sender-specific branches,
finance-specific rules, fixed response templates, minimum response lengths, or
required question counts.

## 5. Error and lifecycle behavior

- A missing or empty configured profile preserves the existing placeholder or
  empty-profile behavior of `work_profile_instruction()`; it does not invent a
  user persona.
- An unreadable profile is a configuration/runtime failure and must remain
  visible through the ordinary Agent failure path rather than silently
  fabricating profile content.
- A candidate that is factually or operationally incomplete continues to use
  existing Audit feedback and failure rules.
- A candidate that is safe but semantically insufficient uses
  `feedback_provided`; no new status or outcome is introduced.
- Existing session, revision, effect, and external readback behavior is
  unchanged.

## 6. Regression and verification

The implementation requires tests that fail against the pre-fix runtime and
pass after the change:

1. Consumer developer instructions contain the current configured work-profile
   instruction.
2. Audit developer instructions contain the same current configured
   work-profile instruction.
3. Updating the configured profile changes the instructions used for a new
   turn without changing code or duplicating profile storage.
4. Consumer and Audit profile injection preserves existing typed contracts,
   dynamic Skill instructions, hard role boundaries, and Audit rules.
5. The message-triage Skill states the semantic distinction between an incoming
   acknowledgment and an outgoing receipt-only response to substantive input,
   without adding domain templates or keyword routing.
6. An attempt-8308-shaped regression fixture demonstrates that a receipt-only
   candidate is not considered a complete response to substantive material;
   Audit requests a Consumer revision through the existing feedback outcome.
7. A context in which a brief acknowledgment is genuinely sufficient remains
   permitted; the fix is not a blanket ban on concise replies or the word
   "received."

Run focused prompt, Agent context, message-triage Skill, and Consumer/Audit
tests first. Then run the relevant broader runtime suite and repository text or
lint checks required by the feedback-processing Skill.

After the implementation commit, restart
`com.ceo-agent-service.main`, verify a new PID, read `launchctl` state, poll
`/healthz`, and verify there is no failed or processing runtime backlog. Patch
feedback item `manual:8308` through the local Console API with attempt `8308`,
run `5903`, Workbench associations, commit, test, restart, and health evidence.
Read the item and batch back before resolving
`feedback-import:manual:8308`, then verify the resolved state through the API.

## 7. Out of scope

- Finance-specific analysis or reply templates;
- keyword or regular-expression classification of acknowledgment text;
- fixed question, paragraph, conclusion, or response-length requirements;
- a new Agent, status, feedback workflow, or profile store;
- changing external-action authority or the Consumer/Audit role boundary;
- modifying the unrelated in-progress email feature work in the current
  worktree.
