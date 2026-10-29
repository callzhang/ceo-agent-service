# Dingteam OKR score-page transport

## Problem and acceptance

Four saved Consumer turns on 2026-10-21, across two distinct objective IDs,
failed `read_dingtang_okr_score_detail` with the same generic
`[unparseable structured diagnostic omitted]`. The score actions were not
proposed or executed. The selected issue is the score-page read transport,
not the separate Consumer/Audit action-scope incident.

Acceptance for this change:

1. The existing page login, exact period/objective/current-batch checks,
   evaluator check, one `makeScore` submission, and post-write readback remain.
2. The two previously failing objective IDs return matching period/objective
   details in read-only probes. Other page API requests keep their headers.
3. A provider object error exposes a bounded numeric code and message through
   the existing credential redactor; `traceId` is not returned. Ordinary
   JavaScript `Error` text remains unchanged.
4. Focused tests and independent review pass. Release uses the normal deploy
   path and verifies production commit, PID, health, queues, Attention and
   History. A read-only production detail probe confirms the loaded code.
   No live score write is manufactured for acceptance.

## Complete-trajectory and provider evidence

The four saved turns called the same read tool; three used one objective and
the fourth another. Directly invoking the read-only page operation reproduced
an exception whose original text was `[object Object]`. The page's
`String(error)` discarded the provider object's fields, then the existing
credential-safe formatter replaced the malformed bracketed text with the
generic diagnostic. An in-memory read-only inspection of the object found
`code` (number), `msg` (string) and `traceId` (string). Its bounded code/message
were `7` and “目标不存在或已被删除”. No trace ID or auth material was copied.

Both failures occurred at `getScoreDetail`, after the period, current batch
and KR-scoring configuration reads succeeded. For the first objective, the
read-only rating archive contained the same objective ID. The cached-token
direct API returned its score detail; page and direct requests had the same
principal, authorization, app/suite/release headers, endpoint, method,
objective and batch. The decisive difference was `X-Space-Id`: the page sent
`1`, while the successful direct API sent an empty value. Repeating the direct
read with only that header changed to `1` returned provider code 7. Changing
only the page's read-only `getScoreDetail` request header to empty returned
matching detail. This is a paired provider observation, not an inference from
the error message alone.

The implementation routes only `getScoreDetail` and `makeScore` requests from
the score page and sets their `X-Space-Id` header to empty. The write endpoint
uses the same score-page API family; its header handling is covered by the
isolated route test, not a live write. Other headers and other requests are
unchanged. The score operation's existing target checks and post-write
readback continue to decide completion.

## Verification before release

The new provider-object regression failed before the code change because
the result was `[object Object]`; the route regression failed because no score
endpoint route was registered. After the change, the affected score-action
file passed **35 tests**. Compile and `git diff --check` passed. Two
read-only live probes of the previously failing objectives then returned the
unique current batch and exact matching period/objective detail, with two
and three KR rows respectively. Neither probe called `makeScore`.

Production deploy and post-release readback remain separate evidence gates.
