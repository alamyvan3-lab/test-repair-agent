"""Thin OpenAI-compatible client for Token Factory: retries with backoff and per-call accounting."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import openai

from .config import RoleModel, Settings

Role = Literal["ultra", "nano"]
Message = dict[str, str]

log = logging.getLogger("test_repair.llm")

_RETRYABLE = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class LLMError(RuntimeError):
    """A model call failed permanently."""


@dataclass(frozen=True)
class LLMResult:
    """One completed chat call."""

    role: Role
    model: str
    text: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    finish_reason: str | None


class ChatModel(Protocol):
    """Anything that can answer a chat request for a role (real client or test fake)."""

    def chat(self, role: Role, messages: list[Message]) -> LLMResult: ...


def strip_reasoning(text: str) -> str:
    """Remove `<think>` blocks some reasoning models put in `content`."""
    text = _THINK_RE.sub("", text)
    if "</think>" in text:  # opening tag was part of the prompt template
        text = text.rsplit("</think>", 1)[1]
    return text.strip()


class TokenFactoryClient:
    """Calls Nebius Token Factory through the OpenAI-compatible API."""

    def __init__(
        self,
        settings: Settings,
        client_factory: Callable[[str], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._factory = client_factory or self._make_client
        self._clients: dict[str, Any] = {}
        self._sleep = sleep

    def _make_client(self, base_url: str) -> openai.OpenAI:
        return openai.OpenAI(
            api_key=self._settings.api_key,
            base_url=base_url,
            timeout=self._settings.llm_timeout_s,
            max_retries=0,  # retries are handled here so they are logged and counted
        )

    def _client(self, base_url: str) -> Any:
        if base_url not in self._clients:
            self._clients[base_url] = self._factory(base_url)
        return self._clients[base_url]

    def role_model(self, role: Role) -> RoleModel:
        return self._settings.ultra if role == "ultra" else self._settings.nano

    def chat(self, role: Role, messages: list[Message]) -> LLMResult:
        cfg = self.role_model(role)
        client = self._client(cfg.base_url)
        retries = self._settings.llm_max_retries
        for attempt in range(retries + 1):
            start = time.perf_counter()
            try:
                resp = client.chat.completions.create(
                    model=cfg.model,
                    messages=messages,
                    temperature=cfg.temperature,
                    max_tokens=cfg.max_tokens,
                    extra_body=cfg.extra_body or None,
                )
            except _RETRYABLE as exc:
                if attempt == retries:
                    raise LLMError(
                        f"{role} call to {cfg.model} failed after {retries + 1} tries: "
                        f"{type(exc).__name__}"
                    ) from exc
                delay = min(2.0**attempt, 20.0)
                log.warning(
                    "llm retry role=%s model=%s error=%s delay=%.1fs",
                    role, cfg.model, type(exc).__name__, delay,
                )
                self._sleep(delay)
                continue
            except openai.APIStatusError as exc:
                raise LLMError(
                    f"{role} call to {cfg.model} rejected: HTTP {exc.status_code}: "
                    f"{str(exc.message)[:300]}"
                ) from exc
            latency = time.perf_counter() - start
            return self._result(role, cfg.model, resp, latency)
        raise AssertionError("unreachable")

    @staticmethod
    def _result(role: Role, model: str, resp: Any, latency: float) -> LLMResult:
        if not resp.choices:
            raise LLMError(f"{role} call to {model} returned no choices")
        choice = resp.choices[0]
        usage = resp.usage
        result = LLMResult(
            role=role,
            model=resp.model or model,
            text=strip_reasoning(choice.message.content or ""),
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            latency_s=latency,
            finish_reason=choice.finish_reason,
        )
        log.info(
            "llm role=%s model=%s prompt_tokens=%d completion_tokens=%d latency=%.2fs finish=%s",
            role, result.model, result.prompt_tokens, result.completion_tokens,
            latency, result.finish_reason,
        )
        return result
