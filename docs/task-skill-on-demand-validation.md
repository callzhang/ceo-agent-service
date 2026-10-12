# Task Skill on-demand delivery validation

Task previously included the entire current work-tracking Skill in every service prompt. Local native CLIs can read that file; Friday's turn API cannot read the local path. The factory now carries the exact selected path. Local Task input includes its brief purpose and full-read entry; Friday's existing message bridge includes that exact file once. The existing source-file preflight remains. No routing, tool permission, result validation, session binding or audit policy changed.

## Regression evidence

The new custom-root regression failed before the change because the full marker body remained in the local prompt. It passes after the change and checks exact factory path, preserved current facts, body-free local Claude input, and full exact remote content once. Existing schema-delivery tests still require one complete output schema. Source-root, historical snapshot exclusion, scheduled scope, input immutability, business guidance and business application tests remain covered. Shared-rule assertions now check the selected Skill plus the Task-specific input, while native read evidence is checked separately.

307 Task/schema/router tests and 85 business-Skill/adapter tests passed locally (392 total). Ruff and diff checks passed. No GitHub Actions test was required for this evidence; no full live-service suite was run.

## Native comparison

Cases: `evals/trajectory_optimization/task_skill_on_demand.cases.v1.json`. Results and input/event/decision hashes: `evals/trajectory_optimization/task_skill_on_demand.results.v1.json`. Raw evidence: `/private/tmp/ceo-task-on-demand-el39rf6f`. Native Codex OAuth, gpt-5.6-luna, medium reasoning, same model/config/facts/clocks/context and full Task result schema. Baseline is the parent Task prompt at 5bcfac89; both use the same explicit native Skill catalog. No real business service or Memory backend is connected.

| Case | Parent service characters | Candidate service characters | Applied result |
| --- | ---: | ---: | --- |
| Normal Project cold start | 32,096 | 8,898 | Both: one Project, Chen Ming owner, Zhao Hua payment responsibility, zero Tasks/Attention |
| Payment-risk same-session continuation | 35,600 | 12,402 | Both: reuse one Project, one display-only Task candidate, one Attention |

Cold sessions: baseline `01a12f8d-4e6f-7e60-ba1b-8ddd299aa239`, candidate `01a12f8d-9484-7bc3-ac9a-cdfa9d9b7270`. Risk turns resumed their respective valid cold sessions. Four outputs passed full existing model/business validation and were actually applied to isolated databases; project/task/Attention counts were read back.

Candidate cold performed a successful native `cat` of the exact current Skill. Its completed output contains the full 23,452-character stripped body, including the end of the file. Candidate risk read the full current file again. Baseline also read the beginning of the file, but already had the full body in its service input. Do not infer incomplete baseline instructions from that extra partial read.

Risk judgments differ: baseline proposed `push` and “确认客户验收材料并落实回款日期”; candidate proposed `watch` and “补齐客户验收材料并确认回款日期排期”. Both suggested the existing overall delivery owner Chen Ming with original evidence. This sample confirms valid source-based application, not identical judgments or superior business accuracy. The candidate completed one `memory_fixture.memory_write`, and the fixture recorded its data, original source timestamp and provenance. Its result explicitly says `fixture:true`; this is not production durable-Memory evidence. Baseline made no MCP call in these runs.

Claude and Friday have regression/code-path evidence only; this step has no actual provider runs for them. On-demand loading reduces the service submission by 23,198 characters in these cases, but the Skill body subsequently enters native tool results. Do not present the reduction as equal total-token savings or zero Skill context. Settings must distinguish the submitted entry from native tool output.

Independent read-only review found no loss in source selection, rules, routes or session plumbing and confirmed full native Skill reading, same-session continuations and the above risk-category difference. This step remains a draft and is not deployed; broader route/business acceptance remains necessary before release.
