# Direct Agent Session Serialization

Each Direct Agent run acquires the durable `codex_session_locks` row for its
conversation before creating an Agent run or starting Codex. A second task for
that conversation returns to `pending` with a short delay and does not consume
a business retry. This prevents concurrent `codex exec resume` calls from
writing to the same Codex session.

If the service stops mid-run, the existing stale-lock expiry permits the
persisted task to resume. Nonzero Codex exits retain a redacted stderr summary
in the Agent run's structured error; prompts, command arguments, and
credentials are not stored in that diagnostic field.
