import re
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_ci_token_is_read_only_and_actions_are_pinned():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "permissions:\n  contents: read\n" in workflow
    uses = re.findall(r"uses: (\S+)@(\S+)", workflow)
    assert uses
    documented = (ROOT / "docs/development.md").read_text()
    for action, ref in uses:
        assert re.fullmatch(r"[0-9a-f]{40}", ref), action
        assert f"`{action}`" in documented and ref in documented
