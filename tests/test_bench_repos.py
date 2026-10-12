"""Every benchmark/example repo fails as shipped and passes with its reference fix."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import BENCH, EXAMPLES, needs_docker, reference_fix

from test_repair.patcher import is_test_file
from test_repair.pytest_parser import parse_pytest_output
from test_repair.sandbox import DockerSandbox, LocalSandbox

REPOS = sorted(p for p in (BENCH / "repos").iterdir() if p.is_dir())
EXAMPLE_REPOS = [EXAMPLES / "demo_invoice", EXAMPLES / "injection_demo"]
REQUIRED_CATEGORIES = {
    "off-by-one", "wrong comparison operator", "wrong import/name", "mishandled empty input",
    "mutable default argument", "integer vs float division", "incorrect string slicing",
    "wrong dictionary key handling", "missed None check", "incorrect sort key",
    "bug spanning two functions", "fix requires reading a second file",
}


def fixed_copy(repo: Path, tmp_path: Path) -> Path:
    work = tmp_path / repo.name
    shutil.copytree(repo, work)
    patch = tmp_path / "fix.patch"
    patch.write_text(reference_fix(repo.name), encoding="utf-8", newline="\n")
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "-c", "core.autocrlf=false", "apply", str(patch)], cwd=work, check=True)
    return work


def test_manifest_covers_required_categories():
    manifest = json.loads((BENCH / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest) == {r.name for r in REPOS}
    assert 12 <= len(REPOS) <= 15
    assert REQUIRED_CATEGORIES <= {m["category"] for m in manifest.values()}


@pytest.mark.parametrize("repo", REPOS + EXAMPLE_REPOS, ids=lambda p: p.name)
def test_fails_before_and_passes_after_reference_fix(repo: Path, tmp_path: Path):
    sandbox = LocalSandbox(timeout_s=60)
    before = sandbox.run_pytest(repo)
    report = parse_pytest_output(before.output, before.exit_code, before.timed_out)
    assert not report.all_passed and report.bad >= 1, before.output

    after = sandbox.run_pytest(fixed_copy(repo, tmp_path))
    assert after.exit_code == 0, after.output

    fix = reference_fix(repo.name)
    touched = [line[6:] for line in fix.splitlines() if line.startswith("+++ b/")]
    assert touched and not any(is_test_file(p) for p in touched)


@pytest.mark.docker
@needs_docker
@pytest.mark.parametrize("repo", REPOS, ids=lambda p: p.name)
def test_reference_fix_in_docker(repo: Path, tmp_path: Path):
    sandbox = DockerSandbox("test-repair-sandbox:latest")
    assert sandbox.run_pytest(repo).exit_code != 0
    assert sandbox.run_pytest(fixed_copy(repo, tmp_path)).exit_code == 0
