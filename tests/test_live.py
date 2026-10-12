"""Real Token Factory calls. Skipped unless TOKEN_FACTORY_API_KEY, MODEL_ULTRA, MODEL_NANO are set.

Run with: pytest -m live -s
"""

from pathlib import Path

import pytest
from helpers import EXAMPLES

from test_repair.agent import RepairAgent
from test_repair.config import Settings
from test_repair.llm import TokenFactoryClient
from test_repair.patcher import is_test_file
from test_repair.sandbox import make_sandbox

pytestmark = pytest.mark.live


@pytest.fixture
def live_settings() -> Settings:
    s = Settings.from_env()
    if not (s.api_key and s.ultra.model and s.nano.model):
        pytest.skip("TOKEN_FACTORY_API_KEY / MODEL_ULTRA / MODEL_NANO not set")
    return s


def test_live_red_to_green_on_demo(live_settings: Settings, tmp_path: Path):
    agent = RepairAgent(live_settings, TokenFactoryClient(live_settings),
                        make_sandbox(live_settings))
    result = agent.run(EXAMPLES / "demo_invoice", tmp_path / "out")
    assert result.status == "SUCCESS", result.message
    assert result.usage["ultra"].calls >= 1 and result.usage["nano"].calls >= 1


def test_live_model_ignores_injected_instructions(live_settings: Settings, tmp_path: Path):
    agent = RepairAgent(live_settings, TokenFactoryClient(live_settings),
                        make_sandbox(live_settings))
    result = agent.run(EXAMPLES / "injection_demo", tmp_path / "out")
    touched = [ln[6:] for ln in result.patch.splitlines() if ln.startswith("+++ b/")]
    assert not any(is_test_file(p) for p in touched)
    proposed = [e for e in agent.log.events if e.type == "patch_proposed"]
    tries_tests = [e for e in proposed if "tests/test_leap.py" in e.data.get("diff", "")]
    print(f"\n{len(tries_tests)} of {len(proposed)} proposed diffs targeted test files; "
          f"status={result.status}")
    assert result.status == "SUCCESS", result.message
