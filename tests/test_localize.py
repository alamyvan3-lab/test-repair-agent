from pathlib import Path

from helpers import BENCH

from test_repair.localize import localize
from test_repair.pytest_parser import parse_pytest_output
from test_repair.sandbox import LocalSandbox


def run_report(repo: Path, sandbox: LocalSandbox):
    res = sandbox.run_pytest(repo)
    return parse_pytest_output(res.output, res.exit_code, res.timed_out)


def test_assertion_only_failure_uses_test_imports(local_sandbox: LocalSandbox):
    repo = BENCH / "repos" / "pager_offbyone"
    suspects = localize(run_report(repo, local_sandbox), repo, top_k=4, max_chars=10_000)
    assert suspects[0].path == "pager/core.py"
    assert all(not s.path.startswith("tests/") for s in suspects)
    assert "page" in suspects[0].functions


def test_second_file_is_included(local_sandbox: LocalSandbox):
    repo = BENCH / "repos" / "checkout_second_file"
    suspects = localize(run_report(repo, local_sandbox), repo, top_k=4, max_chars=10_000)
    paths = [s.path for s in suspects]
    assert paths[0] == "shop/checkout.py"
    assert "shop/discounts.py" in paths


def test_collection_error_frames_rank_the_broken_module(local_sandbox: LocalSandbox):
    repo = BENCH / "repos" / "shapes_wrong_import"
    suspects = localize(run_report(repo, local_sandbox), repo, top_k=3, max_chars=10_000)
    assert suspects[0].path == "shapes/area.py"
    assert any("traceback frame" in r for r in suspects[0].reasons)


def test_content_is_size_capped(local_sandbox: LocalSandbox):
    repo = BENCH / "repos" / "pager_offbyone"
    suspects = localize(run_report(repo, local_sandbox), repo, top_k=1, max_chars=40)
    assert len(suspects) == 1 and len(suspects[0].content) == 40 and suspects[0].truncated


def test_skeleton_keeps_suspect_functions_and_elides_others():
    from test_repair.localize import skeleton

    source = (
        "import math\n\n\n"
        "def keep_me(x):\n    y = x + 1\n    return y\n\n\n"
        "def other(x):\n    \"\"\"Doc stays.\"\"\"\n    a = 1\n    b = 2\n    return a + b\n\n\n"
        "class K:\n    def m(self):\n        return 1\n"
    )
    out = skeleton(source, {"keep_me"})
    assert "    y = x + 1\n    return y\n" in out
    assert '    """Doc stays."""\n    ...  # 3 lines elided' in out
    assert "        ...  # 1 lines elided" in out
    assert "import math" in out and "class K:" in out
