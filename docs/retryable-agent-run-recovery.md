# Retryable Agent Run Recovery

On service startup, the reply queue restores a terminal runtime failure only
when the persisted agent run explicitly says it is retryable and records no
external side effect. The task keeps its execution generation, reduces the
interrupted retry count by one, and receives one normal bounded retry.

This recovery never changes a task whose run has an unknown or completed side
effect. Those cases remain in reconciliation or their existing terminal state
so a message, approval, calendar action, or other visible action is not
duplicated.
