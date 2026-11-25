import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_workflow_token_is_scoped_and_actions_are_pinned(path):
    workflow = path.read_text()
    assert "permissions:\n  contents: read\n" in workflow
    uses = re.findall(r"uses: (\S+)@(\S+)", workflow)
    assert uses
    documented = (ROOT / "docs/development.md").read_text()
    for action, ref in uses:
        assert re.fullmatch(r"[0-9a-f]{40}", ref), action
        assert f"`{action}`" in documented and ref in documented
