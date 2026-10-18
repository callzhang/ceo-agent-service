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
Skill-contract, and email task adapter suites passed **148** tests. After
aligning the repository Skill copy byte-for-byte with the installed source,
the two directly affected suites passed **30** tests. The fixed anonymous
native case in
`evals/trajectory_optimization/email_classifier_candidate_index_native.v1.json`
used that exact installed Skill SHA-256 `5ba8cb23…`, a 21,491-character input,
and one opaque unsubscribe candidate. Both read-only Codex OAuth runs returned
valid `junk` results with candidate index 0 and no URL field; service binding
recovered the exact candidate in each run. There were zero tool calls.

## Release binding

The installed `~/.agents/skills/ceo-email-classifier/SKILL.md` is the managed
Skill source of truth. Normal code deploy does not publish the `ci/shared-skills`
copy. On 2026-10-18 the installed file and this branch's copy both hash to
`5ba8cb23…`, but the enabled scheduled classifier task 10 still references
managed revision 10 (`816012c2…`). Startup imports revision 182 (`5ba8cb23…`),
then reconciles the enabled task's old exact revision and loads the old one.
Production therefore cannot pass until the scheduled task reference is updated.

For a bounded release, pause only scheduled task 10 through its versioned API,
update its single Skill reference to revision 182 while preserving every other
task field, deploy the merged code through `python -m app.deploy`, verify the
worker load receipt and active binding use `5ba8cb23…`, then re-enable task 10
through the versioned API. Confirm the installed file hash, task reference,
process, health, queue and Attention/History after re-enabling. If any step
fails, keep the task paused and report the exact gap rather than run mismatched
classifier code and Skill. This changes only the classifier task's binding;
other scheduled task settings are not part of this release.

## Evidence boundary

This validates the service-side binding path, repository Skill contract, and
one anonymous native classifier case. The native case calls Codex directly; it
does not prove the full routed worker path or a production result. It does not
measure classification quality across diverse mail. The production checkout
remains unchanged until the normal release path deploys a reviewed commit.
Post-release readback must confirm the new commit, loaded managed Skill digest,
updated classifier task reference, healthy worker and queue state; a later real
candidate-bearing trajectory is needed to claim that production completed the
end-to-end binding path.
