import shutil
import subprocess

import pytest


@pytest.mark.parametrize(
    "mode",
    [
        "complete",
        "resume",
        "timeout",
        "failed",
        "expired",
        "review_failure",
        "review_success",
        "completed_restore",
    ],
)
def test_browser_draft_and_optional_review_flows(mode):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for browser-script flow checks")
    result = subprocess.run(
        [node, "tests/studio_flow.cjs", mode], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stdout + result.stderr
