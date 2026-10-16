"""Run complete canonical Tasks20 conversion regressions without app selection."""
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[3]
PYTHON='/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python'
subprocess.run([PYTHON,'-B','-m','pytest','-q','-p','no:cacheprovider',str(Path(__file__).with_name('2026-10-16-task20-release-regression.py'))],cwd=ROOT,env=os.environ|{'PYTHONDONTWRITEBYTECODE':'1'},check=True)
