import json
import subprocess
from pathlib import Path

import pytest
from helpers import BENCH, FakeLLM, reference_fix, ultra_answer

from test_repair.agent import RoleUsage
from test_repair.benchmark import (
    MARKER,
    README_END,
    README_START,
    LocalRunner,
    NebiusJobsRunner,
    Task,
    TrialResult,
    aggregate,
    load_tasks,
    render_markdown,
    update_readme,
)
from test_repair.config import ConfigError, Settings
from test_repair.sandbox import LocalSandbox


def trial(repo: str, mode: str, status: str, attempts: int, ultra=(1000, 200, 4.0),
          nano=(300, 50, 0.6), n=0) -> TrialResult:
    return TrialResult(
        repo=repo, category="cat", mode=mode, trial=n, status=status, attempts=attempts,
        wall_time_s=10.0, test_runs=attempts + 1,
        usage={"ultra": RoleUsage(model="u", calls=2, prompt_tokens=ultra[0],
                                  completion_tokens=ultra[1], latency_s=ultra[2]),
               "nano": RoleUsage(model="n", calls=3, prompt_tokens=nano[0],
                                 completion_tokens=nano[1], latency_s=nano[2])},
    )


META = {"finished_at": "2026-10-12T00:00:00+00:00", "runner": "local", "sandbox": "docker",
        "trials": 1, "seed": 0, "commit": "abc", "models": {"ultra": "u", "nano": "n"},
        "max_attempts": 5, "max_corrections": 2}


def test_aggregate_numbers(settings: Settings):
    results = [trial("a", "agent", "SUCCESS", 1), trial("b", "agent", "SUCCESS", 3),
               trial("c", "agent", "EXHAUSTED", 5), trial("a", "single_shot", "SUCCESS", 1)]
    agg = aggregate(results, settings)
    agent = agg["agent"]
    assert agent["runs"] == 3 and agent["successes"] == 2
    assert agent["pass_rate"] == pytest.approx(0.6667)
    assert agent["mean_attempts_to_success"] == 2.0
    assert agent["roles"]["ultra"]["prompt_tokens"] == 3000
    assert agent["roles"]["ultra"]["mean_latency_per_call_s"] == 2.0  # 12s / 6 calls
    # prices in the fixture: $1 in, $3 out per 1M tokens
    assert agent["roles"]["ultra"]["cost_usd"] == pytest.approx((3000 + 600 * 3) / 1e6)
    assert agg["single_shot"]["pass_rate"] == 1.0


def test_markdown_and_readme_block(settings: Settings, tmp_path: Path):
    results = [trial("a", "agent", "SUCCESS", 1), trial("b", "agent", "EXHAUSTED", 5)]
    results[1].message = "Tests still failing after 5 attempt(s)."
    agg = aggregate(results, settings)
    md = render_markdown(META, agg, results)
    assert "| agent | 2 | 1/2 (50.0%) |" in md
    assert "`b` (agent, trial 0): EXHAUSTED" in md

    readme = tmp_path / "README.md"
    readme.write_text(f"intro\n{README_START}\nold\n{README_END}\nrest\n", encoding="utf-8")
    update_readme(readme, META, agg)
    text = readme.read_text(encoding="utf-8")
    assert "old" not in text and "| agent | 2 | 1/2 (50.0%) |" in text
    assert text.startswith("intro\n") and text.endswith("rest\n")


def test_load_tasks_reads_manifest():
    tasks = load_tasks(BENCH, ["pager_offbyone"])
    assert [t.category for t in tasks] == ["off-by-one"]
    with pytest.raises(ConfigError):
        load_tasks(BENCH, ["nope"])


def test_local_runner_end_to_end(settings: Settings, tmp_path: Path, capsys):
    task = load_tasks(BENCH, ["grades_comparison"])[0]
    runner = LocalRunner(
        settings, tmp_path,
        llm_factory=lambda _s: FakeLLM([ultra_answer(reference_fix(task.name))]),
        sandbox_factory=lambda _s: LocalSandbox(60), emit_marker=True,
    )
    results = runner.run([task], ["agent", "single_shot"], trials=1)
    assert [(r.mode, r.status) for r in results] == [("agent", "SUCCESS"), ("single_shot", "SUCCESS")]
    parsed = NebiusJobsRunner.parse_logs(capsys.readouterr().out)
    assert [r.mode for r in parsed] == ["agent", "single_shot"]


class FakeNebius:
    """Records CLI calls and answers like the documented `nebius ai job` commands."""

    def __init__(self, logs: str) -> None:
        self.calls: list[list[str]] = []
        self.logs = logs
        self.polls = 0

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        verb = cmd[3]
        if verb == "create":
            out = json.dumps({"id": "op-1", "resource_id": "job-123"})
        elif verb == "get":
            self.polls += 1
            out = json.dumps({"status": {"state": "RUNNING" if self.polls < 2 else "COMPLETED"}})
        else:  # logs
            out = self.logs
        return subprocess.CompletedProcess(cmd, 0, out, "")


def test_nebius_jobs_runner_uses_documented_cli(settings: Settings):
    result = trial("pager_offbyone", "agent", "SUCCESS", 1)
    fake = FakeNebius("noise\n" + MARKER + result.model_dump_json() + "\n")
    runner = NebiusJobsRunner(settings, image="cr.example/test-repair:1",
                              api_key_secret="tf-key-secret", execute=fake, sleep=lambda _s: None)
    task = Task("pager_offbyone", BENCH / "repos" / "pager_offbyone", "off-by-one", "")
    results = runner.run([task], ["agent"], trials=1)
    assert [r.status for r in results] == ["SUCCESS"]

    create = fake.calls[0]
    assert create[:4] == ["nebius", "ai", "job", "create"]
    for flag in ["--image", "--platform", "--preset", "--container-command", "--args",
                 "--env-secret", "--async", "--format"]:
        assert flag in create
    assert "TOKEN_FACTORY_API_KEY=tf-key-secret" in create
    assert not any(settings.api_key in part for part in create)  # literal key never sent
    assert "SANDBOX_BACKEND=local" in create
    assert ["nebius", "ai", "job", "get", "--id", "job-123", "--format", "json"] in fake.calls
    assert ["nebius", "ai", "job", "logs", "job-123"] in fake.calls


def test_nebius_jobs_runner_requires_secret_selector(settings: Settings):
    with pytest.raises(ConfigError, match="MysteryBox"):
        NebiusJobsRunner(settings, image="img", api_key_secret="")
