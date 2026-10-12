"""Extract, validate, normalize, and apply unified diffs proposed by the model.

Every rejection raises `PatchError` with a message precise enough to send back to the
model so it can correct its answer.
"""

from __future__ import annotations

import difflib
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path, PurePosixPath

_FENCE_RE = re.compile(r"```(?:diff|patch|udiff)[^\n]*\n(.*?)```", re.DOTALL)
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_TEST_DIRS = {"tests", "test"}
_CONFIG_FILES = {
    "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", "setup.py", "noxfile.py",
    ".coveragerc", "conftest.py",
}
_REJECTED_HEADERS = (
    "rename from", "rename to", "copy from", "copy to", "old mode", "new mode",
    "deleted file mode", "similarity index", "dissimilarity index",
)
_NO_NEWLINE = "\\ No newline at end of file"


class PatchError(ValueError):
    """A diff was rejected; the message is meant to be fed back to the model."""


@dataclass
class Hunk:
    old_start: int
    lines: list[str]  # each starts with " ", "-", "+" or "\\"

    @property
    def old_lines(self) -> list[str]:
        return [ln[1:] for ln in self.lines if ln[:1] in (" ", "-")]

    @property
    def new_len(self) -> int:
        return sum(ln[:1] in (" ", "+") for ln in self.lines)

    @property
    def changed(self) -> int:
        return sum(ln[:1] in ("+", "-") for ln in self.lines)


@dataclass
class FilePatch:
    old_path: str | None  # None means /dev/null (new file)
    new_path: str | None  # None means /dev/null (deletion)
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


@dataclass(frozen=True)
class PreparedPatch:
    """A validated diff that passed `git apply --check` against the workspace."""

    diff: str
    files: list[str]
    changed_lines: int


def is_test_file(path: str) -> bool:
    """True for files under tests/ or test/, test_*.py, *_test.py, and conftest.py."""
    p = PurePosixPath(path)
    name = p.name
    return (
        any(part in _TEST_DIRS for part in p.parts[:-1])
        or (name.startswith("test_") and name.endswith(".py"))
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def is_config_file(path: str) -> bool:
    return PurePosixPath(path).name in _CONFIG_FILES


def extract_diff(text: str) -> str:
    """Return the body of the single ```diff fenced block in a model answer."""
    blocks = _FENCE_RE.findall(text)
    if not blocks:
        raise PatchError(
            "No ```diff fenced block found. Return exactly one unified diff inside a "
            "```diff fence."
        )
    if len(blocks) > 1:
        raise PatchError(
            f"Found {len(blocks)} ```diff blocks. Put all changes into exactly one "
            "```diff block."
        )
    return blocks[0]


def _parse_path(raw: str, prefix: str) -> str | None:
    path = raw.split("\t", 1)[0].strip().strip('"')
    if path == "/dev/null":
        return None
    return path.removeprefix(prefix)


def _is_file_header(lines: list[str], i: int) -> bool:
    return (
        lines[i].startswith("--- ")
        and i + 1 < len(lines)
        and lines[i + 1].startswith("+++ ")
    )


def parse_diff(diff: str) -> list[FilePatch]:
    """Parse unified diff text. Hunk line counts are recomputed later, not trusted."""
    if "\0" in diff:
        raise PatchError("Binary content is not allowed in a diff.")
    lines = diff.splitlines()
    files: list[FilePatch] = []
    current: FilePatch | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(("GIT binary patch", "Binary files ")):
            raise PatchError("Binary patches are not allowed.")
        if line.startswith(_REJECTED_HEADERS):
            raise PatchError(
                f"Unsupported diff header {line!r}: renames, deletions, copies, and mode "
                "changes are not allowed. Only edit existing text files or add new ones."
            )
        if _is_file_header(lines, i):
            current = FilePatch(
                old_path=_parse_path(line[4:], "a/"),
                new_path=_parse_path(lines[i + 1][4:], "b/"),
            )
            files.append(current)
            i += 2
            continue
        if line.startswith("@@"):
            if current is None:
                raise PatchError("Hunk found before any '--- a/path' / '+++ b/path' header.")
            m = _HUNK_RE.match(line)
            if not m:
                raise PatchError(f"Malformed hunk header {line!r}. Expected '@@ -L,N +L,N @@'.")
            hunk = Hunk(old_start=int(m.group(1)), lines=[])
            i += 1
            pending_blank = 0  # blank lines are context lines whose leading space was lost
            while i < len(lines) and not (
                lines[i].startswith(("@@", "diff --git")) or _is_file_header(lines, i)
            ):
                body = lines[i]
                if body == "":
                    pending_blank += 1
                elif body[0] in " +-\\":
                    hunk.lines.extend([" "] * pending_blank)
                    pending_blank = 0
                    hunk.lines.append(body)
                else:
                    raise PatchError(
                        f"Malformed line in hunk {len(current.hunks) + 1} of {current.path}: "
                        f"{body[:80]!r}. Every hunk line must start with ' ', '+', or '-'."
                    )
                i += 1
            if hunk.changed == 0:
                raise PatchError(f"A hunk in {current.path} contains no '+' or '-' lines.")
            current.hunks.append(hunk)
            continue
        if line.startswith(("diff --git", "index ", "new file mode")) or not line.strip():
            i += 1
            continue
        raise PatchError(f"Unexpected line outside any hunk: {line[:80]!r}.")
    if not files:
        raise PatchError("The diff has no file headers ('--- a/path' and '+++ b/path').")
    for fp in files:
        if not fp.hunks:
            raise PatchError(f"File {fp.path} has a header but no hunks.")
    return files


def _check_path(path: str, repo: Path) -> None:
    if not path or path.startswith(("/", "\\")) or _DRIVE_RE.match(path) or "\\" in path:
        raise PatchError(
            f"Path {path!r} must be relative to the repository root and use forward slashes."
        )
    parts = PurePosixPath(path).parts
    if ".." in parts:
        raise PatchError(f"Path {path!r} contains '..'; paths must stay inside the repository.")
    if parts and parts[0] == ".git":
        raise PatchError(f"Path {path!r} is inside .git, which may not be edited.")
    root = repo.resolve()
    if not (repo / path).resolve().is_relative_to(root):
        raise PatchError(f"Path {path!r} resolves outside the repository.")


def validate(files: list[FilePatch], repo: Path, max_changed_lines: int) -> int:
    """Enforce path, protection, and size rules. Returns the number of changed lines."""
    total = 0
    for fp in files:
        if fp.new_path is None:
            raise PatchError(f"Deleting files is not allowed ({fp.old_path}).")
        for p in (fp.old_path, fp.new_path):
            if p is not None:
                _check_path(p, repo)
        if fp.old_path is not None and fp.old_path != fp.new_path:
            raise PatchError(
                f"Renaming {fp.old_path} to {fp.new_path} is not allowed; "
                "the '---' and '+++' paths must match."
            )
        path = fp.new_path
        if is_test_file(path):
            raise PatchError(
                f"Editing test files is not allowed ({path}). Fix the source code so the "
                "existing tests pass."
            )
        if is_config_file(path):
            raise PatchError(f"Editing configuration files is not allowed ({path}).")
        target = repo / path
        if fp.old_path is None and target.exists():
            raise PatchError(f"{path} already exists; use '--- a/{path}' to modify it.")
        if fp.old_path is not None and not target.is_file():
            raise PatchError(f"File not found in the repository: {path}.")
        total += sum(h.changed for h in fp.hunks)
    if total > max_changed_lines:
        raise PatchError(
            f"The diff changes {total} lines; the limit is {max_changed_lines}. "
            "Make a smaller, more targeted fix."
        )
    return total


def _find(haystack: list[str], needle: list[str], hint: int) -> int | None:
    """Locate `needle` in `haystack`, exact first, then ignoring trailing whitespace."""
    for norm in (lambda s: s, str.rstrip):
        hay = [norm(x) for x in haystack]
        target = [norm(x) for x in needle]
        n = len(target)
        hits = [i for i in range(len(hay) - n + 1) if hay[i : i + n] == target]
        if hits:
            return min(hits, key=lambda i: abs(i - hint))
    return None


def _mismatch(path: str, number: int, old: list[str], haystack: list[str]) -> PatchError:
    for k in range(len(old) - 1, 0, -1):
        if _find(haystack, old[:k], 0) is not None:
            return PatchError(
                f"Hunk {number} for {path} does not match the current file: the line "
                f"{old[k]!r} was not found after {old[k - 1]!r}. Copy context and removed "
                "lines exactly from the current source shown above."
            )
    return PatchError(
        f"Hunk {number} for {path} does not match the current file: the line {old[0]!r} "
        "does not appear in it. Copy context and removed lines exactly from the current "
        "source shown above."
    )


def _unified(path: str, before: list[str], after: list[str], new_file: bool) -> list[str]:
    """Standard 3-line-context diff (git requires trailing context except at EOF)."""
    out = [f"diff --git a/{path} b/{path}"] + (["new file mode 100644"] if new_file else [])
    for line in difflib.unified_diff(
        before, after, "/dev/null" if new_file else f"a/{path}", f"b/{path}", n=3
    ):
        out.append(line.removesuffix("\n"))
        if not line.endswith("\n"):
            out.append(_NO_NEWLINE)
    return out


def _render_file(fp: FilePatch, repo: Path) -> list[str]:
    """Locate hunks by content, apply them in memory, and re-diff against the real file."""
    path = fp.new_path or ""
    if fp.old_path is None:
        added = [ln[1:] + "\n" for h in fp.hunks for ln in h.lines if ln[:1] == "+"]
        return _unified(path, [], added, new_file=True)
    try:
        source = (repo / path).read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PatchError(f"{path} is not UTF-8 text and cannot be patched.") from exc
    eol = "\r\n" if "\r\n" in source else "\n"
    file_lines = source.splitlines()

    located: list[tuple[int, Hunk]] = []
    for number, hunk in enumerate(fp.hunks, 1):
        old = hunk.old_lines
        if not old:  # pure insertion without context: trust the stated position
            located.append((min(max(hunk.old_start, 0), len(file_lines)), hunk))
            continue
        pos = _find(file_lines, old, hunk.old_start - 1)
        if pos is None:
            raise _mismatch(path, number, old, file_lines)
        located.append((pos, hunk))

    located.sort(key=lambda item: item[0])
    for (pos_a, a), (pos_b, _) in pairwise(located):
        if pos_a + len(a.old_lines) > pos_b:
            raise PatchError(f"Hunks in {path} overlap; merge them into one hunk.")

    after: list[str] = []
    cursor = 0
    for pos, hunk in located:
        after += file_lines[cursor:pos]
        real = iter(file_lines[pos : pos + len(hunk.old_lines)])
        for ln in hunk.lines:
            if ln[:1] == " ":
                after.append(next(real))  # context comes from the file, not the model
            elif ln[:1] == "-":
                next(real)
            elif ln[:1] == "+":
                after.append(ln[1:])
        cursor = pos + len(hunk.old_lines)
    after += file_lines[cursor:]
    if after == file_lines:
        raise PatchError(f"The diff for {path} makes no change to the file.")

    def with_eol(lines: list[str]) -> list[str]:
        out = [ln + eol for ln in lines]
        if out and not source.endswith(("\n", "\r")):  # keep "no newline at end of file"
            out[-1] = out[-1].removesuffix(eol)
        return out

    return _unified(path, with_eol(file_lines), with_eol(after), new_file=False)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=repo, check=False, capture_output=True, text=True, timeout=60,
    )


def _git_apply(repo: Path, diff: str, check: bool) -> None:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".patch", delete=False, encoding="utf-8", newline="\n"
    ) as fh:
        fh.write(diff)
        patch_file = fh.name
    try:
        args = ["apply", "--whitespace=nowarn"] + (["--check"] if check else []) + [patch_file]
        proc = _git(repo, *args)
    finally:
        Path(patch_file).unlink(missing_ok=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().replace(patch_file, "<patch>")
        raise PatchError(f"git apply {'--check ' if check else ''}failed: {detail[:800]}")


def prepare(model_text: str, repo: Path, max_changed_lines: int) -> PreparedPatch:
    """Extract, validate, normalize, and dry-run a model's diff against `repo`."""
    files = parse_diff(extract_diff(model_text))
    changed = validate(files, repo, max_changed_lines)
    rendered: list[str] = []
    for fp in files:
        rendered.extend(_render_file(fp, repo))
    diff = "\n".join(rendered) + "\n"
    _git_apply(repo, diff, check=True)
    return PreparedPatch(diff=diff, files=[fp.path for fp in files], changed_lines=changed)


def apply(patch: PreparedPatch, repo: Path) -> None:
    """Apply a prepared patch to the workspace working tree."""
    _git_apply(repo, patch.diff, check=False)
