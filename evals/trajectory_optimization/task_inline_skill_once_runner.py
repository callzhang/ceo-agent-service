"""Paired Task full-inline Skill comparison using the existing anonymous runner."""
import argparse
from dataclasses import replace
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.agent_runtime_router import CodexCommandFactory
from evals.trajectory_optimization import task_schema_titles_runner


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--arm", choices=("baseline", "candidate"), required=True)
    args, runner_args = parser.parse_known_args()
    original = CodexCommandFactory.standard

    def selected_factory(**options):
        factory = original(**options)
        return replace(factory, inlined_skill_names=()) if args.arm == "baseline" else factory

    with patch.object(CodexCommandFactory, "standard", selected_factory), patch.object(sys, "argv", [sys.argv[0], *runner_args]):
        task_schema_titles_runner.main()


if __name__ == "__main__":
    main()
