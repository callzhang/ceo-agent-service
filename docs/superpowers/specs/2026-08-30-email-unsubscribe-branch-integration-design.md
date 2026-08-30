# Consumer-Direct Email Unsubscribe Integration Design

**Date:** 2026-08-30

**Status:** Approved design, pending written spec review

**Repository:** `/Users/derek/Documents/Projects/ceo-agent-service`

**Feature branch:** `codex/email-ceo-agent-integration`

**Target branch:** `main`

## 1. Purpose

Complete automatic email unsubscribe with one simple runtime owner:

```text
Email classifier nominates a subscription candidate
    -> Consumer Agent reads the complete email and thread
    -> Consumer decides whether the authorized unsubscribe should run
    -> Consumer invokes one narrow headless-browser unsubscribe operation
    -> service stores the terminal page text and outcome
```

An unsubscribe task does not enter the Audit Agent feedback/revision loop. This
is an explicitly approved exception for `channel=email` + `unsubscribe` only.
It does not change the audit lifecycle for replies, messages, approvals, or any
other external action.

The design replaces the earlier step-by-step audited unsubscribe proposal in
this document. Existing audited implementation history remains preserved in
Git and existing durable rows remain readable, but the production path defined
here is Consumer-direct.

## 2. Current Truth

The branch contains a mature unsubscribe execution core, browser tests, mail
review boundaries, and an independently supervised Email worker. The current
unsubscribe core still expects Audit-accepted operations and the Email worker
does not yet connect a Consumer task to a complete production unsubscribe run.

The production runtime currently has no active email classifier registry, no
production classifier metrics, no saved email classifications, and no category
configuration rows. Therefore production F1, precision, and recall are `N/A`.

The only available classifier quality evidence is an experiment on 73
provisionally labelled emails:

- about 65.75% aggregate accuracy;
- about 65.77% Macro F1;
- no durable, trustworthy aggregate precision or recall report;
- no trustworthy `subscription` precision, recall, or F1 because the category
  support is too small;
- threshold experiments reached at most about 84% automatic precision on
  limited provisional slices, below the automatic-action bar.

These numbers cannot authorize model-only unsubscribe.

## 3. Decisions and Alternatives

### Selected: classifier recall plus Consumer confirmation

The classifier only narrows the candidate set. Consumer reads the complete
email and thread, makes the final semantic decision, and invokes the bounded
unsubscribe capability. No Audit Agent is created for that task.

This preserves automation while avoiding both weak classifier-only decisions
and the cost of sending every incoming email to Consumer.

### Rejected: classifier-only execution

Current production metrics do not exist and provisional quality is too low.
A high top-1 score is not calibrated proof that Derek no longer wants a
subscription.

### Rejected: send every email to Consumer

This would remove the classifier dependency but would run an Agent on every
message, increasing cost and latency while asking Consumer to repeatedly reject
ordinary mail.

## 4. Scope

In scope:

- cold-start and post-validation unsubscribe trigger gates;
- one Consumer-owned decision and execution turn;
- one narrow task-bound unsubscribe operation;
- RFC one-click and ordinary HTTPS unsubscribe pages;
- a dedicated persistent headless browser profile for ordinary pages;
- bounded terminal page/response text as the user-facing result;
- deterministic outcomes for login, CAPTCHA, payment, missing entry, and
  browser failures;
- task idempotency, restart behavior, tests, service restart, and readback;
- the explicitly scoped architecture-document exception to the normal
  Consumer/Audit lifecycle.

Out of scope:

- use of Derek's main Chrome profile or its complete cookie store;
- password, password-manager, MFA, QR-code, or CAPTCHA automation;
- arbitrary web browsing from email content;
- automatic `mailto:` unsubscribe messages;
- attachment content processing;
- automatic replies or any non-unsubscribe external effect;
- deletion of historical audit/effect rows or destructive schema cleanup;
- real unsubscribe-site access during tests or deployment verification.

## 5. Classification and Trigger Gate

### 5.1 Category meaning

`subscription` means an unwanted bulk subscription that Derek does not want to
continue receiving. It does not mean every newsletter or every email containing
the word `unsubscribe`.

The classifier never constructs or selects a private unsubscribe URL. It only
returns category, confidence, alternatives, and model version.

### 5.2 Cold start

Until all model-readiness gates pass, only an explicit user-confirmed
`subscription` classification may create an unsubscribe `ActionPlan`.
Model-predicted subscription items remain in Email pending feedback and cause no
external action.

### 5.3 Automatic-candidate readiness

Model predictions may nominate automatic candidates only when all of the
following are true:

- an active model and immutable metadata exist;
- the time-ordered validation report includes precision, recall, and F1 for
  `subscription`;
- validated `subscription` precision is at least 0.95;
- `subscription` validation support is at least 20;
- the configured category threshold exactly matches the threshold evaluated by
  the promoted model;
- the category metadata says `auto_action_eligible=true`;
- the individual prediction reaches the configured `subscription` threshold.

Precision is the action gate because a false positive is more costly than a
false negative. Recall and F1 must still be recorded and shown for diagnosis,
but they are not independent authorization gates in v1.

Changing the threshold after training makes eligibility stale and returns the
category to pending feedback until a new model is validated and promoted.

### 5.4 Reliable entry gate

An unsubscribe task additionally requires one reliable entry:

1. authenticated RFC `List-Unsubscribe-Post: List-Unsubscribe=One-Click` with
   the required signed headers;
2. an HTTPS entry in `List-Unsubscribe`;
3. an HTTPS link whose visible label or immediate context explicitly means
   unsubscribe/退订.

`mailto:` alone is not executable in v1. No component may guess an unsubscribe
URL. Private URLs and query tokens remain runtime-private and durable records
use opaque references.

## 6. Consumer Contract

The existing immutable `ActionPlan` remains the only action authorization.
Consumer cannot add `unsubscribe`, switch to another email, or broaden the plan
to another action.

Consumer must:

1. read the complete current email and relevant thread through the Email
   context source;
2. read the installed mail-review Skill;
3. confirm that the message is an unwanted bulk subscription rather than work,
   security, billing, order, account, or personal mail;
4. consider prior replies and thread interaction before deciding;
5. return `no_action` when the evidence does not support unsubscribe;
6. when it does support unsubscribe, invoke the single task-bound operation and
   report the operation's structured outcome and result text without rewriting
   or summarizing it.

The operation surface accepts task identity and current execution generation,
not a raw URL. The service resolves the ActionPlan, account/message/thread
identity, private entry, and browser policy internally. The implementation
exposes this as one local CLI operation available only to an actively claimed
`channel=email` unsubscribe task. Its public arguments are the task ID and
current execution generation; the worker DB path comes from the service's
existing runtime configuration. It is not a general browser tool.

Consumer owns both the decision and execution turn. The service-provided
operation performs low-level browser navigation, bounded control selection,
idempotency, and persistence. No Audit run, audit feedback, or revision loop is
created.

## 7. Headless Browser Runtime

Every production unsubscribe browser is launched with `headless=true`. The
operation must not open the system browser, control the in-app browser, bring
Chrome to the foreground, create a visible window, or change the user's current
tabs.

### 7.1 RFC one-click

Authenticated RFC one-click runs in an isolated temporary context:

- POST only;
- exact body `List-Unsubscribe=One-Click`;
- no cookies, even if the dedicated profile has cookies for the domain;
- no GET substitution;
- terminal HTTP response required;
- bounded response body recorded as result text when present.

An empty successful response is represented by deterministic protocol text such
as `List-Unsubscribe POST returned HTTP 204`; this is application-generated
protocol evidence, not an LLM summary.

### 7.2 Ordinary HTTPS pages

Ordinary pages use a dedicated persistent profile under the service runtime
directory, for example:

```text
<runtime-dir>/email-browser-profile/
```

The directory is owner-only. A profile lock serializes access so two consumers
cannot launch the same persistent profile concurrently. Each task launches
headless Chromium with that directory, performs the bounded flow, closes the
page/context/browser, and releases the lock.

The dedicated profile may retain its own cookies and local storage. It must not
point at Chrome's default User Data directory, copy the complete main profile,
attach to the user's active Chrome session, or import the user's full cookie
store.

Navigation remains limited to the reliable entry origin and explicitly allowed
redirect origins. Popups, downloads, new windows, service workers, private
network targets, metadata endpoints, embedded credentials, and unapproved
origins are blocked.

### 7.3 Existing session versus login form

If the dedicated profile already has a valid site session, Consumer may use
that session and continue the unsubscribe flow.

If the site asks for a password, MFA, QR code, account selection that cannot be
resolved from the existing session, CAPTCHA, or a new authorization grant, the
operation stops. It does not inspect password managers, request secrets, reuse
the main Chrome session, or show an interactive browser.

## 8. Result Text Contract

The result shown to the user is the browser's terminal visible body text or the
RFC response body. It is not an LLM-written success summary.

Before persistence, the application:

- normalizes line endings and removes control characters;
- applies the existing sensitive-value redaction rules;
- removes full private unsubscribe URLs, query tokens, cookie values,
  credentials, and local filesystem paths;
- preserves the source language and meaningful line breaks;
- truncates UTF-8 output to at most 16 KiB;
- stores a digest of the normalized full observation for retry comparison.

The public result contains at least:

```json
{
  "outcome": "unsubscribed",
  "result_text": "You have been successfully unsubscribed.",
  "completed": true
}
```

No screenshot, HTML snapshot, DOM dump, cookie, or raw private URL is required.

## 9. Outcomes and Task Projection

The operation uses these business outcomes:

| Outcome | Task projection | Automatic retry | User presentation |
| --- | --- | --- | --- |
| `unsubscribed` | `done` | no | 已退订 + result text |
| `already_unsubscribed` | `done` | no | 已经退订 + result text |
| `consumer_no_action` | `done` | no | Consumer 判断不应退订 |
| `no_reliable_entry` | `done` with skipped result | no | 未退订：没有可信入口 |
| `login_required` | `done` with skipped result | no | 未退订：需要登录 |
| `captcha` | `done` with skipped result | no | 未退订：需要验证码 |
| `payment_required` | `done` with skipped result | no | 未退订：需要付费操作 |
| `browser_error` | retry, then `failed` | bounded | Attention after terminal failure |
| `provider_auth_error` | retry, then `failed` | bounded | Attention after terminal failure |
| `outcome_unresolved` | retry, then `failed` | bounded | Attention after terminal failure |

The service does not introduce `discard` or `discarded`. Skipped outcomes are
successful completion of a bounded attempt with no external effect, so the
queue task is `done` and the typed result retains the skipped reason.

Login, CAPTCHA, payment, and missing entry appear in Email processed results.
They do not create Attention and do not notify or interrupt the user.

## 10. Idempotency and Recovery

Before any write, the operation checks an existing terminal receipt for the
same ActionPlan/action/account/message/thread/entry identity. A terminal success
returns the existing result and does not reopen the page.

For ordinary pages, a retry first loads the current page and checks for a
terminal already-unsubscribed or success state before clicking. It never blindly
replays a click after an interrupted operation.

Persist only the minimal external-effect facts required by the repository
contract:

- operation `unsubscribe`;
- opaque target reference;
- stable receipt/result identifier when available;
- task identity and execution generation;
- outcome, bounded result text, and observation digest;
- started and completed timestamps.

The new runtime does not require Audit proposals, per-step Audit acceptance, or
Audit continuations. Existing historical claim/effect/continuation records are
preserved and remain readable; removing old tables or rewriting history is out
of scope.

## 11. Architecture and Documentation Exception

`docs/architecture.md` and `docs/runtime-mechanism.md` currently state that all
tasks use Consumer -> Audit -> feedback/revision. Implementation must add one
explicit exception:

```text
channel=email + ActionPlan action=unsubscribe
    -> Consumer decision and task-bound execution
    -> no Audit Agent
```

The lifecycle/policy documentation and its contract tests must be changed in a
separate, clearly named commit from the browser/runtime implementation, as
required by the repository's explicit audit-policy rule. No shared helper may
silently weaken Audit requirements for other task or action types.

## 12. Implementation Boundaries

The implementation plan may reuse and simplify existing components, but the
resulting responsibilities must be clear:

- classifier/store: candidate, metrics, readiness, and immutable ActionPlan;
- Email worker: scan, claim, Consumer orchestration, result projection;
- Consumer adapter: full context plus the one task-bound operation capability;
- unsubscribe operation: entry resolution, headless launch, navigation,
  terminal observation, idempotency, and receipt;
- Email UI/API: pending-feedback metrics and processed outcome/result text;
- supervisor: one independent Email worker under the existing launchd job.

The implementation must remove or bypass Audit-only routing for unsubscribe,
but preserve Audit routing for `auto_reply` and every non-email task.

Unrelated root-worktree changes must remain unstaged and uncommitted. Feature
work should proceed through scoped commits and a clean integration worktree.

## 13. Verification

### 13.1 Classifier and trigger tests

- no active model means no model-triggered unsubscribe;
- user-confirmed subscription can create an ActionPlan during cold start;
- precision below 0.95, support below 20, stale threshold, or missing metrics
  fails closed;
- precision/recall/F1 and support are stored for `subscription`;
- a model candidate never bypasses Consumer;
- a non-subscription or missing reliable entry never executes.

### 13.2 Consumer lifecycle tests

- unsubscribe creates a Consumer run and no Audit run;
- Consumer `no_action` finishes without browser execution;
- Consumer cannot change action/message/plan identity;
- `auto_reply` still uses the normal Audit lifecycle;
- all unrelated task types still use Consumer/Audit feedback/revision.

### 13.3 Browser unit and loopback E2E tests

- Chromium is launched with `headless=true`;
- no system/main/in-app browser is opened;
- ordinary flows persist a dedicated-profile cookie across separate headless
  launches;
- profile locking prevents concurrent persistent-profile launches;
- RFC one-click sends the exact POST body with no cookie even when the
  dedicated profile contains a matching cookie;
- direct success, already-unsubscribed, one-step, and multi-step confirmation
  pages return terminal visible text;
- login, password, MFA, CAPTCHA, payment, popup, download, private-network, and
  unapproved-origin cases stop with their exact outcome;
- result text is bounded and redacted;
- restart/retry returns an existing receipt or reconciles before another click;
- tests use loopback fixtures only and never a real mailbox or website.

### 13.4 End-to-end test

A committed E2E test must cover:

```text
user-confirmed or eligible model classification
    -> immutable unsubscribe ActionPlan
    -> Email reply task
    -> Consumer full-context decision
    -> task-bound headless operation
    -> terminal result text
    -> done reply task/current attempt
    -> zero Audit runs
```

### 13.5 Release checks

Run the focused Email suites, full `tests/test_email*.py`, loopback browser
suite, scoped Ruff, `git diff --check`, and `git show --check`. After each
runtime commit, restart `com.ceo-agent-service.main`, verify a new supervisor
and worker process, HTTP health, Email worker health/readback, and no unresolved
new Email `failed` or `processing/running` backlog.

Deployment verification must not access a real mailbox or unsubscribe site.
Real-provider execution requires a separately selected real input and explicit
authorization.

## 14. Integration Strategy

The branch is a linear descendant of the existing mail boundary and audited
unsubscribe history. Preserve that history and add scoped commits for the
simplified lifecycle, runtime operation, tests, and documentation. Do not
squash or rewrite the existing chain merely because the final architecture no
longer uses its Audit continuations.

After all gates pass, integrate with a normal `--no-ff` merge from a clean
`main` worktree, push without force, restart launchd, and verify the exact remote
and running revision. Shared uncommitted WIP must not enter the merge.

Known unrelated production backlog must be reported separately and must not be
replayed, resolved, or cleared as part of Email unsubscribe integration.

## 15. Completion Criteria

The feature is complete only when:

- the classifier cold-start and automatic-candidate gates are enforced;
- current production metrics are reported truthfully as absent until an active
  model is trained and promoted;
- Consumer makes the final unsubscribe decision and no unsubscribe Audit run is
  created;
- the operation runs only in background headless Chromium;
- ordinary pages use only the dedicated persistent profile;
- RFC one-click remains cookie-free;
- valid dedicated-profile sessions may continue, while password/MFA/CAPTCHA
  paths stop without disturbing the user;
- terminal result text is stored and shown without LLM synthesis;
- skipped outcomes are visible in Email processed results and do not create
  Attention;
- retry/idempotency prevents blind duplicate execution;
- focused, full Email, browser, and E2E tests pass;
- architecture documents contain the narrow lifecycle exception;
- `main` is merged, pushed, restarted, and read back with no new Email backlog;
- unrelated worktree WIP and production backlog remain untouched.
