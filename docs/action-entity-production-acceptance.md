# Action Entity Phase 1 boundary

**Status:** complete and live (runtime commit `87a34d299`).

The first external-Action unit (Tasks 1–7) is deployed through the standard release path. The production Store marker is `2026-10-21.1`, Email schema ledger 53, and the current schema has 14 ordinary tables. Each of the seven populated source kinds retired by this unit has zero rows. This closes the defined Phase 1 boundary; specialized unsubscribe continuation, other Action kinds and the broader Task/Run/Email/business-record program remain open. No full Actions retirement or whole-database unification is claimed.

The repository updater counts current common-Action processing rows as in-flight work through the existing publication selector, so a deploy waits for them. Design and task records are under `.superpowers/sdd/2026-10-21-action-entity-unification/`.
