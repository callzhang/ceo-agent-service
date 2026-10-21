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

Production deployment and readback are separate evidence gates.
