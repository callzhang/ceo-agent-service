#!/usr/bin/env python3
"""Read live roster/source and verify an unscored report without production writes."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.dws_client import DwsClient  # noqa: E402
from app.okr_review import DwsLiveOkrSource  # noqa: E402
from app.weekly_okr_report import DwsWeeklyOkrGateway, run_weekly_okr_report  # noqa: E402


class MemoryOnlyState:
    def __init__(self):
        self.state = {}

    def get_service_state(self, key):
        return self.state.get(key, "")

    def set_service_state(self, key, value):
        self.state[key] = value


class ReadOnlyGateway(DwsWeeklyOkrGateway):
    def resolve_wiki(self, *args, **kwargs):
        raise AssertionError("verification cannot enter publication")

    def ensure_folder(self, *args, **kwargs):
        raise AssertionError("verification cannot create a folder")

    def ensure_document(self, *args, **kwargs):
        raise AssertionError("verification cannot create a document")

    def publish_document(self, *args, **kwargs):
        raise AssertionError("verification cannot publish a document")

    def send_group_summary(self, *args, **kwargs):
        raise AssertionError("verification cannot send a message")


class NoScoring:
    def analyze(self, **kwargs):
        raise RuntimeError("live scoring inputs exist; coverage-only verifier cannot fabricate model analysis")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    dws = DwsClient()
    source = DwsLiveOkrSource(dws=dws, command_template=[
        sys.executable, str(ROOT / "scripts/dingteam_okr_headless_source.py"),
        "--user-id", "{user_id}", "--period-label", "{period_label}",
    ])
    result = run_weekly_okr_report(
        store=MemoryOnlyState(), gateway=ReadOnlyGateway(dws), source=source,
        agent=NoScoring(), workspace=args.workspace.resolve(), now=datetime.now(UTC),
        force=True, deliver=False,
    )
    print(json.dumps({**asdict(result), "external_writes": 0, "production_db_writes": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
