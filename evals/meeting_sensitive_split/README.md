# Meeting sensitive private delivery evaluation

Expected behavior: ordinary information goes to the appropriate business group;
sensitive conclusions or actions go to an explicitly selected private recipient.
Source evidence can support selecting the principal. A sensitive-only meeting
sends only its private summary. Omitting that summary is not completed delivery.

`v1.json` fixes four entirely fictional sources, inline group evidence and frozen
Audit candidates. Both refs use the same Claude CLI, Sonnet, high effort,
180-second per-call timeout and sequential execution, with all tools and MCP
disabled. No actual meeting, contact message or production task is an input.
The baseline is `8834a9ffe`; the candidate is `dd4987a12`.

`native-summary-v1.json` contains only aggregate scores, fictional case/target
labels, outcome enums and verification hashes. Raw model outputs and native
session identifiers are excluded from this public repository.

| Routing and split check | Baseline | Candidate |
| --- | --- | --- |
| Mixed content, source-supported principal private recipient | 0/2 | 2/2 |
| Missing private summary with a recorded gap | 1/2 | 2/2 |
| Sensitive-only meeting | 0/2 | 2/2 |
| Ordinary content control | 2/2 | 2/2 |
| Total planner and Audit checks | 3/8 | 8/8 |

The score checks delivery action, exact group/private target, sensitive marker
separation, source-aware schema validation and Audit outcome. It is not a full
factuality or prose-quality score. Manual review found inferred training timing
and occasional erroneous dates in Audit explanations. The frozen candidate
rationale also incorrectly claims omission in the valid-principal and ordinary
control cases; both refs saw the same rationale. These limits do not establish
acceptance on natural production meetings.

Focused regressions cover split/private-only delivery, source identities,
ordinary no-self fallback, receipt reuse, Audit prompt rules, calendar content
and notification previews. Run:

```sh
python -m pytest -q tests/test_meeting_alignment.py tests/test_meeting_alignment_agent.py tests/test_meeting_alignment_delivery.py tests/test_meeting_alignment_models.py
python scripts/eval_meeting_sensitive_split.py --candidate-ref dd4987a12 --output /tmp/meeting-sensitive-native.json
```

Use the standard deploy gate. Publish only the meeting Skill using the existing
managed revision/configuration settings and atomic Skill installer, then the
standard restart. Read back the worker's immutable Skill load receipt and exact
scheduled references. Preserve independently customized reporting Skills.
