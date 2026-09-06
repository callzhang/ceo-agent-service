# Weekly OKR Settings Skill and Feature Switch Design

**Date:** 2026-09-05  
**Status:** Proposed — approved product direction, pending implementation-plan review

## Purpose

Make the management-team weekly OKR report a first-class, service-owned capability in **Settings → Skills**. The operator can turn off its automatic Sunday run without disabling the underlying DingTalk operation skill or preventing an intentional manual run.

The report remains a management-meeting artifact: it synthesizes OKR-system updates with evidence collected from the agreed channels, scores progress using the `dingtang-okr-review` standard, publishes the complete document under the `目标与执行` knowledge base, and sends a concise group summary of material progress and risks.

## Confirmed product decisions

1. Add a repository-owned Skill named `ceo-weekly-okr-report`.
2. Add a Settings feature switch with feature id `weekly_okr_report`, associated with that Skill and enabled by default.
3. Switching it **off** blocks only automatic Sunday production. It must exit before OKR collection, document creation, or group sending.
4. An explicit manual command, `weekly-okr-report --force`, remains allowed when the switch is off.
5. `dingtang-okr-review` remains the operation and evaluation dependency. It is not itself exposed as a Settings-managed feature and is not replaced by the new service-owned Skill.
6. Remove the legacy `CEO_WEEKLY_OKR_REPORT_ENABLED` environment-variable gate. The Feature Registry becomes the single source of truth for automatic-run eligibility.

## Scope and non-goals

### In scope

- Define and bundle the new service-owned Skill.
- Register the weekly-report feature in the existing Feature Registry so the current Settings UI renders its card and toggle.
- Gate the scheduled path before it starts report-source collection or any external write.
- Preserve the existing `--force` path and the current weekly report lifecycle.
- Add coverage and update operator-facing documentation.

### Out of scope

- Changing the report's scoring rubric, source selection, document template, delivery group, or publication workflow.
- Rewriting the existing scheduled maintenance loop.
- Making the new Skill's editable revisions dynamically alter the fixed weekly-runner code.
- Cancelling an already-created or in-flight report run when the switch changes. As with the other Settings feature switches, the setting governs new automatic job creation only.
- Adding audit, authorization, or other safety-policy behavior.

## Design

### Ownership model

`skills/ceo-weekly-okr-report/SKILL.md` is a repository baseline owned by `ceo-agent-service` and marked as such in its metadata. It explains the service contract and delegates evaluation-method details to the installed `dingtang-okr-review` operation skill.

The new baseline is included in the repository's bundled-business-skill catalog. This gives it the same managed import, Settings visibility, versioning, and future revision treatment as the existing service-owned business skills. The direct weekly runner remains code-owned: its operational semantics cannot silently change merely because a managed Skill revision exists.

The feature mapping is:

| Feature Registry id | Settings label | Service-owned Skill | Default |
| --- | --- | --- | --- |
| `weekly_okr_report` | 管理者 OKR 周报 | `ceo-weekly-okr-report` | On |

The existing Settings panel already renders Feature Registry entries as cards, including feature status and a switch. No separate custom UI flow or a second configuration source is needed.

### Automatic-run flow

```text
Sunday maintenance window
  -> weekly_okr_report command (force = false)
  -> FeatureRegistry.feature_enabled("weekly_okr_report")
       -> false: return status "disabled"; do not collect, publish, or send
       -> true: retain existing due-window, idempotency, collection,
                scoring, publishing, group-summary, and readback flow
```

The check belongs at the weekly report command boundary, rather than only in the maintenance loop. That keeps every non-forced automatic entry point consistent and prevents collection from beginning if another caller invokes the command.

### Manual-run flow

```text
weekly-okr-report --force
  -> bypass Feature Registry automatic-production gate
  -> retain existing report execution and delivery behavior
```

`--force` is explicit operator intent; it bypasses only the new automatic-production gate. It does not bypass existing validation, idempotency, publication, send, or readback requirements.

### Configuration migration

The checked-in Feature Registry entry defaults to enabled. Existing installations without a `weekly_okr_report` override therefore keep the present default behavior. The prior example configuration already defaults `CEO_WEEKLY_OKR_REPORT_ENABLED` to `1`; removing that variable avoids a long-lived conflict between environment configuration and Settings.

No automatic state-file migration is required. A user who wants automatic production disabled can do so in Settings; the resulting `skill-state.json` override is the sole persisted choice.

## Implementation boundaries

- Add `ceo-weekly-okr-report` to the bundled service-owned business-skill list and create its `SKILL.md` with the standard managed metadata.
- Add the `weekly_okr_report` mapping to `data/config/skill-features.json`.
- Replace the environment-variable early return in `weekly_okr_report_command` with a non-forced Feature Registry check.
- Leave `run_task_maintenance_loop`, `run_weekly_okr_report`, the report scoring rubric, and delivery code structurally unchanged.
- Do not add the external `dingtang-okr-review` Skill to the service-owned feature registry.

## Verification plan

1. **Catalog and Settings API:** verify the repository catalog imports/exposes `ceo-weekly-okr-report`; verify the feature API returns `weekly_okr_report`, its association, and its default-enabled state.
2. **Settings UI:** verify the managed-skills panel renders the new “管理者 OKR 周报” feature card and its switch using the normal feature-response path.
3. **Automatic disabled:** with `weekly_okr_report` disabled and no `--force`, assert the command returns `disabled` before report-source collection, publishing, or group sending.
4. **Manual force:** with the same feature disabled and `--force`, assert the command proceeds through the existing execution path.
5. **Regression:** retain/adjust the existing due-window, idempotency, report lifecycle, and API tests; remove coverage that asserts the retired environment-variable behavior.
6. **Live rollout:** after committing runtime changes, restart the launchd service, verify it has a new running process, check the service health/state, and confirm there is no unresolved `failed` or `processing` backlog. Confirm the Settings card is visible and that switching it off changes only the next automatic-run eligibility.

## Documentation updates

- Update the README's bundled-skill count and weekly scheduling guidance to point operators to **Settings → Skills → 管理者 OKR 周报**.
- Update the runtime/architecture documentation where scheduled weekly task production is described, clarifying that this feature switch controls automatic creation only and that `--force` remains available for intentional manual execution.

## Acceptance criteria

- Settings contains a ready, enabled-by-default “管理者 OKR 周报” card associated with `ceo-weekly-okr-report`.
- Turning that card off prevents a scheduled Sunday report from collecting data, creating a document, or sending a group message.
- `weekly-okr-report --force` continues to work while the card is off.
- The weekly report still uses the existing multi-source, rubric-based review and management-meeting document format.
- No environment-variable gate remains for this feature, and no second automatic-run configuration source exists.
