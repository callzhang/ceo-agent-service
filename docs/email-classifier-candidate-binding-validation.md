# Email classifier candidate binding validation

## Observed failure

The read-only production trajectory recorded 13 Codex OAuth attempts for one
email classification task. Attempts 1–12 returned `runtime_result_invalid`;
attempt 13 completed. All 13 used the same 14,711-character User input and no
tool calls. In each failed result, the model selected candidate index 0 but
returned a URL whose digest did not match the exact candidate supplied for
that invocation. The successful result kept the same URL-bearing shape but
returned the matching URL. Both Developer prompt variants (36,157 and 39,112
characters) saw the same failure pattern. The later production attempt 372271
had no unsubscribe candidates and returned the old seven-field contract, so
it is not a candidate-selection acceptance run.

The evidence points to the wire contract: the model is asked to reproduce a
long URL even though the service only needs to know which already supplied
candidate was selected. It does not support adding category instructions,
changing routing, or altering retries.

## Scoped change

`AgentClassificationWireResult` contains the classification fields and an
optional candidate index, but no URL. After parsing, the service checks the
index against the candidate list from that exact invocation, binds the
corresponding original URL, then runs the existing category, eligibility,
index and persistence validation. Durable storage continues to hold only the
existing redacted digest/reference. The repository-managed Email Classifier
Skill now describes the same index-only contract. Category policy, provider
actions, routing, and retry behavior are unchanged.

## Local verification

The focused classifier, Skill-contract, and managed-Skill tests passed **70**
cases. The email task adapter suite passed **118** cases, and the three
selected worker cases covering exact candidate execution, no-candidate
handling, and selected-candidate resolution passed. Ruff, `py_compile`, and
`git diff --check` also passed. The tests cover strict rejection of a
model-supplied URL, out-of-range indices, non-`junk` selection, exact
invocation binding, the routed backend parser boundary, redacted persistence,
and alignment between the managed Skill example and the wire model. They do
not call an email provider or perform unsubscribe actions.

After merging current `origin/main` into this branch, the focused classifier,
Skill-contract, and email task adapter suites passed **148** tests. The fixed
anonymous native case in
`evals/trajectory_optimization/email_classifier_candidate_index_native.v1.json`
used this branch's exact managed Skill SHA-256 `3506daa2…`, a 21,412-character
input, and one opaque unsubscribe candidate. Both read-only Codex OAuth runs
returned valid `junk` results with candidate index 0 and no URL field; service
binding recovered the exact candidate in each run. There were zero tool calls.

## Evidence boundary

This validates the service-side binding path, repository Skill contract, and
one anonymous native classifier case. The native case calls Codex directly; it
does not prove the full routed worker path or a production result. It does not
measure classification quality across diverse mail. The production checkout
remains unchanged until the normal release path deploys a reviewed commit.
Post-release readback must confirm the new commit, loaded managed Skill digest,
healthy worker and queue state; a later real candidate-bearing trajectory is
needed to claim that production completed the end-to-end binding path.
