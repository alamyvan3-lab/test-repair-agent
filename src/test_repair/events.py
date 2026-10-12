"""Structured events shared by the agent, CLI, web server, and benchmark."""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "run_started",
    "tests_run",
    "triage",
    "diagnosis",
    "patch_proposed",
    "patch_rejected",
    "sanity_check",
    "patch_applied",
    "regression",
    "patch_reverted",
    "success",
    "failure",
]
Status = Literal["SUCCESS", "EXHAUSTED", "ABORTED_GUARDRAIL", "ERROR"]
TERMINAL_EVENTS = frozenset({"success", "failure"})

_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")


class Event(BaseModel):
    """One step of a run. LLM-backed steps carry role, model, tokens, and latency."""

    seq: int
    ts: float
    type: EventType
    message: str
    attempt: int | None = None
    role: Literal["ultra", "nano"] | None = None
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_s: float | None = None
    data: dict[str, Any] = Field(default_factory=dict)


def redact(value: Any, secrets: list[str]) -> Any:
    """Recursively replace secrets and bearer tokens in strings."""
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[REDACTED]")
        return _BEARER_RE.sub("Bearer [REDACTED]", value)
    if isinstance(value, dict):
        return {k: redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    return value


class EventLog:
    """Assigns sequence numbers and timestamps, redacts secrets, fans out to sinks."""

    def __init__(
        self,
        secrets: list[str] | None = None,
        sinks: list[Callable[[Event], None]] | None = None,
    ) -> None:
        self._secrets = [s for s in (secrets or []) if len(s) >= 8]
        self._sinks = list(sinks or [])
        self._lock = threading.Lock()
        self.events: list[Event] = []

    def add_sink(self, sink: Callable[[Event], None]) -> None:
        self._sinks.append(sink)

    def redact(self, value: Any) -> Any:
        return redact(value, self._secrets)

    def emit(self, type: EventType, message: str, **fields: Any) -> Event:
        with self._lock:
            event = Event(
                seq=len(self.events) + 1,
                ts=round(time.time(), 3),
                type=type,
                message=redact(message, self._secrets),
                **redact(fields, self._secrets),
            )
            self.events.append(event)
        for sink in self._sinks:
            sink(event)
        return event


def jsonl_sink(path: Path) -> Callable[[Event], None]:
    """Append each event as one JSON line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")

    def write(event: Event) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.model_dump(), ensure_ascii=False) + "\n")

    return write
