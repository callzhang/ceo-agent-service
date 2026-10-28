# Installed OKR v21 release baseline

Derek explicitly authorized a separate, verbatim sync of the installed OKR v21
Skill and its release baselines so the approved half-day leave correction can
pass standard publication without reverting the existing notification contract.

The installed file and managed revision both carry SHA-256
`e786d7a199671ba62b069a402df7e38200f075233839010f343c4a90de1b58f0`.
The repository file is now byte-identical. Only this Skill's old installed,
old managed and new source digests are aligned in the publication manifest.

V21 retains management report and individual scorecard generation while making
the group message a separate company-level artifact. The group proposal excludes
personal names, individual scores/OKRs, HR evaluations and links exposing
individual materials. No new business rule is authored in this sync.

The exact installed-baseline regression failed against the prior source and
passes after the sync. At the initial sync checkpoint, the publisher file had 29 passing tests and one stale
attendance no-op assertion introduced by the separate leave-version transition;
that assertion is repaired in a separate leave test commit. Static checks pass.
Subsequent verification passes all 30 publisher tests. PR 333 is merged and standard gated publication at `9f163a678b1a58a9a91e3935066dbfe1e9064623` completed; all 26 assets match installed/source/target digests. The installed OKR v21 bytes remain unchanged.
