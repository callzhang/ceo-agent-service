"""Compact evidence report from retained artifacts; never awards quality passes."""

import argparse
import json
from pathlib import Path


def summarize(report):
    output = {}
    for arm, data in report["arms"].items():
        output[arm] = {"size_summary": data["size_summary"], "roles": {}}
        for role in ("consumer", "audit"):
            rows = [row for row in data["results"] if row["role"] == role]
            usage = [value for row in rows for value in row["native"].get("usage", [])]
            input_tokens = [
                value["input_tokens"] for value in usage if "input_tokens" in value
            ]
            output_tokens = [
                value["output_tokens"] for value in usage if "output_tokens" in value
            ]
            elapsed = [row["native"]["elapsed_seconds"] for row in rows]
            output[arm]["roles"][role] = {
                "invocations": len(rows),
                "native_json_valid": sum(row["native"]["ok"] for row in rows),
                "strict_schema_valid": sum(row["normalized"]["ok"] for row in rows),
                "outcome_and_binding_screen_passed": sum(
                    row["screen"]["contract_and_binding_screen_passed"] for row in rows
                ),
                "tool_events": sum(
                    bool(row["native"].get("tool_item_types")) for row in rows
                ),
                "screen_failures": [
                    {"case_id": row["case_id"], "errors": row["screen"]["errors"]}
                    for row in rows
                    if row["screen"]["errors"]
                ],
                "provider_usage_events": len(usage),
                "provider_input_tokens_total": sum(input_tokens)
                if input_tokens
                else None,
                "provider_output_tokens_total": sum(output_tokens)
                if output_tokens
                else None,
                "elapsed_seconds_total": sum(elapsed),
                "elapsed_seconds_mean": sum(elapsed) / len(elapsed)
                if elapsed
                else None,
                "semantic_quality": "pending independent exact-output review",
            }
    return {
        "completed": report["completed"],
        "refs": report["refs"],
        "settings": report["settings"],
        "cases_sha256": report["cases_sha256"],
        "arms": output,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(json.loads(args.report.read_text()))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
