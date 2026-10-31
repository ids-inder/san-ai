"""Provider tests with a stubbed SDK client - no network, no API key spent."""
from __future__ import annotations

import pytest

from sanai.config import Settings
from sanai.providers.base import Budget, BudgetExceeded, ProviderError, Usage, salvage_json

anthropic = pytest.importorskip("anthropic")
from sanai.providers.anthropic_provider import AnthropicLLM  # noqa: E402


class FakeUsage:
    input_tokens = 1000
    output_tokens = 200
    cache_read_input_tokens = 500
    cache_creation_input_tokens = 0


class FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class FakeMessage:
    def __init__(self, text: str = '{"ok": true}', stop_reason: str = "end_turn",
                 model: str = "claude-opus-5-5") -> None:
        self.content = [FakeBlock(text)]
        self.stop_reason = stop_reason
        self.model = model
        self.usage = FakeUsage()
        self.stop_details = None


class FakeStream:
    def __init__(self, msg: FakeMessage) -> None:
        self._msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def get_final_message(self):
        return self._msg


class FakeMessages:
    def __init__(self, msg: FakeMessage, beta_raises: Exception | None = None) -> None:
        self.msg = msg
        self.beta_raises = beta_raises
        self.beta_calls = 0
        self.plain_calls = 0
        self.last_kwargs: dict = {}

    def stream(self, **kwargs):
        if "betas" in kwargs:
            self.beta_calls += 1
            if self.beta_raises:
                raise self.beta_raises
        else:
            self.plain_calls += 1
        self.last_kwargs = kwargs
        return FakeStream(self.msg)


class FakeClient:
    def __init__(self, messages: FakeMessages) -> None:
        self.messages = messages
        self.beta = type("B", (), {"messages": messages})()


def build(monkeypatch, msg=None, beta_raises=None) -> tuple[AnthropicLLM, FakeMessages]:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    cfg = Settings(_env_file=None)
    llm = AnthropicLLM(cfg)
    fake = FakeMessages(msg or FakeMessage(), beta_raises)
    llm.client = FakeClient(fake)
    return llm, fake


def test_requests_opt_into_refusal_fallback(monkeypatch):
    llm, fake = build(monkeypatch)
    llm.text("sys", "hi")
    assert fake.beta_calls == 1 and fake.plain_calls == 0
    assert fake.last_kwargs["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in fake.last_kwargs["betas"]


def test_unsupported_beta_degrades_once_and_stays_degraded(monkeypatch):
    fake_response = type(
        "R", (), {"status_code": 400, "headers": {}, "request": object()}
    )()
    err = anthropic.BadRequestError("unknown beta", response=fake_response, body=None)
    llm, fake = build(monkeypatch, beta_raises=err)
    assert llm.text("sys", "a") == '{"ok": true}'
    assert fake.beta_calls == 1 and fake.plain_calls == 1
    llm.text("sys", "b")
    assert fake.beta_calls == 1, "must not retry the rejected beta every call"
    assert fake.plain_calls == 2


def test_request_shape_matches_the_model(monkeypatch):
    llm, fake = build(monkeypatch)
    llm.text("sys", "hi", deep=True)
    k = fake.last_kwargs
    assert k["thinking"] == {"type": "adaptive"}
    assert k["output_config"]["effort"] == "max", "deep calls use max effort"
    assert k["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "budget_tokens" not in str(k), "budget_tokens is rejected on this model"
    assert "temperature" not in k and "tool_choice" not in k


def test_json_passes_a_schema_and_parses(monkeypatch):
    llm, fake = build(monkeypatch, FakeMessage('{"a": 1, "b": ["x"]}'))
    schema = {"type": "object", "properties": {"a": {"type": "integer"}},
              "required": ["a"], "additionalProperties": False}
    assert llm.json("sys", "go", schema) == {"a": 1, "b": ["x"]}
    assert fake.last_kwargs["output_config"]["format"]["type"] == "json_schema"
    assert fake.last_kwargs["output_config"]["format"]["schema"] is schema


def test_refusal_becomes_an_error_not_a_silent_empty(monkeypatch):
    msg = FakeMessage(text="", stop_reason="refusal")
    msg.stop_details = type("D", (), {"category": "cyber"})()
    llm, _fake = build(monkeypatch, msg)
    with pytest.raises(ProviderError, match="cyber"):
        llm.text("sys", "hi")


def test_usage_is_recorded_against_the_model_that_served_it(monkeypatch):
    llm, _fake = build(monkeypatch, FakeMessage(model="claude-opus-5"))
    llm.text("sys", "hi")
    assert llm.usage.calls == 1
    assert llm.usage.per_model == {"claude-opus-5": 1}
    assert llm.usage.cost_usd > 0


def test_budget_stops_before_the_call_is_made(monkeypatch):
    llm, fake = build(monkeypatch)
    llm.budget = Budget(max_calls=1)
    llm.text("sys", "first")
    with pytest.raises(BudgetExceeded, match="call ceiling"):
        llm.text("sys", "second")
    assert fake.beta_calls == 1, "the over-budget call must not reach the API"


def test_empty_response_is_an_error(monkeypatch):
    llm, _fake = build(monkeypatch, FakeMessage(text=""))
    with pytest.raises(ProviderError, match="empty response"):
        llm.text("sys", "hi")


@pytest.mark.parametrize("raw,expected", [
    ('{"a":1}', {"a": 1}),
    ('```json\n{"a":1}\n```', {"a": 1}),
    ('Sure!\n```\n{"a":1}\n```\n', {"a": 1}),
    ('prose {"a":1} more', {"a": 1}),
])
def test_json_salvage(raw, expected):
    assert salvage_json(raw) == expected


def test_json_salvage_gives_up_loudly():
    with pytest.raises(ProviderError, match="could not parse"):
        salvage_json("no json at all")


def test_usage_cost_arithmetic():
    u = Usage()
    u.add("claude-opus-5-5", input_tokens=1_000_000, output_tokens=0)
    assert u.cost_usd == pytest.approx(4.00)
    u.add("claude-opus-5-5", output_tokens=1_000_000)
    assert u.cost_usd == pytest.approx(24.00)
