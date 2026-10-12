"""Test helpers: a scripted fake LLM and reference fixes."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from test_repair.llm import LLMResult, Role
from test_repair.sandbox import DockerSandbox

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "bench"
EXAMPLES = ROOT / "examples"
FAKE_KEY = "tf-test-key-0123456789abcdef"

Messages = list[dict[str, str]]
Responder = Callable[[Messages], str]

TRIAGE_OK = json.dumps({
    "failing_tests": ["tests/test_x.py::test_y"],
    "error_class": "AssertionError",
    "key_lines": ["E   assert 1 == 2"],
    "summary": "An assertion fails in the tested function.",
})
SANITY_OK = json.dumps({"verdict": "ok", "reason": "Fixes the logic generally."})
SANITY_SPECIAL = json.dumps({"verdict": "special_case", "reason": "Hard-codes the test value."})

needs_docker = pytest.mark.skipif(not DockerSandbox.available(), reason="Docker not available")


def reference_fix(name: str) -> str:
    for base in (BENCH / "reference_fixes", EXAMPLES / "reference_fixes"):
        path = base / f"{name}.patch"
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError(name)


def ultra_answer(diff: str, diagnosis: str = "The function computes the wrong value.") -> str:
    return f"DIAGNOSIS: {diagnosis}\n\n```diff\n{diff}```\n"


def default_nano(messages: Messages) -> str:
    return TRIAGE_OK if "You triage" in messages[0]["content"] else SANITY_OK


class FakeLLM:
    """Scripted ChatModel. Ultra answers are consumed in order (the last one repeats)."""

    def __init__(
        self,
        ultra: list[str | Responder],
        nano: Responder = default_nano,
        tokens: tuple[int, int] = (100, 20),
    ) -> None:
        self.ultra = list(ultra)
        self.nano = nano
        self.tokens = tokens
        self.calls: list[tuple[Role, Messages]] = []

    def chat(self, role: Role, messages: Messages) -> LLMResult:
        self.calls.append((role, messages))
        if role == "ultra":
            item = self.ultra.pop(0) if len(self.ultra) > 1 else self.ultra[0]
            text = item(messages) if callable(item) else item
        else:
            text = self.nano(messages)
        return LLMResult(role=role, model=f"fake/{role}", text=text,
                         prompt_tokens=self.tokens[0], completion_tokens=self.tokens[1],
                         latency_s=0.01, finish_reason="stop")

    def roles(self) -> list[Role]:
        return [role for role, _ in self.calls]
