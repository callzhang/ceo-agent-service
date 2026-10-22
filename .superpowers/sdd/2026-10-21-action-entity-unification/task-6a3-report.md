# Task 6A3: native revision and settlement recovery

Status: both approved native branches completed genuine Consumer/Audit turns and passed their same-cohort current inverse / pinned-old-code read and cold prepare / re-forward proofs. Settlement recovery produced one acknowledged substitute send and completed from persisted facts after interruption. The first revision run ended `failed` after four authentic Audit returns and zero effects; one controlled same-fixture rerun corrected the candidate and completed one approved effect. This does not claim perfect model reliability or overall Task 6 completion. Historical readback and UI remain separate follow-ups.

## Scope and runtime

Started from reviewed A2 HEAD `2bcb70f5b481247bff63071cd2a635036150921a`. Native calls used `codex_oauth`, `gpt-6-luna`, reasoning effort `high`, CLI `0.163.0`, and the actual `~/.codex` session home. The only outbound path was the normal `SystemExecutor` connected to `InMemoryProvider`; no real provider client or network send was available. No model output was fabricated, and no production policy, action gate, runtime prompt, or retry behavior was changed.

The reviewed current cohort was `/tmp/action-entity-task6-corrected.IH2XQu/copy/cohort-current.sqlite3`, SHA-256 `176ad9cbfb2d835933f72a714252467cbc54209297e7f26bc4a2f2c58363538b`. The protected source and C2 copy were not written. All new candidate databases and roundtrip files are under `/tmp/action-entity-task6a3.VOOfhl`; preserve these acceptance copies through the review/UI handoff.

## First native revision case: authentic failure

Command:

```sh
python scripts/validate_native_action_entity_lifecycle.py \
  --current-copy /tmp/action-entity-task6-corrected.IH2XQu/copy/cohort-current.sqlite3 \
  --owned-temp-root /tmp/action-entity-task6a3.VOOfhl/owned-revision \
  --old-root /private/tmp/ceo-action-entity-validation.8rUAvK/old-code-897227b \
  --case revision
```

Task `5983543444405867` ended `failed`. The first Consumer read the initial two-record artifact and proposed 2 / 50. The harness restored the final three-record artifact (23, 27, 50; total 100) after that actual Consumer turn. Audit revision 0 returned the stale 2 / 50 facts. The next actual Consumer corrected the count and total. Audit revision 1 returned unsupported claims about an earlier read; after the Consumer removed those claims, Audit revision 2 returned a real output-contract mismatch: `proposal.actions[0].payload.content` had `(via agent)` while `purpose_output.payload.actions[0].payload.content` did not. The next candidate retained that mismatch; Audit returned again and the bounded worker ended failed at revision 3.

Recorded result: 9 run records; 4 Audit returns; 0 substitute sends, 0 provider receipt rows, 0 Sent rows. Source hash stayed `176ad9cbfb2d835933f72a714252467cbc54209297e7f26bc4a2f2c58363538b`. Input artifact SHA-256 was `d76f2ba09ee8252feeb5f330af5f732dc98dea00524a07f8f39c0626828ed212` after restoration. This is a genuine downstream revision failure, not a native startup or authentication failure.

Original trajectory files remain in `~/.codex/sessions`; no transcript copies were made. Consumer session `01a16310-3f8f-7513-81cc-6ac8d67d7919`, file `2026/10/22/rollout-2026-10-22T03-02-52-01a16310-3f8f-7513-81cc-6ac8d67d7919.jsonl`, lines `0–91`, SHA-256 `5b34ba04d9252913c903e62cf195eb9fd8b67d91f527c48da95cb47019f37030`. Audit sessions: `01a16310-9cf1-74f3-bc61-32b1c2b45315`, lines `0–49`, SHA-256 `dbc0ab8f2c2154ecd34697b676c1c87d3d9a8f4f6802ba66c1bc7e23157c05cc`; `01a16311-5dcb-7d51-912c-ece42f378796`, lines `0–34`, SHA-256 `dbd7537c62fd9878d4341d17c20501fca9bc5e97773bf3c8dac260298630af16`; `01a16311-f0d0-7320-a5cf-ba6f776b4c4b`, lines `0–34`, SHA-256 `2ebf644ba53c840b74b1e25c7b3f7170d47e0acc270444522a3453433d07aff9`; and `01a16312-7979-7e72-9b5b-68c1d806a091`, lines `0–34`, SHA-256 `0e7a49178d5be8ab6a7273a74e100a68e70b9b9e993a581d5a84bfa51c6df67`.

## Settlement recovery case: native completion and roundtrip passed

Task `5672673071538147` had exactly two completed native runs: Consumer `391692` (session `01a16313-510d-7da0-b3b6-6c070cc8108c`, lines `0–32`, SHA-256 `f251a59a2d3543093ca48f1007581a79b398e185e94b16065b8b5a13a0ad5551`) and Audit `391694` (session `01a16313-a03a-7043-872a-e2f15a2c24e2`, lines `0–32`, SHA-256 `14cd9b51835834750851f425a60faeddce81baecc17668276b9a8e8f4c3a0129`). The original trajectories are under `~/.codex/sessions/2026/10/22/` with those session IDs.

The in-memory provider acknowledged one send. The existing settlement-interruption trigger aborted task finalization after the receipt and Sent fact had committed. The worker reopened the same database and completed `done` using the persisted acknowledgment. Final facts: one substitute send, one verification, one provider receipt, one Sent row, and no additional Consumer/Audit run. The candidate database SHA-256 after refoward was `612eb9aa0c6f9ae4d12cb1036e05213fb9b0099459d959ba0466d462c7d02816`; the protected input remained unchanged.

The full same-cohort proof ran directly on that saved candidate, with no extra native calls. Current inverse, pinned old-code public Sent read, old public `prepare_outbound_postfix`, and re-forward all passed. Old `app/store.py` resolved to `/private/tmp/ceo-action-entity-validation.8rUAvK/old-code-897227b/app/store.py`, SHA-256 `0d426abbfe0042247cdbc40f41d559f294b1f7299436724602f0e38155268d4c`. The old public read returned the exact native Sent body (SHA-256 `1d167928528786ff23eb9586fb369efa8d84a7a13bf2a4c81c4a655001d59326`), provider receipt (SHA-256 `f53665ccfa55b8e27ba0d49038f0dbd805bafa7390dd2fc68623a0615b8da63b`), and Audit run `391694`. The independent cold-prepared body SHA-256 was `c6bcebfa217331e4626834a64b402cc0981822a6b32473ee7aa5177cd173e00e`.

The strict comparator matched 518,458 stored rows, 102,815 typed Action identities, exact schema and sequences, and the same one inherited FK finding. It allowed 29,147 physical ID remaps only across the six declared retired types (`candidate_action_attempts` 44, `outbound_postfixes` 1,241, `sent_replies` 2,509, `email_actions` 11,387, `email_action_attempts` 13,478, and `sent_reply_observers` 488); uncovered remaps were zero. All 488 Sent references matched typed identity. Current fact-family projections were exact except the one intended cold-prepared postfix added by the pinned old public call.

## Test-harness receipt assertion correction

The first recovery invocation reached `done` with the correct one-send/one-receipt/one-Sent state but stopped in a test-only postcondition: it compared the stored Action receipt envelope to the verifier's input object. The existing handler stores both `{provider_result, verification}`. The harness now records the verifier result, compares the exact complete envelope, and asserts recovery completed with exactly two native runs and one acknowledged effect. Focused coverage: `python -m pytest -q tests/test_native_action_entity_lifecycle.py` — 11 passed. The corrected assertion was then exercised against the actual recovery copy before its successful roundtrip.

## Controlled revision rerun: native lifecycle passed

One controlled rerun used the same harness, fixture shape, model, route, and native configuration, without prompt tuning. Task `5076715376367384` completed `done` with Consumer revision 0 / Audit `return`, Consumer revision 1 / Audit `approve`, one substitute send, one verification, one provider receipt, and one Sent row. The first actual Audit return identified the stale 2 / 50 facts after reading the final three-record / 100 artifact. Consumer revision 1 corrected the facts, and Audit approved it. The Sent row links to the approving Audit run `391698`, selected from the unique approved candidate review (not by assuming the task had only one Audit run). Output body SHA-256 `02850290395b37a6bab9660cfbf69a6ba438fe3af369b9e9bcffc6eccf584b64`; input artifact SHA-256 `54c714006d7fcd3b6491a7331612a906b065c8c1b4df815cf1c567dfa9957a43`. The starting reviewed cohort remained unchanged.

Original native trajectories remain under `~/.codex/sessions/2026/10/22/`; no transcript copies were made. Consumer session `01a16318-38e7-7f61-8a29-1f15b7ddee79`, lines `0–65`, SHA-256 `6723c1431a4555dfb618f5fbf85469dd283bd9d12879256fd41684f89e23d482`. Initial Audit session `01a16318-b295-7d73-b0b7-22fe0890c9b7`, lines `0–36`, SHA-256 `18b2cf4ffb68c906dbfaf91a903024e2b4126fc6c3f436d687221f8ccf0d3952`. Approving Audit session `01a16319-6047-7082-a42f-18a9da50c2cf`, lines `0–32`, SHA-256 `a332769c5cc7ce2d857d2118c39b3a79b561cf0df6991e31eb4545a5c47bacc6`.

The two actual revision runs therefore demonstrate both a feedback-cycle failure caused by successive unsupported/mismatched candidate fields and a successful controlled native correction with one effect. The failed case remains recorded above; no claim of perfect model reliability is made. The successful revision copy is `/tmp/action-entity-task6a3.VOOfhl/owned-revision-rerun/revision/candidate.sqlite3`.

The positive revision roundtrip passed current inverse, pinned old-code public Sent read and cold prepare, and re-forward. The re-forwarded candidate database SHA-256 is `73515ca8c30230a0bb67149aa9773a4584ac418caaa0050d9eee7960bd6091d0`; the protected input remained unchanged. Its public old-code read returned the actual native Sent body SHA-256 `02850290395b37a6bab9660cfbf69a6ba438fe3af369b9e9bcffc6eccf584b64`, provider receipt SHA-256 `efaf3ca21cda0f0e1d9854852ead10e1670abb1f9f471ea2f82241460357c9a2`, and approving Audit run `391698`. The separate cold-prepared body SHA-256 was `0896f32daa70a56be81b3bec2a7aee27eee0fd78735b707444626d38ef008c0c`.

This cohort matched 518,465 stored rows and 102,818 typed Action identities, exact schema and sequences, and the same single inherited FK finding. It allowed 29,148 physical ID remaps only in the six declared retired types (`candidate_action_attempts` 44, `outbound_postfixes` 1,242, `sent_replies` 2,509, `email_actions` 11,387, `email_action_attempts` 13,478, and `sent_reply_observers` 488); uncovered remaps were zero. All 488 Sent references matched typed identity. Every current fact family matched by typed business identity and values, except the one intended old-cold postfix addition.

## Remaining gates

The A3 native branch matrix and roundtrip evidence are complete; the named historical completed-run/no-candidate fixture in `tests/test_dingtalk_delivery_repair.py` remains for the separately scoped A4 continuation. UI acceptance and full Task 6 review/release remain separate gates. No push or deploy was performed.
