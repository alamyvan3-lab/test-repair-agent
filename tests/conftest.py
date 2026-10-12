"""Shared fixtures: settings with fake model IDs and a local sandbox."""

from __future__ import annotations

import pytest
from helpers import FAKE_KEY

from test_repair.config import RoleModel, Settings
from test_repair.sandbox import LocalSandbox


@pytest.fixture
def settings() -> Settings:
    def role(name: str) -> RoleModel:
        return RoleModel(model=f"fake/{name}", base_url="http://127.0.0.1:9/v1/",
                         temperature=0.0, max_tokens=512, extra_body={},
                         price_in_per_m=1.0, price_out_per_m=3.0)

    # Techniques are off here so each test sees the plain loop; test_techniques.py turns
    # them on one at a time.
    return Settings(api_key=FAKE_KEY, ultra=role("ultra"), nano=role("nano"),
                    sandbox_backend="local", test_timeout_s=60, max_attempts=3,
                    candidates=1, sbfl=False, isolation_rerun=False, minimize_patch=False)


@pytest.fixture
def local_sandbox() -> LocalSandbox:
    return LocalSandbox(timeout_s=60)
