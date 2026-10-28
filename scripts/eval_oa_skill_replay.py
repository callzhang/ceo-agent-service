#!/usr/bin/env python3
"""Replay synthetic OA cases through the native Consumer with two Skill versions.

No provider, database, installed Skill, DingTalk call or tool is used: the
facts are inline and the sandbox is read-only. Each Git ref supplies its own
production role instructions and OA Skill text; the model, flags and facts are
the same, so a difference in the proposed action comes from the Skill change.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import eval_consumer_audit_business as business  # noqa: E402

CASES = ROOT / "evals/oa_skill_proportionality/cases.json"
SKILLS = (
    "ci/shared-skills/dingtalk-oa-approval/SKILL.md",
    "ci/shared-skills/stardust-oa-people-review/SKILL.md",
    "ci/shared-skills/stardust-oa-attendance-travel-review/SKILL.md",
)


def skills_at(ref: str | None) -> str:
    parts = []
    for path in SKILLS:
        text = (
            subprocess.run(["git", "show", f"{ref}:{path}"], cwd=ROOT, check=True,
                           capture_output=True, text=True).stdout
            if ref else (ROOT / path).read_text(encoding="utf-8")
        )
        parts.append(f"=== {path} ===\n{text}")
    return "\n\n".join(parts)


def operations(result: dict | None) -> list[str]:
    """Every typed operation (or outcome) the Consumer proposed, wherever it sits."""
    found: list[str] = []

    def walk(value) -> None:
        if isinstance(value, dict):
            for key in ("operation", "action", "outcome"):
                if isinstance(value.get(key), str):
                    found.append(value[key])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(result)
    return list(dict.fromkeys(found))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-ref", action="append", required=True)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--case", action="append")
    parser.add_argument("--with-audit", action="store_true")
    parser.add_argument("--model", help="Use the same explicit model for both variants")
    args = parser.parse_args()
    manifest = json.loads(CASES.read_text(encoding="utf-8"))
    settings = manifest["settings"]
    instructions = business.role_instructions(ROOT)
    schema = instructions["wire_schemas"]["consumer"]
    variants = {f"baseline:{ref}": skills_at(ref) for ref in args.baseline_ref}
    variants["candidate"] = skills_at(None)
    rows = []
    with tempfile.TemporaryDirectory(prefix="oa-skill-replay-") as raw:
        for case in manifest["cases"]:
            if args.case and case["id"] not in args.case:
                continue
            for name, skill_text in variants.items():
                for repeat in range(args.repeats):
                    run = business.run_role(
                        command=business.native_command(
                            model=args.model or settings["model"], effort=settings["reasoning_effort"],
                            developer_instructions=instructions["consumer"], workdir=Path(raw)),
                        prompt=business._consumer_prompt({**case, "skill_excerpt": skill_text}, schema),
                        timeout=settings["timeout_seconds_per_case"])
                    ops = operations(run.get("result"))
                    bad = [op for op in ops if op in case["expect_not"]]
                    audit = None
                    if args.with_audit and run["ok"]:
                        normalized = business.normalize_role_result(ROOT, "consumer", run["result"])
                        if normalized["ok"]:
                            audit = business.run_role(
                                command=business.native_command(
                                    model=args.model or settings["model"],
                                    effort=settings["reasoning_effort"],
                                    developer_instructions=instructions["audit"], workdir=Path(raw)),
                                prompt=business._audit_prompt(
                                    {**case, "skill_excerpt": skill_text}, normalized["result"],
                                    normalized["digest"], instructions["wire_schemas"]["audit"]),
                                timeout=settings["timeout_seconds_per_case"])
                        else:
                            audit = {"ok": False, "error": normalized["error"]}
                    rows.append({"case": case["id"], "variant": name, "repeat": repeat,
                                 "ok": run["ok"], "operations": ops, "violates": bad,
                                 "error": run.get("error", ""),
                                 "audit_ok": audit["ok"] if audit else None,
                                 "audit_outcome": (audit.get("result") or {}).get("outcome") if audit else None,
                                 "audit_summary": str((audit.get("result") or {}).get("summary", ""))[:500] if audit else None,
                                 "summary": str((run.get("result") or {}).get("summary", ""))[:300]})
                    print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    print(json.dumps({"rows": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
