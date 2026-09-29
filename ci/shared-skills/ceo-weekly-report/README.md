# CEO Weekly Report Skill

This Skill reconciles one Beijing-calendar week of DingTalk evidence into a CEO
management report. It keeps unresolved issues traceable, separates actuals from
forecasts, preserves source-report links, and protects rich DingTalk formatting.

## Files

- `SKILL.md`: operating workflow and management boundaries.
- `references/report-contract.md`: evidence, metrics, issue, and privacy rules.
- `references/dingtalk-runbook.md`: verified DingTalk read/write sequence.
- `scripts/report_contract.py`: deterministic source and evidence checks.
- `scripts/validate_run.py`: complete publication gate.
- `scripts/render_dingtalk_jsonml.py`: native DingTalk JSONML renderer.
- `scripts/jsonml_check.py`: structural check of rendered JSONML before any write.

Report evidence and generated documents belong under:

```text
/Users/derek/Documents/memory/02_管理与组织/CEO周报运行/<target-date>/
```

Do not store exported meetings, messages, or report-run data in this repository.

## Verification

From the Skill directory:

```bash
python3 -m unittest discover -s tests -v
python3 /Users/derek/.agents/skills/skill-creator/scripts/quick_validate.py .
python3 -m scripts.validate_run --help
python3 -m scripts.render_dingtalk_jsonml --help
```

Before publication, run the validator against `manifest.json`, `report.json`,
and `previous-issues.json`. Publish only when it exits successfully with
`publishable: true`, then verify the complete DingTalk readback.
