"""Sandbox behaviour. Docker tests run only when a Docker daemon and the image are available."""

import sys
from pathlib import Path

import pytest
from helpers import needs_docker

from test_repair.sandbox import DockerSandbox, LocalSandbox

PASSING = "def test_ok():\n    assert 1 + 1 == 2\n"
LOOPING = "def test_forever():\n    while True:\n        pass\n"
NETWORK = (
    "import socket\n\n"
    "def test_network_is_blocked():\n"
    "    s = socket.socket()\n"
    "    s.settimeout(3)\n"
    "    try:\n"
    "        s.connect(('1.1.1.1', 53))\n"
    "    except OSError:\n"
    "        return\n"
    "    raise AssertionError('network reachable')\n"
)
NOISY = "def test_noise():\n    print('x' * 500_000)\n    assert False\n"


def make_repo(tmp_path: Path, test_source: str) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_it.py").write_text(test_source, encoding="utf-8")
    return tmp_path


def test_local_passing_run(tmp_path: Path):
    res = LocalSandbox(timeout_s=60).run_pytest(make_repo(tmp_path, PASSING))
    assert res.exit_code == 0 and not res.timed_out
    assert "1 passed" in res.stdout


def test_local_infinite_loop_is_killed(tmp_path: Path):
    res = LocalSandbox(timeout_s=3).run_pytest(make_repo(tmp_path, LOOPING))
    assert res.timed_out
    assert res.exit_code != 0
    assert res.duration_s < 30


def test_local_output_is_capped(tmp_path: Path):
    res = LocalSandbox(timeout_s=60, max_output_bytes=4_000).run_pytest(make_repo(tmp_path, NOISY))
    assert res.truncated
    assert len(res.stdout) < 6_000
    assert "1 failed" in res.stdout  # the tail (summary) is kept


def test_local_does_not_mutate_workdir(tmp_path: Path):
    repo = make_repo(tmp_path, "def test_w():\n    open('created.txt', 'w').write('x')\n")
    LocalSandbox(timeout_s=60).run_pytest(repo)
    assert not (repo / "created.txt").exists()


def test_local_env_has_no_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TOKEN_FACTORY_API_KEY", "tf-secret-should-not-leak")
    src = "import os\n\ndef test_env():\n    assert 'TOKEN_FACTORY_API_KEY' not in os.environ\n"
    assert LocalSandbox(timeout_s=60).run_pytest(make_repo(tmp_path, src)).exit_code == 0


@pytest.mark.skipif(sys.platform != "linux", reason="network namespaces need Linux")
def test_local_network_isolated_when_unshare_works(tmp_path: Path):
    sandbox = LocalSandbox(timeout_s=60)
    if not sandbox.network_isolated:
        pytest.skip("unshare -rn not permitted here")
    assert sandbox.run_pytest(make_repo(tmp_path, NETWORK)).exit_code == 0


def test_docker_command_has_all_restrictions():
    cmd = DockerSandbox("img:1", memory="256m", cpus="0.5", pids_limit=64).command("c1")
    joined = " ".join(cmd)
    for flag in ["--network none", "--read-only", "--memory 256m", "--memory-swap 256m",
                 "--cpus 0.5", "--pids-limit 64", "--cap-drop ALL",
                 "--security-opt no-new-privileges", "--user 10001:10001", "--rm"]:
        assert flag in joined, flag
    assert "--tmpfs" in cmd and cmd[cmd.index("--tmpfs") + 1].startswith("/tmp:rw,nosuid")
    assert "-v" not in cmd and "--volume" not in cmd  # repo goes in via stdin, no bind mount


@pytest.mark.docker
@needs_docker
def test_docker_passing_run(tmp_path: Path):
    res = DockerSandbox("test-repair-sandbox:latest").run_pytest(make_repo(tmp_path, PASSING))
    assert res.exit_code == 0, res.output
    assert "1 passed" in res.stdout


@pytest.mark.docker
@needs_docker
def test_docker_network_is_blocked(tmp_path: Path):
    res = DockerSandbox("test-repair-sandbox:latest").run_pytest(make_repo(tmp_path, NETWORK))
    assert res.exit_code == 0, res.output


@pytest.mark.docker
@needs_docker
def test_docker_infinite_loop_is_killed(tmp_path: Path):
    sandbox = DockerSandbox("test-repair-sandbox:latest", timeout_s=5)
    res = sandbox.run_pytest(make_repo(tmp_path, LOOPING))
    assert res.timed_out
    assert res.duration_s < 60


@pytest.mark.docker
@needs_docker
def test_docker_root_filesystem_is_read_only(tmp_path: Path):
    src = ("import pytest\n\ndef test_ro():\n    with pytest.raises(OSError):\n"
           "        open('/usr/evil.txt', 'w')\n")
    res = DockerSandbox("test-repair-sandbox:latest").run_pytest(make_repo(tmp_path, src))
    assert res.exit_code == 0, res.output
