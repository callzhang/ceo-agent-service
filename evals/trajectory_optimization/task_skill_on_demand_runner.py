"""Compare full-inline and on-demand Task Skill with the same anonymous cases."""

import argparse
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import task_agent
from app.structured_agent import load_skill_text
from evals.trajectory_optimization import task_schema_titles_runner


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--arm", choices=("full", "on_demand"), required=True)
    args, runner_args = parser.parse_known_args()
    original = task_agent.build_task_agent_prompt

    def full_inline(*items, **options):
        prompt = original(*items, **options)
        if options.get("inline_skill"):
            return prompt
        start = prompt.index("CEO Work Tracking Skill (complete current protocol): ")
        end = prompt.index("\n\nThe current independent", start)
        return (
            prompt[:start]
            + load_skill_text([task_agent.WORK_TRACKING_SKILL_PATH])
            + prompt[end:]
        )

    with patch.object(sys, "argv", [sys.argv[0], *runner_args]):
        if args.arm == "full":
            with patch.object(task_agent, "build_task_agent_prompt", full_inline):
                task_schema_titles_runner.main()
        else:
            task_schema_titles_runner.main()


if __name__ == "__main__":
    main()
