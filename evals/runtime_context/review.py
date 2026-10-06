#!/usr/bin/env python3
"""Obtain an arm-blind independent semantic review of native eval outputs."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "cases.v1.json"
RUBRIC = HERE / "rubric.v1.md"
ARTIFACTS = HERE / "artifacts"
MODEL = "gpt-5.6-sol"
EFFORT = "medium"


def final_message(stdout: str) -> str:
    final = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                final = item.get("text") or ""
    return final


def review_prompt(manifest: dict, rubric: str, items: list[dict]) -> str:
    cases = {case["id"]: case for case in manifest["cases"]}
    payload = []
    for item in items:
        case = dict(cases[item["case_id"]])
        if "inherits" in case:
            case = {**cases[case.pop("inherits")], **case}
        messages = case["messages"]
        if messages[0]["content"] != case["trigger"]:
            messages = [{**messages[0], "content": case["trigger"]}, *messages[1:]]
        payload.append({"case_id": item["case_id"], "repetition": item["repetition"],
                        "original_request": case["trigger"],
                        "source_messages": messages,
                        "source_calendar_events": case["events"],
                        "calendar_error": case.get("calendar_error"),
                        "absent_tools": case.get("absent_tools", []),
                        "prior_error": case.get("prior_error"),
                        "outputs": item["outputs"]})
    return (
        "You are an independent semantic evaluator. The labels A/B are randomized for "
        "each pair. Evaluate each complete final result and its actual calls separately "
        "against the original request and fixed sources. Do not infer an arm or grade "
        "on lexical matches alone. Return a single strict JSON object with `judgments` "
        "array; each entry has case_id, repetition, label, pass (boolean), reason, "
        "and evidence (list of exact call or source facts). Include every output once. "
        "If the native run errored, fail it.\n\n"
        f"Rubric:\n{rubric}\n\nFixed cases and blinded results:\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def review_batch(manifest: dict, rubric: str, items: list[dict]) -> dict:
    with TemporaryDirectory(prefix="runtime-context-review-") as raw:
        command = ["codex", "exec", "--json", "--ignore-user-config", "--ignore-rules",
                   "--disable", "hooks", "--ephemeral", "--skip-git-repo-check",
                   "--sandbox", "read-only", "--cd", raw, "--model", MODEL,
                   "-c", f'model_reasoning_effort="{EFFORT}"',
                   "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
                   "-c", 'features.shell_tool=false', "-c", 'features.unified_exec=false',
                   "-c", 'features.apps=false', "-c", 'features.plugins=false',
                   "-c", 'features.hooks=false', "-c", 'features.multi_agent=false',
                   "-c", 'features.memories=false', "-c", 'features.code_mode_host=false',
                   "-c", 'features.code_mode_only=true',
                   "-c", 'features.code_mode.excluded_tool_namespaces=["functions"]', "-"]
        completed = subprocess.run(command, input=review_prompt(manifest, rubric, items),
                                   text=True, capture_output=True, timeout=360, check=False)
    text = final_message(completed.stdout)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    return {"case_repetitions": [[x["case_id"], x["repetition"]] for x in items],
            "exit_code": completed.returncode, "judgment": parsed,
            "error": "" if completed.returncode == 0 and parsed else
                     (text[:1000] or completed.stderr[:1000] or "missing_evaluator_result")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ARTIFACTS / "results.blinded.v1.json")
    parser.add_argument("--output", type=Path, default=ARTIFACTS / "semantic-review.v1.json")
    parser.add_argument("--batch-size", type=int, default=2)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("batch size must be positive")
    blinded = json.loads(args.input.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if blinded["manifest_sha256"] != sha256(MANIFEST.read_bytes()).hexdigest():
        raise ValueError("blinded packet and fixed manifest differ")
    rubric = RUBRIC.read_text(encoding="utf-8")
    items = blinded["items"]
    batches = []
    for index in range(0, len(items), args.batch_size):
        batch = review_batch(manifest, rubric, items[index:index + args.batch_size])
        batches.append(batch)
        print(f"Reviewed {index + 1}-{min(index + args.batch_size, len(items))}/{len(items)}: "
              f"exit={batch['exit_code']} error={batch['error'][:100] or '-'}", flush=True)
    result = {"evaluator_model": MODEL, "evaluator_effort": EFFORT,
              "input_sha256": sha256(args.input.read_bytes()).hexdigest(),
              "rubric_sha256": sha256(RUBRIC.read_bytes()).hexdigest(), "batches": batches}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if all(not batch["error"] for batch in batches) else 1


if __name__ == "__main__":
    raise SystemExit(main())
