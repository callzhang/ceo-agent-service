# Project CRM Customer Association

**Status:** Approved for implementation by Derek, 2026-10-06

## Decision

An official Project may optionally reference a Fxiaoke CRM AccountObj. The CRM customer is not part of the Project title, and not every Project is an external customer project. Customers can be used to group Projects, but unassociated/internal Projects remain visible in the ordinary Project list.

Store the stable CRM `_id` as identity and the CRM name as a display snapshot. CRM remains the customer source of truth; the association belongs to the CEO Agent Service Project. A Task does not store a duplicated customer field: its customer display is derived from its confirmed Project relation. A standalone Task has no inferred customer.

## Lookup and confirmation

- Read CRM through the authenticated `sharecrm` CLI only; no CRM create, update, delete, follow-up, or other write.
- Use the installed `sharecrm data record query-by-name` action, restricted to `AccountObj`, to retrieve CRM name-resolution candidates for a source-backed label.
- The installed resolver does not prove exact or exhaustive uniqueness. Do not auto-link from its ranking or `RESOLVED` state. Every returned candidate, including a single result from a Task Agent source label, stays unlinked until a user explicitly confirms it in the Project UI.
- Multiple returned candidates remain unlinked until a user selects one. Resolver `NO_MATCH`, CLI/auth/query unavailable, and conflicting later evidence are distinct states.
- Confirm only a CRM ID returned in the most recent search results saved for that Project. Repeating the same confirmation is idempotent. Clearing a link is an explicit local action.
- An evidence scan cannot clear or silently replace an existing confirmed link. A conflicting match is surfaced for resolution.
- Bound the number of returned candidates. If the response is malformed, outside the target CRM object, or exceeds the bound, report lookup unavailable rather than promoting a partial result.

## Agent prompt boundary

Task Agent receives an explicit prompt instruction not to call Memory `memory_write`. This is a prompt-level instruction only; do not introduce a tool allowlist or CLI/MCP code interception for this rule.

## User-visible behavior

- Project list and detail show the linked CRM customer or the current lookup state.
- Project detail lets the user perform a read-only customer name resolution, inspect returned candidates, confirm a selected candidate, or explicitly clear a confirmed local relation.
- Customer-grouped Project view groups by stable CRM ID (not display name) and includes only confirmed links. Unassociated Projects remain in the regular Projects view.
- Task views may show the customer name derived from its confirmed Project link; they must not show a Task-specific CRM association.

## Data and safety constraints

- Existing Project rows migrate as unassociated; do not rewrite Project titles or synthesize customer links from title prefixes.
- CRM resolver output is projected to stable ID, matched display name, and a source-neutral resolution marker; provider diagnostics and unrelated CRM fields are not persisted or returned.
- No customer association is inferred solely from the Project title, Task text, customer name similarity, or an unconfirmed candidate.

## Acceptance checks

1. Migration preserves all existing Project identity/title fields and leaves customer association empty.
2. Any candidate returned from cited Task Agent evidence or manual UI resolution is saved as unconfirmed; ambiguous, no-match, unavailable, and over-bound result sets remain distinct.
3. Only a saved candidate ID can be confirmed; confirmation is idempotent and explicit clearing affects only local Project association.
4. A failed later lookup preserves a confirmed customer ID/name; a conflicting candidate does not replace it.
5. Project customer grouping uses CRM ID, excludes unassociated/internal Projects, and normal project listing still includes them.
6. Task customer display is derived through a confirmed Project; independent Tasks remain unassociated.
7. Task Agent prompt includes the Memory `memory_write` prohibition, with no code-level tool restrictions added for that prohibition.
8. Focused backend/frontend tests, build, fixed Project evaluation, production CRM read-only smoke, release, and live service readback are recorded separately; no CRM write is issued.
