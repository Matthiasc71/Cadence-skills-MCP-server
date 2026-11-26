"""The 10 mcp-builder evaluation questions stay answerable with read-only tools on the demo environment."""

from __future__ import annotations

import sys
from pathlib import Path

EVAL_DIR = Path(__file__).parents[1] / "evaluations"
sys.path.insert(0, str(EVAL_DIR))


def test_evaluation_answers(tmp_path):
    from build_demo_env import build
    from verify_answers import verify
    build(tmp_path / "demo")
    results = verify(str(tmp_path / "demo" / "config.json"), EVAL_DIR / "cadence_eval.xml")
    bad = [(q[:60], exp, got) for q, exp, got, ok, _ in results if not ok]
    assert not bad, bad
