from pathlib import Path

from test_repair.cli import _save_markdown, format_event, main
from test_repair.events import Event


def test_format_event_shows_role_model_tokens_latency():
    e = Event(seq=3, ts=105.0, type="diagnosis", message="off by one in page()", attempt=1,
              role="ultra", model="nvidia/x", prompt_tokens=900, completion_tokens=120,
              latency_s=4.25)
    line = format_event(e, t0=100.0)
    assert line.startswith("[   5.0s] #1 diagnose")
    assert "[ultra nvidia/x | 900->120 tok | 4.2s]" in line


def test_tests_run_label_and_diff_rendering():
    red = Event(seq=1, ts=1.0, type="tests_run", message="Tests: 1 failed",
                data={"all_passed": False})
    assert "tests FAIL" in format_event(red, 0.0)
    applied = Event(seq=2, ts=1.0, type="patch_applied", message="Applied",
                    data={"diff": "--- a/x\n+++ b/x\n"})
    assert "\n    --- a/x\n    +++ b/x" in format_event(applied, 0.0)


def test_save_markdown(tmp_path: Path):
    out = tmp_path / "docs" / "example_run.md"
    _save_markdown(out, ["run", "examples/demo_invoice"], ["[0.0s] start"],
                   "# Test-Repair report: demo\n\n- **Status:** SUCCESS\n")
    text = out.read_text(encoding="utf-8")
    assert "$ test-repair run examples/demo_invoice" in text
    assert "## Report: demo" in text and "[0.0s] start" in text


def test_missing_config_exits_with_message(monkeypatch, capsys, tmp_path: Path):
    monkeypatch.chdir(tmp_path)  # no .env here
    for var in ("TOKEN_FACTORY_API_KEY", "MODEL_ULTRA", "MODEL_NANO"):
        monkeypatch.delenv(var, raising=False)
    assert main(["run", str(tmp_path)]) == 2
    assert "Missing TOKEN_FACTORY_API_KEY" in capsys.readouterr().err


def test_doctor_reports_problems_without_leaking_the_key(settings, tmp_path: Path):
    import dataclasses

    from helpers import FAKE_KEY

    from test_repair.cli import doctor_checks

    checks = doctor_checks(dataclasses.replace(settings, sandbox_backend="local"),
                           env_file=tmp_path / ".env")
    status = {name: ok for name, ok, _ in checks}
    assert status["Python >= 3.11"] and status["sandbox"]
    assert not status[".env file"]
    assert status["TOKEN_FACTORY_API_KEY"]  # fixture key is set...
    assert not status["ultra model listed"]  # ...but 127.0.0.1:9 is unreachable
    assert all(FAKE_KEY not in detail for _, _, detail in checks)
