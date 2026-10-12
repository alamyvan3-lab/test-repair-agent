import logging
from types import SimpleNamespace

import httpx2
import openai
import pytest
from helpers import FAKE_KEY

from test_repair.config import Settings
from test_repair.llm import LLMError, TokenFactoryClient, strip_reasoning

REQUEST = httpx2.Request("POST", "http://127.0.0.1:9/v1/chat/completions")


def response(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        model="nvidia/fake-model",
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
    )


class FakeCompletions:
    def __init__(self, outcomes: list) -> None:
        self.outcomes = outcomes
        self.kwargs: list[dict] = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def client_with(settings: Settings, outcomes: list):
    completions = FakeCompletions(outcomes)
    fake = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    delays: list[float] = []
    client = TokenFactoryClient(settings, client_factory=lambda _url: fake, sleep=delays.append)
    return client, completions, delays


def test_success_reports_usage_and_strips_reasoning(settings: Settings):
    client, completions, _ = client_with(settings, [response("<think>hmm</think>\nanswer")])
    result = client.chat("ultra", [{"role": "user", "content": "hi"}])
    assert result.text == "answer"
    assert (result.prompt_tokens, result.completion_tokens) == (11, 7)
    assert result.role == "ultra" and result.model == "nvidia/fake-model"
    sent = completions.kwargs[0]
    assert sent["model"] == "fake/ultra" and sent["temperature"] == 0.0


def test_retries_with_backoff_then_succeeds(settings: Settings):
    errors = [openai.APIConnectionError(request=REQUEST), openai.APITimeoutError(request=REQUEST)]
    client, completions, delays = client_with(settings, [*errors, response("ok")])
    assert client.chat("nano", [{"role": "user", "content": "x"}]).text == "ok"
    assert delays == [1.0, 2.0]
    assert len(completions.kwargs) == 3


def test_gives_up_after_max_retries(settings: Settings):
    errors = [openai.APIConnectionError(request=REQUEST) for _ in range(4)]
    client, _, delays = client_with(settings, errors)
    with pytest.raises(LLMError, match="failed after 4 tries"):
        client.chat("nano", [{"role": "user", "content": "x"}])
    assert delays == [1.0, 2.0, 4.0]


def test_client_errors_are_not_retried(settings: Settings):
    resp = httpx2.Response(401, request=REQUEST)
    err = openai.AuthenticationError("bad key", response=resp, body=None)
    client, completions, delays = client_with(settings, [err])
    with pytest.raises(LLMError, match="HTTP 401"):
        client.chat("ultra", [{"role": "user", "content": "x"}])
    assert delays == [] and len(completions.kwargs) == 1


def test_logs_role_model_tokens_latency_but_never_the_key(settings: Settings,
                                                          caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO, logger="test_repair.llm")
    client, _, _ = client_with(settings, [response("ok")])
    client.chat("nano", [{"role": "user", "content": "x"}])
    text = caplog.text
    assert "role=nano" in text and "prompt_tokens=11" in text and "latency=" in text
    assert FAKE_KEY not in text


def test_strip_reasoning_variants():
    assert strip_reasoning("a<think>x\ny</think>b") == "ab"
    assert strip_reasoning("reasoning only</think>final") == "final"
    assert strip_reasoning("plain") == "plain"


def test_rate_limit_honors_retry_after(settings: Settings):
    resp = httpx2.Response(429, request=REQUEST, headers={"retry-after": "7"})
    err = openai.RateLimitError("slow down", response=resp, body=None)
    client, _, delays = client_with(settings, [err, response("ok")])
    assert client.chat("nano", [{"role": "user", "content": "x"}]).text == "ok"
    assert delays == [7.0]


def test_json_mode_falls_back_when_unsupported(settings: Settings):
    resp = httpx2.Response(400, request=REQUEST)
    err = openai.BadRequestError("response_format not supported", response=resp, body=None)
    client, completions, _ = client_with(settings, [err, response("{}"), response("{}")])
    client.chat("nano", [{"role": "user", "content": "x"}], json_mode=True)
    client.chat("nano", [{"role": "user", "content": "x"}], json_mode=True)
    sent = [k.get("response_format") for k in completions.kwargs]
    assert sent == [{"type": "json_object"}, None, None]  # remembered after the first 400


def test_temperature_override(settings: Settings):
    client, completions, _ = client_with(settings, [response("ok")])
    client.chat("ultra", [{"role": "user", "content": "x"}], temperature=0.2)
    assert completions.kwargs[0]["temperature"] == 0.2
