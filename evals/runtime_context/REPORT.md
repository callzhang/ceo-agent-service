# Runtime Context synthetic evaluation

The original native comparison is recorded in [`REPORT.v1.md`](REPORT.v1.md): the candidate passed 8/10 and the baseline 7/10 on the original meeting-window request; both failed the one known-counterpart-timezone test because of an incorrect daylight-saving conversion. Four bounded negative scenes passed in both arms. The result did not establish a general improvement.

After a general date-specific timezone verification rule was added, the independent [`REPORT.v2.md`](REPORT.v2.md) follow-up ran three new pairs for each positive scene on the same fixed corpus, tools, model and rubric. The revised candidate passed 1/3 known-zone attempts; one further attempt still carried an incorrect London UTC offset and one lacked valid final JSON. It passed 3/3 on the original unknown-counterpart scene, while the baseline passed 2/3. **Timezone accuracy remains an open model behavior gap in this bounded read-only setup.**

Both reports compare constructor-level Consumer instructions and a synthetic three-read-tool Runtime Context. They do not replay the production final prompt assembler, historical task effects, or any real provider state. The versioned artifacts hold exact final results, synthetic tool calls, prompt hashes, and arm-blind semantic judgments.
