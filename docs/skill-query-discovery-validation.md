# Targeted installed Skill discovery

## Selected trajectory issue and acceptance

On 2026-10-21, 26 saved native sessions called `read_skill`; 19 sessions made a
no-source catalog call and 24 such calls appeared in total. Twenty completed
catalog responses were about 116,073 characters each. Every session with a
catalog call subsequently read at least one named Skill. In one complete
Consumer trajectory, the task had selected message-triage and chat Skills, but
the source also needed an OKR Skill; the Agent read the entire 369-entry
installed catalog before reading `dingtang-okr-review` by name. The directory
result alone does not establish that the business decision was correct.

This round addresses the discovery transfer, not task classification or a
global prompt rewrite. Acceptance requires:

1. A topic query returns only matching installed metadata, with every matching
   name, full description and exact source path preserved. It returns no Skill
   body to the Agent. Multiple query terms may appear in any order. An empty query must not
   silently return the entire catalog.
2. The existing no-source full catalog, exact name/path reads, duplicate-name
   ambiguity, source authorization and all role boundaries remain unchanged.
   Task input offers the topic query as the discovery entry when another
   Skill is needed. The service does not infer task type from message text.
3. Fixed anonymous OKR, document and duplicate-name cases find the intended
   Skill and then retrieve its complete body. The query route must make the
   directory response materially smaller than the full catalog, without
   changing the chosen result or introducing business actions.
4. Focused fail-first tests, native or service-level fixed-case comparison,
   independent review, normal merge/deploy and production readback pass.
   After release, actual trajectories determine whether Agents use the new
   entry. No reduction is claimed for a turn that still chooses the full list.

The current 369-entry local catalog is 114,046 characters in the existing TSV
representation. A case-insensitive `okr` metadata match yields 4 entries and
1,969 characters, including `dingtang-okr-review`; this is a local sizing
baseline, not model-use evidence.

## Verification

The focused regression first failed because `read_skill(query=...)` was not a
tool argument. After the first implementation, an anonymous native check
found a real semantic gap: the Agent searched for `OKR score review`, while
the target description used those words in a different order. The single
substring matcher returned no rows, and the Agent did not read a Skill. A
second regression failed on that phrase; matching all whitespace-separated
terms across name and description repaired it. This is a query behavior
correction found by the trajectory, not a hard-coded OKR special case.

Focused Agent CLI, discovery transport, Skill and prompt tests pass: 111
tests. The synthetic service fixture checks OKR, Chinese document and
duplicate-name discovery, complete body read by exact name/path, empty result,
blank query rejection and mutually exclusive arguments. Ruff, compile and
diff checks pass.

Paired anonymous native Codex OAuth controls use the same model
(`gpt-6-luna`, low effort), 369 fictional Skill entries, read-only MCP tool,
source and expected target marker. Both arms found `dingtang-okr-review`,
read its full synthetic body and returned the exact marker. The full-list arm
called `read_skill()` for 369 entries and received 118,489 characters before
its named read. The query arm called `read_skill(query="OKR score review")`
for one entry and received 361 characters before the same named read. No
business MCP, real Skill metadata, Work Profile, provider write or external
action was included. These controls establish the new entry can be used
correctly and reduces the tested directory response; they do not prove
that production Agents will always choose it.

Independent review found that the metadata scanner reads installed Skill files
to extract their frontmatter. The contract and tests cover what the tool returns
to the Agent, not whether the service reads those files internally. The docs and
test name were corrected, and the same 111 focused tests passed again. Merge,
deployment and live usage readback remain open.
