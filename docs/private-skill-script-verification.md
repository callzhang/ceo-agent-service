# Reviewed private Skill scripts

The Consumer script tool uses `ci/reviewed-skill-scripts/<name>.json` as its
reviewed entrypoint and complete script-tree digest catalog. The implementation
may live only in an installed private Skill. Before execution, the existing
complete-tree algorithm checks installed scripts and non-test Python dependencies
against that fixed digest, rejects links and checks the selected Python path.
The catalog contains no business source code. Installing a repository does not
register new executable entrypoints; changed scripts require a separately reviewed
catalog update. Public and private packages use the same check on every route.

The original three report identities and their three independent private package
identities have equal implementation digests. Current installed files are preserved.
This separately authorized source-location adjustment removes the redundant public
source copy check; it retains task binding, reviewed entrypoints, dependency digest,
path containment, output limits and all Consumer/Audit contracts.
