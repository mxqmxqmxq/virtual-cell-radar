"""前端 topics.js 的 node:test 测试（无 node 时跳过）。"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_node_topics_suite():
    r = subprocess.run(
        ["node", "--test", "tests/js/*.test.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
