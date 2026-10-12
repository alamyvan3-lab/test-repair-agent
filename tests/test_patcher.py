import subprocess
from pathlib import Path

import pytest

from test_repair import patcher
from test_repair.patcher import PatchError, is_config_file, is_test_file

SOURCE = """\
def add(a, b):
    return a - b


def mul(a, b):
    return a * b
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "calc").mkdir()
    (tmp_path / "calc" / "ops.py").write_text(SOURCE, encoding="utf-8", newline="\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ops.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def answer(diff: str) -> str:
    return f"DIAGNOSIS: add subtracts.\n\n```diff\n{diff}```\n"


GOOD = """\
--- a/calc/ops.py
+++ b/calc/ops.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""


def test_good_diff_applies(repo: Path):
    prepared = patcher.prepare(answer(GOOD), repo, max_changed_lines=60)
    assert prepared.files == ["calc/ops.py"] and prepared.changed_lines == 2
    patcher.apply(prepared, repo)
    assert "return a + b" in (repo / "calc" / "ops.py").read_text(encoding="utf-8")


def test_wrong_line_numbers_and_counts_are_fixed(repo: Path):
    wrong = GOOD.replace("@@ -1,2 +1,2 @@", "@@ -40,7 +40,9 @@")
    prepared = patcher.prepare(answer(wrong), repo, 60)
    assert "@@ -1,5 +1,5 @@" in prepared.diff  # regenerated with real position and context
    patcher.apply(prepared, repo)
    assert "a + b" in (repo / "calc" / "ops.py").read_text(encoding="utf-8")


def test_trailing_whitespace_in_context_is_tolerated(repo: Path):
    fuzzy = GOOD.replace(" def add(a, b):\n", " def add(a, b):   \n")
    patcher.apply(patcher.prepare(answer(fuzzy), repo, 60), repo)
    assert "a + b" in (repo / "calc" / "ops.py").read_text(encoding="utf-8")


def test_blank_context_line_without_leading_space(repo: Path):
    diff = """\
--- a/calc/ops.py
+++ b/calc/ops.py
@@ -2,4 +2,4 @@
     return a - b


-def mul(a, b):
+def mul(a, b):  # product
"""
    patcher.apply(patcher.prepare(answer(diff), repo, 60), repo)
    assert "# product" in (repo / "calc" / "ops.py").read_text(encoding="utf-8")


def test_context_mismatch_gives_precise_error(repo: Path):
    bad = GOOD.replace("-    return a - b", "-    return a - b - 0")
    with pytest.raises(PatchError, match=r"Hunk 1 for calc/ops.py does not match.*a - b - 0"):
        patcher.prepare(answer(bad), repo, 60)


def test_repeated_file_header_merges_hunks(repo: Path):
    second = """\
--- a/calc/ops.py
+++ b/calc/ops.py
@@ -5,2 +5,2 @@
 def mul(a, b):
-    return a * b
+    return b * a
"""
    patcher.apply(patcher.prepare(answer(GOOD + second), repo, 60), repo)
    text = (repo / "calc" / "ops.py").read_text(encoding="utf-8")
    assert "return a + b" in text and "return b * a" in text


def test_new_file_is_allowed(repo: Path):
    diff = "--- /dev/null\n+++ b/calc/extra.py\n@@ -0,0 +1,2 @@\n+def neg(a):\n+    return -a\n"
    patcher.apply(patcher.prepare(answer(diff), repo, 60), repo)
    assert (repo / "calc" / "extra.py").read_text(encoding="utf-8") == "def neg(a):\n    return -a\n"


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("no fence here", "No ```diff fenced block"),
        (answer(GOOD) + answer(GOOD), "Found 2 ```diff blocks"),
        (answer("--- a/calc/ops.py\n+++ b/calc/ops.py\n@@ bad @@\n"), "Malformed hunk header"),
        (answer("--- a/calc/ops.py\n+++ b/calc/ops.py\n@@ -1 +1 @@\n*junk\n"), "Malformed line"),
        (answer("@@ -1 +1 @@\n-a\n+b\n"), "before any"),
        (answer("just words\n"), "Unexpected line"),
        (answer("--- a/calc/ops.py\n+++ b/calc/ops.py\n@@ -1,1 +1,1 @@\n def add(a, b):\n"),
         "no '\\+' or '-' lines"),
    ],
)
def test_malformed_diffs_are_rejected(repo: Path, text: str, match: str):
    with pytest.raises(PatchError, match=match):
        patcher.prepare(text, repo, 60)


@pytest.mark.parametrize(
    ("path", "match"),
    [
        ("../outside.py", "contains '..'"),
        ("calc/../../outside.py", "contains '..'"),
        ("/etc/passwd", "must be relative"),
        ("C:/Windows/win.ini", "must be relative"),
        ("calc\\ops.py", "forward slashes"),
        (".git/config", "inside .git"),
    ],
)
def test_path_traversal_and_absolute_paths_are_rejected(repo: Path, path: str, match: str):
    diff = f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-x\n+y\n".replace("a//", "/").replace("b//", "/")
    with pytest.raises(PatchError, match=match):
        patcher.prepare(answer(diff), repo, 60)


@pytest.mark.parametrize(
    "path",
    ["tests/test_ops.py", "calc/test_ops.py", "calc/ops_test.py", "conftest.py", "test/x.py"],
)
def test_test_files_are_protected(repo: Path, path: str):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1\n", encoding="utf-8")
    diff = f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    with pytest.raises(PatchError, match="Editing test files is not allowed"):
        patcher.prepare(answer(diff), repo, 60)


@pytest.mark.parametrize("path", ["pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"])
def test_config_files_are_protected(repo: Path, path: str):
    (repo / path).write_text("x = 1\n", encoding="utf-8")
    diff = f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    with pytest.raises(PatchError, match="configuration files"):
        patcher.prepare(answer(diff), repo, 60)


def test_deleting_tests_is_rejected(repo: Path):
    diff = "--- a/tests/test_ops.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-def test_x():\n-    pass\n"
    with pytest.raises(PatchError, match="Deleting files is not allowed"):
        patcher.prepare(answer(diff), repo, 60)


def test_oversized_diff_is_rejected(repo: Path):
    added = "".join(f"+X{i} = {i}\n" for i in range(61))
    diff = f"--- a/calc/ops.py\n+++ b/calc/ops.py\n@@ -1,1 +1,62 @@\n def add(a, b):\n{added}"
    with pytest.raises(PatchError, match="changes 61 lines; the limit is 60"):
        patcher.prepare(answer(diff), repo, 60)


def test_binary_patch_is_rejected(repo: Path):
    diff = "--- a/calc/ops.py\n+++ b/calc/ops.py\nGIT binary patch\nliteral 3\n"
    with pytest.raises(PatchError, match="Binary"):
        patcher.prepare(answer(diff), repo, 60)


def test_rename_is_rejected(repo: Path):
    diff = "--- a/calc/ops.py\n+++ b/calc/ops2.py\n@@ -1 +1 @@\n-def add(a, b):\n+def add(a, b):  #\n"
    with pytest.raises(PatchError, match="Renaming"):
        patcher.prepare(answer(diff), repo, 60)


def test_missing_file_is_rejected(repo: Path):
    diff = "--- a/calc/nope.py\n+++ b/calc/nope.py\n@@ -1 +1 @@\n-a\n+b\n"
    with pytest.raises(PatchError, match="File not found"):
        patcher.prepare(answer(diff), repo, 60)


def test_file_without_trailing_newline(repo: Path):
    (repo / "calc" / "last.py").write_bytes(b"def f():\n    return 1")
    diff = "--- a/calc/last.py\n+++ b/calc/last.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"
    patcher.apply(patcher.prepare(answer(diff), repo, 60), repo)
    assert (repo / "calc" / "last.py").read_bytes() == b"def f():\n    return 2"


def test_crlf_file(repo: Path):
    (repo / "calc" / "win.py").write_bytes(b"def f():\r\n    return 1\r\n")
    diff = "--- a/calc/win.py\n+++ b/calc/win.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"
    patcher.apply(patcher.prepare(answer(diff), repo, 60), repo)
    assert (repo / "calc" / "win.py").read_bytes() == b"def f():\r\n    return 2\r\n"


def test_file_classification():
    assert is_test_file("tests/unit/helpers.py")
    assert is_test_file("pkg/test_thing.py") and is_test_file("pkg/thing_test.py")
    assert not is_test_file("pkg/testing_utils.py") and not is_test_file("pkg/contest.py")
    assert is_config_file("sub/pyproject.toml") and not is_config_file("pkg/config.py")


def test_hunk_without_line_numbers(repo: Path):
    bare = GOOD.replace("@@ -1,2 +1,2 @@", "@@ @@")
    prepared = patcher.prepare(answer(bare), repo, 60)
    assert prepared.new_contents["calc/ops.py"].startswith("def add(a, b):\n    return a + b\n")


def test_uniform_indentation_shift_is_repaired(repo: Path):
    (repo / "calc" / "box.py").write_text(
        "class Box:\n    def area(self):\n        return self.w + self.h\n", encoding="utf-8")
    flat = ("--- a/calc/box.py\n+++ b/calc/box.py\n@@ -2,2 +2,2 @@\n"
            " def area(self):\n-    return self.w + self.h\n+    return self.w * self.h\n")
    patcher.apply(patcher.prepare(answer(flat), repo, 60), repo)
    assert "        return self.w * self.h\n" in (repo / "calc" / "box.py").read_text(encoding="utf-8")


def test_syntax_error_is_rejected_before_any_test_run(repo: Path):
    broken = GOOD.replace("+    return a + b", "+    return a +")
    with pytest.raises(PatchError, match="does not compile: invalid syntax"):
        patcher.prepare(answer(broken), repo, 60)


@pytest.mark.parametrize(
    ("added", "match"),
    [
        ('    if os.environ.get("PYTEST_CURRENT_TEST"): return 3', "PYTEST_CURRENT_TEST"),
        ('    if "pytest" in sys.modules: return 3', "detects or imports pytest"),
        ("    caller = sys._getframe(1).f_code.co_name", "inspects the call stack"),
        ('    data = open("tests/expected.json").read()', "references test files"),
        ("    from unittest.mock import patch", "adds mocking"),
    ],
)
def test_test_gaming_code_is_rejected(repo: Path, added: str, match: str):
    diff = GOOD.replace("+    return a + b", f"+{added}\n+    return a + b")
    with pytest.raises(PatchError, match=match):
        patcher.prepare(answer(diff), repo, 60)
