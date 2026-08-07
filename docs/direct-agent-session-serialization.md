# Consumer Agent Session Serialization

This historical filename is retained for existing links. It documents the current
Consumer Agent A session behavior, not the superseded Direct Agent runtime.

Each Consumer Agent A turn acquires the durable `codex_session_locks` row for its
conversation before creating an Agent run or starting Codex. A second task for
that conversation returns to `pending` with a short delay and does not consume
a business retry. This prevents concurrent `codex exec resume` calls from
writing to the same Codex session.

If the service stops mid-run, the existing stale-lock expiry permits the
persisted task to resume. Nonzero Codex exits retain a redacted stderr summary
in the Agent run's structured error; prompts, command arguments, and
credentials are not stored in that diagnostic field.

Consumer and Audit results are validated locally after Codex exits. The Agent
commands do not use Codex `--output-schema`: that upstream mode can reject a
request before the Agent can execute the task. Audit B's OA receipts must place
the verified read-back payload in `oa_action_receipt.result` so the completed
action can be persisted and reconciled.

The service also does not force a Codex reasoning-summary setting. Model
providers negotiate supported response fields themselves; forcing a setting
that a provider does not support prevents an Agent process from starting.
