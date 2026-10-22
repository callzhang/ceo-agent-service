# Consumer native-schema prompt validation

## Scope

Complete Consumer trajectories showed a 13,089-character Pydantic wire schema in Developer input for native Codex and Claude routes that already submit the matching output schema through their native CLI. This change removes only that duplicate text for those two Consumer routes; the assembled Developer prompt falls by 13,116 characters after its section separators are included. Friday keeps the Pydantic schema text because it has no matching native output-schema channel. Audit prompt assembly is unchanged.

All Consumer routes keep the same shared role boundary, including the prohibition on directly sending external messages, email, or executing approvals. The native schema, parser, Pydantic/business validation, task facts, route selection, session binding, Audit review, and System effect handling remain unchanged. The runtime prompt snapshot and current Settings preview use the selected route's exact Developer sections; historical previews continue to read the saved input.

## Fixed checks

- Route-section test asserts Codex and Claude omit `Pydantic Wire Contract`, Friday retains it, and every route retains the shared outbound restriction and typed-result semantics.
- Consumer execution test asserts Claude still receives native `--json-schema` while the saved service Developer input omits duplicated wire-schema text and retains the shared outbound restriction.
- Preview test compares Codex/Claude Consumer and Claude Audit output; Consumer previews follow route-specific assembly and Audit retains its existing contract.
- Existing parser, native schema projection, runner recovery-identity, Consumer, and preview tests cover unchanged structured output and route/session behavior.

## Verification status

The route-section, Claude native-schema execution/snapshot, Codex cold-start compatibility, Settings preview, and AgentTurnProcess suites passed: 55 tests. The broader Consumer, Settings preview, and AgentTurnProcess run passed 226 tests with 4 skipped and 3 known baseline cases deselected (two stale `lease_owner` projection fixtures and one stale capability-length threshold). Native Consumer output-schema tests passed 11/11. An initial broad run also exposed two old assertions that expected duplicated wire text; they were updated to check the new route-specific input, and those paths passed. Ruff, `py_compile`, and `git diff --check` passed.

Anonymous Codex OAuth cold-start and same-session continuation passed on the fixed fictional S-18 case using the service-equivalent `gpt-6-luna` selection and current general native output schema. Both results passed the existing native decode and Consumer Pydantic parser as `no_action`; both turns reused one session and the recorded range had zero tool calls. The model-visible Developer text omitted the duplicated Pydantic schema and retained the shared outbound rule. No production data or external actions were used.
