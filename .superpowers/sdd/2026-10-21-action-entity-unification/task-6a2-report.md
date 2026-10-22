# Task 6A2: native Action lifecycle and affected recovery

Status: handed off for a narrow source-fact conversion fix. Native route readiness passed. Three native harness runs are preserved: the first two exposed contradictory test instructions; the third exposed missing synthetic group facts and failed before any provider-substitute send. The fixture clarification and focused harness are in place, but an owned inverse/reforward check exposed a load-bearing `candidate_action_attempts.external_action_key` loss. No accepted native effect or Task 6A2 completion is claimed.

## Scope and exact run inputs

- Worktree: `/Users/derek/.codex/worktrees/action-entity-plans/ceo-agent-service`, starting HEAD `9177c624d902e57007b4898dd4e0b68e211f06b8`.
- Approved native route: `codex_oauth`, `gpt-6-luna`, reasoning `high`; Codex CLI `0.163.0`. Readiness evidence: `codex --version`, `codex login status` (logged in using ChatGPT), and `codex debug models` (catalog lists `gpt-6-luna` with `high`). No fallback route was used.
- Current A1 cohort: `/tmp/action-entity-task6-corrected.IH2XQu/copy/cohort-current.sqlite3`, SHA-256 `176ad9cbfb2d835933f72a714252467cbc54209297e7f26bc4a2f2c58363538b`. The harness opens it read-only and makes owned SQLite backup copies under `/tmp/task6a2.fjw5dC`; it remains unchanged.
- Genuine pinned old-code root: `/tmp/ceo-action-entity-validation.8rUAvK/old-code-897227b`. The source DB and protected C2 copy were not opened for writing.
- Commands run so far:

```sh
python scripts/validate_native_action_entity_lifecycle.py --current-copy /tmp/action-entity-task6-corrected.IH2XQu/copy/cohort-current.sqlite3 --owned-temp-root /tmp/task6a2.fjw5dC/approve-run --old-root /tmp/ceo-action-entity-validation.8rUAvK/old-code-897227b --case approve
python scripts/validate_native_action_entity_lifecycle.py --current-copy /tmp/action-entity-task6-corrected.IH2XQu/copy/cohort-current.sqlite3 --owned-temp-root /tmp/task6a2.fjw5dC/approve2-run --old-root /tmp/ceo-action-entity-validation.8rUAvK/old-code-897227b --case approve
python scripts/validate_native_action_entity_lifecycle.py --current-copy /tmp/action-entity-task6-corrected.IH2XQu/copy/cohort-current.sqlite3 --owned-temp-root /tmp/task6a2.fjw5dC/approve3-run --old-root /tmp/ceo-action-entity-validation.8rUAvK/old-code-897227b --case approve
pytest -q tests/test_native_action_entity_lifecycle.py
python -m py_compile scripts/validate_native_action_entity_lifecycle.py tests/test_native_action_entity_lifecycle.py
git diff --check
```

Full command logs are under the owned temporary root. Native trajectories remain in `~/.codex/sessions`; none were copied into the repository or temp tree.

## Native fail-first evidence and root cause

Both initial actual native attempts completed Consumer/Audit/Consumer/Audit with exit code 0. They created a real revision-0 candidate with one `send_group_message`, then Audit rejected it and Consumer revision 1 returned an approved `no_action`. The in-memory provider therefore recorded 0 sends and 0 verifications. The tasks ended `done` through the no-action path. This was a harness-case failure, not an authentication or service failure.

The exact root cause is present in the original native input and tool/output trajectory: the user prompt requested a `send_group_message` candidate and also said not to send, access real messages, or use the network. The actual `read_system_action_contract` result defines the operation as `effect: "external"` with native send-receipt completion. Audit's saved rejection feedback identified that contradiction and requested removal of the external action; revision 1 complied and Audit approved `no_action`. The test harness had bound `SystemExecutor` to `InMemoryProvider`, but the task did not tell the model that the configured executor sink was a local fake.

Original trajectory pointers (session JSONL paths under `/Users/derek/.codex/sessions/2026/10/22/`; `start–end` are the persisted run transcript line bounds):

- Attempt 1 task `5190188010847419`: Consumer session `01a162d2-fcfa-7893-b634-c7567842e5ef`, lines `0–40`, SHA-256 `bcd0c6efd643a553c042c0a0cbedbb29a408cbd137faf2e78eb4948b0b09e769`; Audit `01a162d3-4db7-7c42-97d5-d657103475d9`, lines `0–36`, SHA-256 `d3e81d4b178919ac982de3ecd7de6ccac9961b7b5852d8210621a10c974e2a0e`; revision Consumer uses the same session at lines `0–53`; revision Audit `01a162d3-e141-7f53-8046-68d817036f1d`, lines `0–32`, SHA-256 `8d6a4ec491b55903b4e8d53e6277c96e2ca5f36bed945e1be8dbeefee996e069`.
- Attempt 2 task `5035074797820666`: Consumer session `01a162d4-ba64-7eb2-bece-928487d72121`, lines `0–44`, SHA-256 `b3dd13dfa81700578deda6c692248492dc8adcb5c7e9e93bd972ffa646eb7b0d`; Audit session `01a162d5-0287-7e40-9ef2-48d1e9e3f342`, lines `0–33`; revision Audit `01a162d5-a717-7841-a2b4-4bebd51ac3d5`, lines `0–33`, SHA-256 `b63d3b21131ea8fd858e34a144d3b310ad9b9394f597d5a239667797bfd78fdf`.

Inspection covered the complete task input, both restricted MCP calls and their results, Consumer candidate/final outputs, Audit feedback and final decisions. The artifact read returned the three test records and expected aggregate; the candidate referenced both the artifact and contract. No provider call occurred in these attempts. The model correctly followed the contradictory prompt by dropping the external effect.

Approved test-only adjustment: the acceptance prompt now requests one candidate effect and states that this test invocation's `SystemExecutor` has only a local in-memory provider substitute, with no real provider client or network path. It separately prohibits the Agent from calling direct send tools. No production prompt, System Action Contract, audit rule, or action policy changes.

The old-prompt fail-first evidence is the 0-send result above. The focused prompt regression now passes (`1 passed`); it checks for an explicit local substitute and one requested system action, while rejecting the contradictory “do not send” / “forbid sending” wording.

## Current progress

Approve3 is terminal, not accepted: task `5643350199711279` ended `failed`; Consumer revisions 0, 1 and 2 each produced candidates; Audit returned/rejected them and requested the missing synthetic group roster; Consumer revision 3 attempted an undeclared `reply_to_message` read and the restricted executor failed with `read_dingtalk_messages_in_window is not a function`. The selected registry operation for this harness is `send_group_message`. Expected provider-substitute sends/receipts/Sent rows were `1/1/1`; actual were `0/0/0`. This is a test-fixture gap, not production policy. The original full native trajectory is retained under `~/.codex/sessions` and was inspected across inputs, tool calls/results, feedback, and outputs. Pointers: Consumer session `01a162d8-f37e-7c00-83f6-50fe0f704b94`, lines `0–102`, SHA-256 `5303972fa3cfa1cfeb446eba081bdc593743bb9882cb5c9d17beba5a7ea66155`; Audit sessions `01a162d9-7049-7280-801d-3f12af50ac67`, `0–36`, SHA-256 `dd26902a45b3676fad3f8379fdde9b89a2af34523541e49cc7556b95cb9f444d`; `01a162da-1ee1-76d3-9c1f-b9065e84d5dc`, `0–40`, SHA-256 `c6794a83604a848f4dcb2d3d70757f6b179a22b8f6961f8a07bcb63b5f25fa7a`; and `01a162db-0cd2-7342-8bf0-e289d67055eb`, `0–43`, SHA-256 `f195fe1d9706697144cfe3dc1e427446fee69043c1cfd89cfdb8f1a3b9e1d836`.

Approved harness-only correction: add a complete synthetic group roster, roles, and recipient-fit facts to `case-input.json`, and state that this artifact is authoritative for the isolated synthetic group. The acceptance prompt also makes clear that only the registered `send_group_message` system action is available and the normal `SystemExecutor` uses only the local in-memory substitute. It still forbids direct send tools. No runtime behavior or policy is changed. The focused prompt/helper tests currently pass (`2 passed`).

Roundtrip helper preflight used only the failed approve3 candidate copy. It has no task Sent fact, so it cannot establish old-code readback of a native-created effect. The actual current inverse → pinned old public cold write → reforward reached same-cohort comparison and failed. The exact command was a direct helper call on a private owned copy (no native CLI):

```sh
python - <<'PY'
from pathlib import Path
from scripts.validate_native_action_entity_lifecycle import _copy, _roundtrip
case = Path('/tmp/task6a2.fjw5dC/roundtrip-preflight/fixture')
current = case / 'current.sqlite3'
_copy(Path('/tmp/task6a2.fjw5dC/approve3-run/approve/candidate.sqlite3'), current)
_roundtrip(current, case, Path('/tmp/ceo-action-entity-validation.8rUAvK/old-code-897227b'), 'preflightA2')
PY
```

Exact saved owned snapshots: `/tmp/task6a2.fjw5dC/roundtrip-preflight/fixture/after-old-cold.sqlite3` (SHA-256 `629e010cbefe11d73adb928dbac4080d212e96ff71d5de4b67bd50a0c3d42ed9`) and `/tmp/task6a2.fjw5dC/roundtrip-preflight/fixture/reforward-inverse-check.sqlite3` (SHA-256 `6567d11d084396f7b93bd9ba8138045a8ca6ee32995c658de08986b4ccf77c02`). The failing assertion was `full old-schema inverse semantic population differs from the same-cohort baseline`; the source comparator had already confirmed equal table counts, Action typed identity sets, Sent-reference identity digest, schema, FK findings and sqlite sequences. The only semantic delta was `candidate_action_attempts.external_action_key`: the source row's 64-character orphan key becomes `""` after re-forward. Source field order is `(action_index, external_action_key, status, result_json, created_at, updated_at)`; the released Action payload owner uses `external_action_key`, and the current inverse projection reads it from the Action owner. The standalone Action creation path clears that field when the key has no canonical result owner.

The one semantic row delta is an orphan `candidate_action_attempts` source fact created by approve3 before its unsupported read failed. At identity SHA-256 `b7c7470e59e2a2df1bfd0a4705488ee6fe0c5c125de15cccdfab0e00d6c03dc0`, both inverse snapshots contain the same row id `120091`, action index `0`, status `uncertain`, and unchanged result JSON hash `cabbc5867911066c46d470115a1d883661f4e94610955c164d7042921d3db43a`; the `external_action_key` changes from a 64-character value (SHA-256 `1dd0af9acf47ff2f1ec9b654c9772f9178069cae9bf9a1e766cd9ec268b47a2e`) to empty. It has no canonical `external_action_results` owner. Source cardinality on the A1 predecessor copy: 401 candidate attempts, 358 nonempty keys / 357 unique keys, 576 result keys, no orphan key, and one duplicate key group. After approve3 created the failed candidate attempt: 402 attempts, 359 nonempty / 358 unique, 576 result keys, one unique orphan key and one duplicate key group. This establishes that the regression is not an arbitrary repeated-key observation in the saved acceptance case, while broader repeated-orphan cardinality still needs product-level consideration.

The conversion path is `app/action_entity_transfer.py:_transfer_candidates`: lines 231 and 246–248 set/clear `external_action_key` based on a canonical external-result owner, otherwise the standalone Action is created without the source observation key. This drops the source fact on the next inverse. The A1 predecessor had 401 candidate attempts with 358 nonempty/357 unique keys and no orphan; the failed approve3 attempt created the one observed unique orphan (402 attempts, 359 nonempty/358 unique, one orphan row/group, max group size 1). A regression fixture can be added to `tests/test_action_entity_schema_release.py` by inserting one legitimate unique-key `candidate_action_attempts` fact with status `uncertain` and no matching `external_action_results`, publishing, capturing the Action, inverting, and asserting the exact key/status/result JSON/source identity roundtrip. Do not globally retain keys without resolving repeated orphan-key grouping and Action uniqueness constraints. I did not change runtime transfer or policy code. The corrected accepted native effect, revision/recovery cases, and named historical readback fixture remain unverified and are not claimed. Focused harness tests pass (`2 passed`); they do not cover this production conversion defect.

Still outstanding for the next owner: review and fix the orphan candidate-action key conversion with a focused regression; then run one corrected approved native effect and prove its new Action/receipt/Sent fact survives old-runtime read/write and re-forward. Revision, settlement recovery, and the named historical-readback fixture were intentionally left for the next Task 6 owner. No UI, push, or deploy was performed.
