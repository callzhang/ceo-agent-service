"""Read saved production metadata/JSONL; no model, provider, or database writes."""

import math
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics

from app.native_trajectory import find_codex_session_path

KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)


def stats(rows):
    out = {"n": len(rows)}
    for key in (
        "wall_seconds",
        "native_seconds",
        "input_tokens",
        "cached_input_tokens",
        "uncached_input_tokens",
        "output_tokens",
        "total_tokens",
        "first_input_tokens",
        "user_chars",
        "service_user_chars",
        "developer_chars",
        "tool_calls",
    ):
        values = sorted(r[key] for r in rows if r.get(key) is not None)
        out[key] = (
            {
                "n": len(values),
                "median": round(statistics.median(values), 2),
                "p90": round(
                    values[min(len(values) - 1, math.ceil(len(values) * 0.9) - 1)], 2
                ),
                "mean": round(statistics.mean(values), 2),
            }
            if values
            else None
        )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    c = sqlite3.connect(args.db.as_uri() + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    c.execute("pragma query_only=on")
    c.execute("begin")
    sql = """select a.*,r.role,r.proposal_revision,r.task_id,i.channel,case when coalesce(i.oa_url,'')<>'' then 'oa' else coalesce(i.channel,'') end task_subtype from runtime_attempts a
    left join runs r on r.id=a.parent_id left join tasks t on t.id=r.task_id
    left join task_inputs i on i.id=t.current_input_id
    where a.runtime_kind='codex_cli' and a.started_at>='2026-10-06' and a.started_at<'2026-10-27'
    and a.workload_kind in ('agent_run','meeting') order by a.id"""
    dbrows = [dict(r) for r in c.execute(sql)]
    c.close()
    cache = {}
    rows = []
    for a in dbrows:
        r = {
            k: a[k]
            for k in (
                "id",
                "parent_id",
                "task_id",
                "role",
                "channel",
                "task_subtype",
                "workload_kind",
                "model",
                "route_name",
                "credential_mode",
                "session_mode",
                "proposal_revision",
                "status",
                "started_at",
                "finished_at",
                "session_id",
                "transcript_start",
                "transcript_end",
            )
        }
        r["wall_seconds"] = (
            round((dt(a["finished_at"]) - dt(a["started_at"])).total_seconds(), 3)
            if a["finished_at"] and a["started_at"]
            else None
        )
        r["phase"] = (
            "before_schema_prune"
            if a["started_at"] < "2026-10-22 12:12:35"
            else "after_schema_prune"
        )
        sid = a["session_id"]
        r["native_available"] = False
        if sid and sid not in cache:
            p = find_codex_session_path(sid, codex_home=Path("/Users/derek/.codex"))
            cache[sid] = p
        p = cache.get(sid)
        if (
            not p
            or not a["transcript_end"]
            or a["transcript_end"] <= a["transcript_start"]
        ):
            rows.append(r)
            continue
        # Read streaming; retain metadata, never persist source/tool text.
        previous = None
        last = None
        first = None
        starttime = None
        endtime = None
        models = set()
        efforts = set()
        names = []
        user = []
        dev = []
        sha = hashlib.sha256()
        n = 0
        has_wire = False
        has_native = False
        has_ondemand = False
        seen_assistant = False
        reset = False
        with p.open() as f:
            for i, line in enumerate(f):
                if i >= a["transcript_end"]:
                    break
                v = json.loads(line)
                b = v.get("payload", {})
                typ = b.get("type")
                if i < a["transcript_start"]:
                    if typ == "token_count" and b.get("info"):
                        previous = b["info"].get("total_token_usage")
                    continue
                sha.update(line.rstrip("\n").encode())
                sha.update(b"\n")
                n += 1
                if typ == "task_started":
                    starttime = v.get("timestamp")
                if typ == "task_complete":
                    endtime = v.get("timestamp")
                if v.get("type") == "turn_context":
                    models.add(b.get("model"))
                    efforts.add(b.get("effort"))
                if v.get("type") == "response_item" and typ == "message":
                    text = "\n".join(
                        x.get("text", "")
                        for x in b.get("content", [])
                        if isinstance(x, dict)
                    )
                    if b.get("role") == "assistant":
                        seen_assistant = True
                    if not seen_assistant and b.get("role") == "user":
                        user.append(len(text))
                    if not seen_assistant and b.get("role") == "developer":
                        dev.append(len(text))
                        has_wire |= "Pydantic Wire Contract" in text
                        has_native |= "Native Output Format" in text
                        has_ondemand |= "read_consumer_output_schema" in text
                if v.get("type") == "response_item" and typ in (
                    "function_call",
                    "custom_tool_call",
                ):
                    names.append(b.get("name", ""))
                if typ == "token_count" and b.get("info"):
                    info = b["info"]
                    last = info.get("total_token_usage")
                    first = first or info.get("last_token_usage")
        r.update(
            native_available=n == a["transcript_end"] - a["transcript_start"],
            native_lines=n,
            native_sha256=sha.hexdigest(),
            native_models=sorted(x for x in models if x),
            efforts=sorted(x for x in efforts if x),
            tool_calls=len(names),
            tool_names=sorted(set(names)),
            user_chars=sum(user),
            first_user_chars=user[0] if user else None,
            service_user_chars=sum(user[1:]) if len(user) > 1 else None,
            developer_chars=sum(dev),
            has_wire=has_wire,
            has_native=has_native,
            has_ondemand=has_ondemand,
            native_seconds=round((dt(endtime) - dt(starttime)).total_seconds(), 3)
            if starttime and endtime
            else None,
        )
        if last:
            if previous and any(last.get(k, 0) < previous.get(k, 0) for k in KEYS):
                reset = True
            r["usage_counter_reset"] = reset
            if not reset:
                for k in KEYS:
                    r[k] = last.get(k, 0) - (previous or {}).get(k, 0)
                r["uncached_input_tokens"] = (
                    r["input_tokens"] - r["cached_input_tokens"]
                )
                r["first_input_tokens"] = (first or {}).get("input_tokens")
        rows.append(r)
    complete = [
        r
        for r in rows
        if r["status"] == "completed"
        and r.get("native_available")
        and r.get("total_tokens") is not None
    ]
    groups = {}
    for r in complete:
        if r["model"] != "gpt-6-luna" or r["native_models"] != ["gpt-6-luna"]:
            continue
        if not ("2026-10-21" <= r["started_at"] < "2026-10-25"):
            continue
        key = "|".join(
            [
                r["phase"],
                r["role"] or r["workload_kind"],
                r["channel"] or "",
                r["session_mode"],
                ",".join(r["efforts"]),
                r["route_name"],
            ]
        )
        groups.setdefault(key, []).append(r)
    coverage = Counter(
        (
            r["started_at"][:10],
            r["workload_kind"],
            r["model"],
            r.get("native_available", False),
        )
        for r in rows
    )
    result = {
        "extracted_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": "Production DB metadata and exact native attempt ranges; cumulative token counters minus last counter before start. Missing counters excluded,not zero. Cached input subset of input; reasoning subset of output; token_usage_record duplicate ignored. Counter reset excluded. No model calls/tests/provider operations.",
        "schema_prune_merge_utc": "2026-10-22T12:12:35Z",
        "merge_time_is_not_exact_deployment_time": True,
        "coverage": [
            {"day": k[0], "workload": k[1], "model": k[2], "available": k[3], "n": v}
            for k, v in sorted(coverage.items())
        ],
        "rows": rows,
        "groups": {k: stats(v) for k, v in groups.items()},
    }
    eligible = [
        r
        for r in complete
        if r["model"] == "gpt-6-luna"
        and r["native_models"] == ["gpt-6-luna"]
        and r["efforts"] == ["high"]
        and r["route_name"] == "codex_oauth"
        and r["session_mode"] == "fresh"
        and "2026-10-21" <= r["started_at"] < "2026-10-25"
        and r["task_subtype"] == "dingtalk"
    ]
    comparisons = {}
    for role in ("consumer", "audit"):
        before = [
            r
            for r in eligible
            if r["role"] == role and r["phase"] == "before_schema_prune"
        ]
        after = [
            r
            for r in eligible
            if r["role"] == role and r["phase"] == "after_schema_prune"
        ]
        available = after.copy()
        pairs = []
        for first in sorted(before, key=lambda r: r["started_at"]):
            options = [
                r
                for r in available
                if first["service_user_chars"]
                and r["service_user_chars"]
                and 0.8 <= r["service_user_chars"] / first["service_user_chars"] <= 1.25
            ]
            if not options:
                continue
            second = min(
                options,
                key=lambda r: (
                    abs(
                        math.log(r["service_user_chars"] / first["service_user_chars"])
                    ),
                    r["started_at"],
                ),
            )
            available.remove(second)
            pairs.append((first, second))
        comparisons[role] = {
            "matching": "Outcome-blind greedy chronological before order; nearest log user-message char distance; after/before within0.8..1.25; without replacement. All initial user messages after bootstrap,not tool reads or historical context. Task category/model/effort/route/fresh fixed; facts,toolset and load not controlled.",
            "unmatched_before": stats(before),
            "unmatched_after": stats(after),
            "pairs": [
                {
                    "before_attempt": a["id"],
                    "after_attempt": b["id"],
                    "user_char_ratio": b["service_user_chars"]
                    / a["service_user_chars"],
                }
                for a, b in pairs
            ],
            "before": stats([a for a, b in pairs]),
            "after": stats([b for a, b in pairs]),
        }
    meeting = {}
    for name, lo, hi in [
        ("before", "2026-10-15", "2026-10-18 09:11:50"),
        ("after", "2026-10-18 09:11:50", "2026-10-21 04:00:00"),
    ]:
        cohort = [
            r
            for r in rows
            if r["workload_kind"] == "meeting"
            and r["model"] == "gpt-5.6-luna"
            and r["route_name"] == "codex_oauth"
            and r["status"] == "completed"
            and lo <= r["started_at"] < hi
        ]
        meeting[name] = {
            "stats": stats(cohort),
            "attempt_ids": [r["id"] for r in cohort],
            "native_available": sum(r.get("native_available", False) for r in cohort),
        }
    result["comparisons"] = comparisons
    result["meeting_metadata_comparison"] = meeting
    result["extraction_counts"] = {
        "db_attempts": len(rows),
        "native_available": sum(r.get("native_available", False) for r in rows),
        "completed_with_usage": len(complete),
    }
    # Persist only relevant per-attempt metadata; coverage preserves excluded/missing counts.
    result["rows"] = [
        r
        for r in rows
        if (
            r["model"] == "gpt-6-luna"
            and r["role"] in ("consumer", "audit")
            and "2026-10-21" <= r["started_at"] < "2026-10-25"
            and r["channel"] == "dingtalk"
        )
        or (
            r["workload_kind"] == "meeting"
            and r["model"] == "gpt-5.6-luna"
            and "2026-10-15" <= r["started_at"] < "2026-10-21 04:00:00"
        )
        or r["started_at"] >= "2026-10-26 03:47:27"
    ]
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "db_attempts": len(rows),
                "complete_with_usage": len(complete),
                "consumer_pairs": len(comparisons["consumer"]["pairs"]),
                "audit_pairs": len(comparisons["audit"]["pairs"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
