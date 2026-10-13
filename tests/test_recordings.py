"""Publishing real runs for replay, and serving them."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import EXAMPLES, ROOT, FakeLLM, reference_fix, ultra_answer

from test_repair.agent import RepairAgent
from test_repair.config import ConfigError, ServerSettings, Settings
from test_repair.recordings import publish
from test_repair.sandbox import LocalSandbox
from test_repair.server import create_app


@pytest.fixture
def fake_run(settings: Settings, tmp_path: Path) -> Path:
    out = tmp_path / "run"
    llm = FakeLLM([ultra_answer(reference_fix("demo_invoice"))])
    RepairAgent(settings, llm, LocalSandbox(60)).run(EXAMPLES / "demo_invoice", out)
    return out


def test_runs_with_fake_models_are_never_published(fake_run: Path, tmp_path: Path):
    with pytest.raises(ConfigError, match="no real Token Factory model calls"):
        publish(fake_run, tmp_path / "recordings")
    assert not (tmp_path / "recordings").exists()


def test_publish_copies_events_and_updates_index(fake_run: Path, tmp_path: Path):
    events = fake_run / "events.jsonl"  # stand-in for a real run: rename the fake models
    events.write_text(events.read_text(encoding="utf-8").replace("fake/", "nvidia/"),
                      encoding="utf-8")
    dest = tmp_path / "recordings"
    entry = publish(fake_run, dest, title="Invoice demo")
    assert entry["status"] == "SUCCESS" and entry["title"] == "Invoice demo"
    assert (dest / entry["file"]).read_text(encoding="utf-8") == events.read_text(encoding="utf-8")
    index = json.loads((dest / "index.json").read_text(encoding="utf-8"))
    assert index["live_url"] == "" and [r["file"] for r in index["recordings"]] == [entry["file"]]
    publish(fake_run, dest)  # republishing the same run replaces its entry
    index = json.loads((dest / "index.json").read_text(encoding="utf-8"))
    assert len(index["recordings"]) == 1


def test_server_serves_recordings_safely(settings: Settings, tmp_path: Path):
    app = create_app(settings, ServerSettings(runs_dir=str(tmp_path / "runs")),
                     llm_factory=lambda _s: FakeLLM(["x"]),
                     sandbox_factory=lambda _s: LocalSandbox(60),
                     demo_dirs=[EXAMPLES], web_dir=ROOT / "web")
    client = TestClient(app)
    res = client.get("/recordings/index.json")
    assert res.status_code == 200 and "recordings" in res.json()
    for bad in ["..%2Findex.html", "index.html", "a.py", "missing.jsonl"]:
        assert client.get(f"/recordings/{bad}").status_code == 404
