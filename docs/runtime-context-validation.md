# Runtime Context and Settings input preview validation

Derek approved development on 2026-10-05. This change adds the approved work and participant-timezone principles to the existing Consumer/Audit developer instructions. It appends invocation-specific Runtime Context after route and role configuration, and exposes the service input through Settings → Prompts → Rendered preview. There is no separate editable Runtime Context prompt.

## Implementation boundary

The preview shows service Developer and Task input. Claude also shows the native combined input contract. CLI-generated system instructions, schemas and session history are outside this view. The actual runner persists a preparation snapshot and a separate adapter-return record in the existing event stream. Neither is proof of model receipt, task completion or external effect. Each route attempt can be selected independently.

Current previews render current role rules, profile and default Skill catalog. They reuse only a marked task-specific Skill override and saved complete role task body, with source run/time stated. Missing complete old inputs show unavailable; no historical business task is replayed. Timezone metadata includes only scalar participant_id, timezone, source_ref and applies_on; credential redaction uses the existing structured redactor.

## Focused verification

- Backend role, runner, preview, profile and Audit-rule tests: 301 passed, 4 existing skipped (before the final timezone wording and two added edge cases).
- Final preview/API and Runtime Context verification: 23 passed; the additional opaque-source regression first reproduced AttributeError and was corrected by projecting only mapping metadata.
- Frontend Settings/preview/API focused tests: 62 passed, 2 existing skipped. TypeScript/Vite build passed.
- Independent spec review and code quality review passed after three findings were resolved: source-field credential exposure, a stale default Skill catalog in current preview, and inaccessible earlier route inputs.
- Real React/API browser verification used a separate SQLite database and a non-executing test executor at localhost:8944. Consumer/Audit, Codex/Claude combined input, earlier invoked/later prepared attempts, invalid task errors and long-input rendering were checked. The existing light theme remained readable under a simulated dark preference; no new theme was introduced. At 390px, document scroll width equals viewport width. No console error appeared in the tested interaction.
- Screenshot: `/Users/derek/Documents/memory/ceo-agent-service/runtime-context-20261005/settings-current-preview.jpg`. The screenshot contains synthetic fixture identity and paths, not production task evidence.

## Fixed native evaluation

See `evals/runtime_context/REPORT.md` and the versioned artifacts for exact corpus/model/prompt hashes and blind semantic judgments. The fixture has three read-only MCP tools and no production calendar, shell, provider effects or business messages. Constructor-level role instructions and a synthetic capability description are compared, rather than replaying a historical full service invocation.

The first comparison is deliberately retained: both arms failed the known-timezone case at a DST transition. The final wording requires date-specific offset verification and UTC round-trip checking; when conversion cannot be verified it must state that limit rather than invent a definite counterpart time. The follow-up evaluates that same failure under unchanged sources and rubric. V2 known-zone results are baseline 0/3 and final candidate 1/3: one candidate converted correctly, one gave the correct clock times but the wrong London UTC offset, and one final was unparseable. This wording reduces neither timezone validation to a guaranteed result nor the small-sample score to a production success claim. Passing tests or this bounded comparison do not establish a general production-quality improvement.

## Release

PR: https://github.com/callzhang/ceo-agent-service/pull/15. Production checkout/PID, health, queues, Attention, History and live Settings readback are recorded after deployment in `/Users/derek/Documents/memory/ceo-agent-service/runtime-context-20261005/release-readback.json`. Implementation and local verification alone are not a production release claim.
