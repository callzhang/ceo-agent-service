# Compact installed Skill discovery

## Selected trajectory and completion condition

Two saved OKR Consumer turns requested `agent_cli.read_skill()` without an
argument, received a large installed-Skill directory, then separately read
`dingtang-okr-review`. On this host the original body-free directory measured
114,046 characters across 369 authorized entries. The task input had already
named the relevant Skill. This change concerns only the no-argument discovery
response; it does not alter task selection, prompts, role tools or Skill bodies.

Completion requires a shorter response that still lists every authorized name
and exact source path, including duplicate names, while topic queries return
full descriptions and exact name/path reads return the same body. The fixed
transport cases cover quoted/multiline descriptions, two roots, duplicate
names, a topic query, and an exact body read. They do not claim to measure a
model's future choice of Skill.

## Verification

The three focused discovery tests failed before the change because the
no-argument response still included descriptions. After the change,
`tests/test_agent_cli.py`, `tests/test_skill_discovery_transport.py` and
`tests/test_business_skills.py` passed (83 tests). The current authorized
directory now measures 20,999 characters; `query="OKR"` returns 1,969
characters of matching full metadata, and an exact read of
`dingtang-okr-review` returns its unchanged complete body. `git diff --check`
and Python compilation passed.

## Production readback

PR #206 merged as `c444162e1d585087b113a2f71e883e43b2cfac17`.
The standard `python -m app.deploy` completed and moved production from
`17da0fe46520d738c724b09bfd3d0a72c8c88e74` to that merge commit.
The supervisor PID changed from 64002 to 72101. `/healthz` returned HTTP 200
with `ok=true`; queue, Attention and History APIs returned HTTP 200. The
dashboard showed one active and 19 queued items, Attention had 13 existing
items and History reported 36,868 entries at readback. Production's
`app/agent_cli.py` and `app/business_skills.py` SHA-256 hashes matched the
deployed development source. This proves the release and observable service
health, not a new natural Agent Skill-selection outcome. The GitHub test job
was still queued at readback; the 83 focused local tests above are the
completed behavior verification.
