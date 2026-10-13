# Calendar organizer source preservation

The actual DWS event projection flattened provider organizer objects into a
display string, losing original identity fields. PR79 preserves the exact object
as organizer_details alongside the unchanged organizer display string. Strings
still produce empty details. No identifier inference, notification, audit policy,
route or authorization behavior is introduced.

Six source regressions fail before the two-line fix, then pass. Earlier source/
reader and calendar-worker coverage passes402 checks. Exact head76c9f42e Quality
run38463468779 passes, including the normal Vercel preview. Current-main metadata
publication is subsequently merged with only a claims appendix conflict; both
claims survive.26 source/integration checks pass and the relative runtime diff
still contains only the two DWS projection lines.

PR79 merges as3e7052dc. Formal deploy deploy-6fd268b3-ee0b-4cbb-9233-a98a3cf9fc32
reports succeeded, ba8b5931 ->3e7052dc. Backup completeness is verified. New
supervisor PID74842, health, workers, Attention, History and both Consumer/Audit
Settings rendered previews are read back. Reply/Work/Classification/Meeting
queues have0 pending/processing; two existing Reply failures and three pending
mail actions remain.

The deployed DWS source bytes equal the verified development source. Parsing
the existing complete anonymous event fixture preserves the exact organizer
object, display, leading-zero string and nested false/null. An initial probe
omitted event times and returned None as designed; only the fixture was corrected.
This is source/operational readback, not a fresh provider calendar read, Agent
business outcome or recipient authorization. Compact receipt is
`evals/trajectory_optimization/calendar_organizer_source_release.v1.json`.
