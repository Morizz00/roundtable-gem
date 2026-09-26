"""The demo task: fix a deliberately broken script, judged by a hidden spec suite.

demo/broken_script/  the files the Coder gets (inventory_report.py, SPEC.md, sample_stock.csv)
demo/hidden_tests/   the verification suite only the Critic gets
The bug has an obvious surface symptom (a crash on the sample) and subtler requirements in SPEC.md, so a first
attempt can fail for real, not by script.
"""
from __future__ import annotations

from pathlib import Path

from app.config import ROOT
from app.orchestrator import TaskSpec

DEMO_DIR = ROOT / "demo"
TASK = "inventory_report.py crashes when run on sample_stock.csv. Fix it so it runs on that file and behaves as described in SPEC.md."
CATEGORY = "coding_python"  # Sage's canonical routing label


def load_demo_task(demo_dir: Path = DEMO_DIR) -> TaskSpec:
    broken, hidden = demo_dir / "broken_script", demo_dir / "hidden_tests"
    workspace = {p.name: p.read_text(encoding="utf-8") for p in sorted(broken.iterdir()) if p.is_file() and p.name != "README.md"}
    hidden_tests = {p.name: p.read_text(encoding="utf-8") for p in sorted(hidden.glob("test_*.py"))}
    return TaskSpec(name="inventory_report", task=TASK, category=CATEGORY, workspace=workspace, hidden_tests=hidden_tests)
