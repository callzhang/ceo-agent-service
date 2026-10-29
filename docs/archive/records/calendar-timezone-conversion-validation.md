# Calendar timezone conversion validation

## Reason for the change

The synthetic Runtime Context evaluations in `evals/runtime_context/REPORT.v1.md`
and `REPORT.v2.md` show that prompt wording alone did not make known-zone
conversion reliable. V2 scored 0/3 for baseline and 1/3 for the revised prompt;
the failures included a wrong London UTC offset and invalid final JSON.

## Current implementation

`convert_dingtalk_calendar_time` is a read-only Consumer/Audit MCP tool backed by
Python `zoneinfo`. It accepts a local ISO datetime without an offset and two
IANA zone names. It returns the source offset, UTC instant, target local time
and target offset. Ambiguous source times return both folds. Nonexistent times
return `nonexistent` with no proposed instant. Offset-bearing local datetimes,
invalid datetimes and unknown zones return explicit status values.

The tool converts time only. It does not read events, determine free/busy,
create or update calendar events, send messages, or establish another
participant's availability. Existing source-binding and scheduling rules stay
in force; usual working hours do not become confirmed availability.

Deterministic conversion tests cover both participants' transition boundaries:
Los Angeles spring/fall changes and London's spring gap/fall fold, including
the UTC and target-local representation for both London folds.

## Duplicate conversion found in trajectory review

The anonymous Calendar output-format rerun produced an exact semantic repeat:
Consumer converted the ambiguous Los Angeles 01:30 to UTC, then converted
08:30Z back to Los Angeles twice (once with `+00:00`, once without an offset).
The first result already contained both folds, `source_local`, UTC and
`target_local`. The repeat came from the Skill's instruction to reverse-convert
and verify the same instant. The managed Skill now tells the Agent to reuse the
complete result and only recalculate when the candidate time or target zone
changes. Timezone and availability rules are unchanged.

Post-change native Consumer cold/resume preserved `no_action`, the exact
folds, and the confirmed 09:30Z update, with one conversion call per turn. A
matched baseline/optimized comparison using the same explicit London display
zone also made one conversion call per turn in both variants and had no failed
tool reads. Therefore the earlier duplicate was not reproduced in the matched
case, and a call-count reduction is not established. The Skill wording now
matches the converter's complete return value; retain broader cases as a
follow-up before claiming runtime savings. The matching managed-Skill digest
is recorded in `ci/consumer-system-contract-release.json`; this local change
has not been published or deployed.

## Same-session conversion reuse after exact fold confirmation

A six-turn anonymous comparison tested whether the exact converter result can
be reused when a resumed source confirms one fold. The narrow candidate that
required all three converter inputs to be identical did not reduce calls: the
cold request converted Los Angeles 01:30 to London, while the updated source
confirmed the already-listed 09:30Z instant and the model requested UTC to
London. The input tuple changed even though the prior conversion had already
returned that exact instant and its London representation.

The final Skill wording permits reuse in either of two cases: the full
`local_time`/`source_timezone`/`target_timezone` tuple is unchanged, or a newly
confirmed UTC instant exactly matches a previous conversion choice and the
target timezone is unchanged. The candidate made one cold conversion and no
resume conversion; both arms still reread the updated messages and calendar.
The resumed result remained valid `no_action`, selected fold 1 at 09:30Z,
reported Los Angeles 01:30 PST and London 09:30 UTC+00:00, and did not claim an
event was created or that the counterpart was free.

This is one matched synthetic pair, so it shows a one-call reduction on this
case, not a general token/cost reduction or production effect. The full three-
arm comparison, including the failed strict-equality trial, is in
`evals/trajectory_optimization/calendar_session_conversion_reuse.native.v2.json`.

## Verification

- `CEO_USER_PROMPT_TEMPLATE_PATH=app/defaults/user_prompt.md pytest -q
  tests/test_calendar_timezone.py tests/test_agent_cli_timezone.py
  tests/test_calendar_skill_conversion_contract.py`: 9 passed.
- `ruff check app/calendar_timezone.py app/agent_cli.py
  app/wechat/codex_safety.py tests/test_calendar_timezone.py
  tests/test_agent_cli_timezone.py`: passed.
- `git diff --check` on the owned source, test, claim and documentation files:
  passed.
- Anonymous native Consumer cold start and same-session resume, using an
  isolated Codex home and fake read-only MCP tools: both completed and selected
  the current 09:30Z fold, rendered as 09:30 Europe/London, preserved the
  withdrawn 08:30Z proposal, and kept the replacement unconfirmed. Both turns
  called the message fixture and converter.
- An early anonymous Audit cold/resume run appeared to show a disagreement
  about a repeated local hour. The later approved, matched cold/resume
  comparison supersedes that interpretation: Audit wire-only and native-schema
  agreed to approve the valid clarification on cold start, then return it on
  resume because the source had since confirmed the exact UTC time while the
  candidate still asked for it. That was a correct stale-candidate finding,
  not a timezone-conversion or Audit consistency defect. The native Audit
  sessions are `01a14653-d8b4-7e60-819d-5f45c29dcd77` and
  `01a14654-3a87-7710-afb6-443c85782c3d`; both output formats agreed on the
  business result. The separate native-schema Consumer rejection occurred
  before inference and is documented in the trajectory review.
- Earlier resume attempts did not reach the model: the test command passed an
  unsupported `--sandbox` flag, and the first cold sessions were ephemeral and
  therefore had no resumable rollout. The successful continuation evidence is
  from the later persistent sessions in a temporary isolated Codex home.

Native tests prove that the model can select the converter and that Consumer
resumption can retain the decision on this synthetic fixture. They do not
validate the production prompt assembler, full scheduling workflow, actual
calendar reads, production route, or production behavior. The Audit resume
return was correct stale-candidate handling after the fixture's source changed,
not a timezone or Audit consistency failure. No production source was read, no
business MCP was connected, and nothing was published or deployed.
