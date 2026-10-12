"""Parse `pytest -q --tb=short -rfE` output into structured failures."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

_BLOCK_RE = re.compile(r"^={3,} (.*?) ={3,}$")
_TITLE_RE = re.compile(r"^_{3,} (.*?) _{3,}$")
_FRAME_RE = re.compile(r"^(?P<path>(?:[A-Za-z]:)?[^:\s][^:]*?\.py):(?P<line>\d+): in (?P<func>\S+)")
_SUMMARY_RE = re.compile(r"^(?P<kind>FAILED|ERROR) (?P<id>\S+?)(?: - (?P<msg>.*))?$")
_COUNT_RE = re.compile(
    r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected|warnings?)"
)
_FINAL_RE = re.compile(r"\bin \d+(?:\.\d+)?s\b")
_MAX_TRACEBACK_CHARS = 2_000


class Frame(BaseModel):
    """One `path:line: in func` traceback frame."""

    path: str
    line: int
    func: str


class Failure(BaseModel):
    """One failing test or collection/setup error."""

    test_id: str
    kind: Literal["failed", "error"]
    message: str
    traceback: str = ""
    frames: list[Frame] = Field(default_factory=list)


class PytestReport(BaseModel):
    """Structured view of one pytest run."""

    exit_code: int
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    failures: list[Failure] = Field(default_factory=list)
    summary: str = ""
    timed_out: bool = False

    @property
    def all_passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.bad == 0

    @property
    def bad(self) -> int:
        return self.failed + self.errors

    def signature(self) -> tuple[tuple[str, str], ...]:
        """Identity of the failure set, used to detect patches that changed nothing."""
        return tuple(sorted((f.test_id, f.message[:200]) for f in self.failures))

    def failing_ids(self) -> list[str]:
        return [f.test_id for f in self.failures]


def _title_key(test_id: str) -> str:
    """`tests/test_a.py::TestX::test_y` -> `TestX.test_y` (pytest's section title form)."""
    return test_id.split("::", 1)[1].replace("::", ".") if "::" in test_id else test_id


def _sections(lines: list[str]) -> dict[str, list[str]]:
    """Collect `____ title ____` sections inside the FAILURES and ERRORS blocks."""
    sections: dict[str, list[str]] = {}
    in_block = False
    current: list[str] | None = None
    for line in lines:
        block = _BLOCK_RE.match(line)
        if block:
            in_block = block.group(1).strip() in {"FAILURES", "ERRORS"}
            current = None
            continue
        if not in_block:
            continue
        title = _TITLE_RE.match(line)
        if title:
            current = sections.setdefault(title.group(1).strip(), [])
        elif current is not None:
            current.append(line)
    return sections


def _frames(body: list[str]) -> list[Frame]:
    frames = []
    for line in body:
        m = _FRAME_RE.match(line)
        if m:
            frames.append(
                Frame(path=m["path"].replace("\\", "/"), line=int(m["line"]), func=m["func"])
            )
    return frames


def _first_error_line(body: list[str]) -> str:
    for line in body:
        if line.startswith("E "):
            return line[1:].strip()
    return ""


def _counts(lines: list[str]) -> tuple[dict[str, int], str]:
    for line in reversed(lines):
        text = line.strip("= ").strip()
        if _FINAL_RE.search(text) and (_COUNT_RE.search(text) or text.startswith("no tests ran")):
            counts: dict[str, int] = {}
            for num, word in _COUNT_RE.findall(text):
                key = "errors" if word.startswith("error") else word
                counts[key] = counts.get(key, 0) + int(num)
            return counts, text
    return {}, ""


def _normalize_id(test_id: str) -> str:
    """Use forward slashes in the file part (pytest on Windows prints backslashes)."""
    path, sep, rest = test_id.partition("::")
    return path.replace("\\", "/") + sep + rest


def _body_for(sections: dict[str, list[str]], kind: str, test_id: str) -> list[str]:
    key = _title_key(test_id)
    candidates = [key] if kind == "failed" else [
        f"ERROR collecting {test_id}",
        f"ERROR at setup of {key}",
        f"ERROR at teardown of {key}",
        key,
    ]
    for name in candidates:
        if name in sections:
            return sections[name]
    if kind == "error":  # collection titles are rootdir-relative; match by suffix
        for title, body in sections.items():
            if title.startswith("ERROR collecting ") and title.endswith(test_id):
                return body
    return []


def parse_pytest_output(output: str, exit_code: int, timed_out: bool = False) -> PytestReport:
    """Turn raw pytest output into a `PytestReport`. Tolerates truncated output."""
    lines = output.splitlines()
    sections = _sections(lines)
    counts, summary = _counts(lines)

    failures: list[Failure] = []
    seen: set[str] = set()
    for line in lines:
        m = _SUMMARY_RE.match(line)
        if not m:
            continue
        test_id = _normalize_id(m["id"])
        if test_id in seen:
            continue
        kind = "failed" if m["kind"] == "FAILED" else "error"
        body = _body_for(sections, kind, test_id)
        seen.add(test_id)
        failures.append(
            Failure(
                test_id=test_id,
                kind=kind,
                message=(m["msg"] or _first_error_line(body)).strip(),
                traceback="\n".join(body)[:_MAX_TRACEBACK_CHARS],
                frames=_frames(body),
            )
        )

    if not failures:  # summary lines missing (e.g. truncated output): fall back to sections
        for title, body in sections.items():
            kind = "error" if title.startswith("ERROR ") else "failed"
            failures.append(
                Failure(
                    test_id=title.removeprefix("ERROR collecting ").strip(),
                    kind=kind,
                    message=_first_error_line(body),
                    traceback="\n".join(body)[:_MAX_TRACEBACK_CHARS],
                    frames=_frames(body),
                )
            )

    failed = counts.get("failed", sum(f.kind == "failed" for f in failures))
    errors = counts.get("errors", sum(f.kind == "error" for f in failures))
    return PytestReport(
        exit_code=exit_code,
        passed=counts.get("passed", 0),
        failed=failed,
        errors=errors,
        skipped=counts.get("skipped", 0),
        failures=failures,
        summary=summary or ("timed out" if timed_out else ""),
        timed_out=timed_out,
    )


def compact_failures(report: PytestReport, limit: int = 8) -> str:
    """Deterministic, compact failure list for prompts (test id, message, frames)."""
    if report.timed_out:
        head = "The test run TIMED OUT (possible infinite loop or hang)."
    else:
        head = f"{report.passed} passed, {report.failed} failed, {report.errors} errors."
    out = [head]
    for f in report.failures[:limit]:
        frames = " -> ".join(f"{fr.path}:{fr.line} in {fr.func}" for fr in f.frames[-4:])
        out.append(f"- [{f.kind}] {f.test_id}: {f.message[:300]}")
        if frames:
            out.append(f"  frames: {frames}")
    if len(report.failures) > limit:
        out.append(f"- ... and {len(report.failures) - limit} more")
    return "\n".join(out)
