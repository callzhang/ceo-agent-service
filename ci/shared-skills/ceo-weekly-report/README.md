# CEO Weekly Report Skill

This Skill reconciles one Beijing-calendar week of DingTalk evidence into a CEO
management report. It keeps unresolved issues traceable, separates actuals from
forecasts, preserves source-report links, and protects rich DingTalk formatting.

## Files

- `SKILL.md`: operating workflow and management boundaries.
- `references/report-contract.md`: evidence, metrics, issue, and privacy rules.
- `references/dingtalk-runbook.md`: verified DingTalk read/write sequence.
- `scripts/report_contract.py`: reusable advisory source and evidence helpers.
- `scripts/validate_run.py`: mechanical validation of renderer inputs.
- `scripts/render_dingtalk_jsonml.py`: native DingTalk JSONML renderer.
- `scripts/jsonml_check.py`: structural check of rendered JSONML before any write.

Report evidence and intermediate documents belong in the current task's
artifact workspace. Keep only final valuable documents in durable storage.

Do not store exported meetings, messages, or report-run data in this repository.

## Verification

From the Skill directory:

```bash
python3 -m unittest discover -s tests -v
python3 /Users/derek/.agents/skills/skill-creator/scripts/quick_validate.py .
python3 -m scripts.validate_run --help
python3 -m scripts.render_dingtalk_jsonml --help
```

The renderer validates its required input fields and JSONML in one invocation.
Business coverage and report quality are judged by the Skill and Audit. The
former service `publishable` publication condition is retired. Submit the exact
rendered artifact with its target revision and retain the provider write receipt.
