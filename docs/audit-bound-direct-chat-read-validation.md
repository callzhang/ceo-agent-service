# Bound direct-chat source read: acceptance evidence

## Scope and cause

In complete Audit trajectories for task IDs 416511, 416516, 416558 and
416573, `read_dingtalk_messages` was called with the task's conversation ID
and `single_chat=true`, but without the sender's openDingTalkId or a title.
The reader passed an empty title to `dws contact user search`, which failed
before reading messages. One Audit then tried the group-only window reader on
the same direct chat and received a provider target error. The task's claimed
trigger carried the stable sender openDingTalkId; the model's omission of that
duplicate tool argument caused the repeated technical Audit failures.

Acceptance: both roles can read the bound direct conversation when the model
supplies only its conversation ID, even if it omits `single_chat`. The explicit
time-window read must be
complete and include only that conversation. Mismatched source message or
conversation identity fails before DWS. Existing explicit direct-target and
group reads, Audit decisions and external action permissions must not change.

## Verification

- Four new focused cases failed before the change: bound recent read, bound
  window read, and two source-identity mismatch cases. After the change, the
  direct-chat test file passed all eleven cases, including its two existing cases.
- The affected local test files passed 110 tests:
  `test_agent_cli.py`, `test_agent_cli_direct_chat.py`,
  `test_dingtalk_message_payload_sharing.py`, and
  `test_dingtalk_audience_reads.py`, plus the role-tool-boundary tests.
- A read-only call through the updated bound `agent_cli` tools against the
  saved task 416573 returned a complete nine-message direct-chat window that
  contained the original trigger, and a five-message recent read whose every
  message matched the bound conversation. No business action was executed.

The fix merged as PR #210 at `36ef34ac50fd94ea7b86933aefdc9e619b042770`.
The standard deploy reached that exact production commit, started PID 89665,
and returned HTTP 200 from `/healthz`. Active and queued task views, Attention,
and History returned HTTP 200. The production `app/agent_cli.py` hash matched
the tested source. A post-deploy read-only call against saved task 416573
returned a complete nine-message direct-chat window containing the trigger;
all five recent messages belonged to the same conversation.

This establishes the deployed source-reader behavior, not that any historical
Audit decision would now approve the candidate. No natural post-release Audit
turn had appeared at the time of this readback, so the business outcome remains
unverified.
