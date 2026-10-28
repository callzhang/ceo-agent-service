# Search and install Skills in Settings

Status: the user approved the existing-page search/list/Install interaction on
2026-10-28. The concrete contracts below are implemented and verified in the development
branch. Standard publication is pending; company repository migration is separate.

## User outcome

After deploying the open-source service, the user opens the existing Settings →
Skills page, enters a repository URL or a public Skill name, clicks Search, and
sees matching Skills. Each row shows its name, one-sentence description, author
or maintainer, last modification time, source, and an Install button. Installing
one row installs that Skill's complete directory, excluding nested independent
Skill packages; it does not install every Skill in the repo.
There is no separate enterprise setup wizard.

## Scope and decisions

- Use the existing Settings route and managed revision/config/load receipt model.
- Repository URL searches use native Git under the service account and never go
  through the public name index. Public name searches use skills.sh. No login
  tokens, proxy credentials or new OAuth implementation belong to this feature.
- Initial repository support is HTTPS and SSH Git repository URLs. Public
  name results resolve to GitHub repositories. Tree/blob URLs,
  local paths and archive URLs are outside this first version; show a clear
  unsupported-source error rather than interpreting them as public keywords.
- A repository can contain a root SKILL.md, skills/<directory>/SKILL.md, or
  .agents/skills/<directory>/SKILL.md. Collect all supported locations; duplicate
  declared names in one repo are ambiguous and cannot be installed by name.
- Resolve the default branch to an exact commit during search. Install uses that
  commit and exact relative directory, even if the branch moves afterwards.
  Advanced ref selection and updates are a follow-up, not extra first-use fields.
- Parse name/description with the existing frontmatter parser. Show the first
  paragraph of description as one sentence visually, with full text accessible.
  Use metadata.author (or top-level author) when supplied; otherwise show the
  repository namespace as maintainer when the URL identifies one, clearly
  labelled; otherwise show 未提供. Never infer authorship from
  the Git committer. Time means the latest commit touching the Skill directory,
  displayed in the user's timezone with the source timestamp retained.
- Public index results only identify candidates; the repository provides actual
  Skill metadata. Missing attribution/time displays 未提供/未知. A malformed
  Skill is an unavailable row with a reason; it does not hide valid siblings.
- Search at most 10 public matches per submission; retain provider ordering and
  group repository reads. Hydrate at most two repositories concurrently. Native
  Git commands have a 30-second timeout and the search request has a 120-second
  total budget. A timed-out metadata lookup is explicitly unavailable, not an
  empty successful search. No background polling or scheduled refresh is added.
- New Skills appear in an installed-Skills list independently of feature groups.
  Existing feature groups, editing, activation and task selection remain usable.
- Install means package installed and revision recorded. Managed process loading
  still follows the existing next-start config and load receipt flow. Show
  已安装 · 待加载 until the matching process has loaded it; do not force a restart
  from an HTTP request or claim that installation enabled business automation.
- Same source/commit/package already installed is idempotent. A different source
  or different content colliding with an installed name reports a conflict;
  this version does not silently overwrite it or invent an update workflow.
- Install the entire directory, including scripts, references and assets, using
  the existing package staging behavior. Do not run install hooks or scripts.
  Ordinary external Skills need no ceo-agent-service ownership marker, and
  their exact SKILL.md bytes must not be rewritten to add one.
- Reuse existing name/path/ownership checks, file staging, revision binding and
  load verification. No new Audit review, authorization policy, human approval
  stage, runtime-route branch or task lifecycle is part of this feature.

## Initial installation and public/private boundary

The public service must boot with its public, generic baseline and no enterprise
repo access. Optional installed Skills are imported explicitly; a hardcoded list
of company Skill names must not be a startup prerequisite. Users install private
Skills through the same URL search and Install buttons as public Skills. Git
authentication errors identify the service-account setup requirement. Search
must remain usable before any private package has been installed.

Fresh deployment installs the existing REPOSITORY_MANAGED_SKILL_NAMES baseline
from the public release's ci/shared-skills into the normal installed directory
before first worker startup. Keep that existing generic baseline name set; do
not add company names to it. Bootstrap currently only verifies eight names and
does not install this baseline, so this is an actual missing first-use step.
The separately scoped content migration must ensure the retained public baseline
contains generic content; this installer must not copy enterprise Skill folders
merely because they are adjacent to public assets in a source directory.

The private repo is the authoring source; the local installed package and managed
revision are the runtime installation. Installing it does not export its body
into the public service checkout, CI fixtures, logs or Git history. The existing
explicit export feature is not invoked by this flow. Public tests use synthetic
repositories; private policy tests remain private. Dependencies on CLI/MCP tools
retain their existing configuration and availability rules; this installer does
not provision those tools or recursively execute prose dependency instructions.

Creating the actual private repository, moving existing company rules and tests,
sanitizing current public documentation, and handling already-public Git history
are a separate migration/release workstream. This feature must not be reported
as completing that migration. Preserve the unpushed OA optimization for that
workstream; do not publish its company rules while implementing this UI.

## Architecture and interfaces

Use a small repository reader for exact Git identity and metadata, a discovery
service for URL versus public-name lookup, and an installer that reuses local
package staging and existing Store revisions. Add a narrow route module rather
than expanding the large console registration function. A small React search
component lives inside ManagedSkillsPanel; installed rows remain visible without
selecting a business feature.

HTTP contracts:

- POST /api/console/settings/skill-search: {query: string} → {items, errors}.
- POST /api/console/settings/skill-installs: {repo_url, commit, skill_path} →
  {name, skill_id, revision_id, sha256, package_sha256, source,
  install_status: installed|already_installed, activation_status: pending_restart|active}.

Each item contains repo_url, commit, skill_path, name, description, author,
author_kind (author|maintainer|unknown), updated_at (nullable ISO timestamp),
available and unavailable_reason. The canonical source identity is
{repo_url, commit, skill_path, package_sha256}; persist it in the existing revision
source string as versioned JSON, with no new SQLite tables or schema version.
Package digest covers relative filenames and exact bytes in deterministic order.

The installer rereads the exact commit, validates the candidate and stages it
before mutation, then uses existing transactional Store and filesystem rollback
boundaries. Installation must leave no orphan installed package/revision after
a failed operation; do not weaken existing concurrency checks. The candidate
commit is never replaced with HEAD. Request errors use the existing console
envelope: 422 invalid source/Skill, 409 collision/config conflict, 502 upstream
failure, 504 timeout. Partial metadata failures are row-level errors; a failed
public index request is an upstream error, not zero matches.

## Acceptance

1. Existing Settings Skills page supports both approved input kinds and per-row Install.
2. Every valid result has the requested metadata or an honest missing-value label.
3. A synthetic private repo works using service-account Git credentials only.
4. A root Skill and a multi-Skill repo both work; installation includes support files.
5. A moved default branch does not alter what an existing result installs.
6. Repeat install is idempotent; name collisions and concurrent requests preserve data.
7. Fresh public setup starts and shows Settings with no company Skills/private access.
8. Installed/unassigned Skills are visible and selectable using existing task options.
9. Existing active revisions/frozen tasks/load receipts are preserved; pending is not active.
10. No private material is copied to the public source tree by search or installation.

## Provider reference

Public name discovery follows the skills.sh endpoint used by the provider's own
CLI: https://github.com/vercel-labs/skills/blob/main/src/find.ts
(searchSkillsAPI, verified 2026-10-28). It returns names and source identifiers,
not the complete requested display metadata. Repository hydration is necessary.
Native authentication principles are described in
https://github.com/vercel-labs/skills/blob/main/docs/commands.md . This design
uses Git directly for repository reads and does not add the skills CLI or copy
its fallback chains.
