#!/usr/bin/env python3
"""Fixed tool-free Skill comparison; replies require human semantic review."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.eval_consumer_audit_business import native_command, run_role  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--candidate-ref", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest_path = ROOT / "evals/material_review_followup.v1.json"
    manifest = json.loads(manifest_path.read_text())
    settings = manifest["settings"]
    report = {"manifest_sha256": sha256(manifest_path.read_bytes()).hexdigest(),
              "settings": settings, "results": []}
    with TemporaryDirectory(prefix="material-review-eval-") as temporary:
        for arm, ref in [("baseline", args.baseline_ref), ("candidate", args.candidate_ref)]:
            commit = subprocess.check_output(["git", "rev-parse", ref + "^{commit}"], cwd=ROOT, text=True).strip()
            instructions = "\n\n".join(subprocess.check_output(
                ["git", "show", commit + ":ci/shared-skills/" + name + "/SKILL.md"],
                cwd=ROOT, text=True) for name in ["ceo-mail-review", "ceo-document-review"])
            command = native_command(model=settings["model"], effort=settings["reasoning_effort"],
                                     developer_instructions=instructions, workdir=Path(temporary))
            for case in manifest["cases"]:
                prompt = ("这是合成的只读材料复核练习，不得发送消息或调用外部工具。仅依据下列已提供的原始事实和适用Skill拟中文回复。"
                          "返回JSON对象，字段为reply（拟发给请求人的回复）、source_ids（实际支持该结论的来源ID数组）、"
                          "limitations（未验证部分）。不需要执行审批。\n"
                          + json.dumps({"request": case["request"], "facts": case["facts"]}, ensure_ascii=False))
                start = time.monotonic()
                result = run_role(command=command, prompt=prompt, timeout=settings["timeout_seconds"])
                report["results"].append({"arm": arm, "commit": commit, "case": case["id"],
                    "instruction_sha256": sha256(instructions.encode()).hexdigest(),
                    "seconds": round(time.monotonic() - start, 2), **result})
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
                print(arm, case["id"], "valid" if result["ok"] else "failed", flush=True)
                if not result["ok"]:
                    raise SystemExit(1)


if __name__ == "__main__":
    main()
