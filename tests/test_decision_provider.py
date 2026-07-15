import json

import pytest

from app.config import Settings
from app.decision_provider import (
    DecisionProviderError,
    LangChainStructuredDecisionProvider,
    _usage,
    build_decision_provider,
)
from app.decision_schemas import IntentDecision


class FakeRaw:
    usage_metadata = {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16}
    response_metadata = {}


class FakeRunnable:
    def __init__(self, schema, failures=0):
        self.schema = schema
        self.failures = failures

    def invoke(self, messages):
        assert "JSON Schema" in messages[0][1]
        assert "task_type" in messages[0][1]
        if self.failures > 0:
            self.failures -= 1
            raise ConnectionError("temporary provider failure")
        return {
            "parsed": self.schema(
                task_type="knowledge_qa", decision_basis="课程知识问题"
            ),
            "raw": FakeRaw(),
            "parsing_error": None,
        }


class FakeChatOpenAI:
    failures = 0
    init_kwargs = None

    def __init__(self, **kwargs):
        FakeChatOpenAI.init_kwargs = kwargs

    def with_structured_output(self, schema, include_raw, method):
        assert include_raw is True
        assert method in {"json_schema", "json_mode", "function_calling"}
        return FakeRunnable(schema, failures=FakeChatOpenAI.failures)


def test_structured_provider_records_schema_usage_and_never_traces_key(monkeypatch):
    import langchain_openai

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setenv("ROBOT_TEST_LLM_KEY", "super-secret-value")
    settings = Settings(
        agent_profile="agentic-online",
        llm_api_key_env="ROBOT_TEST_LLM_KEY",
        llm_model="test-model",
        llm_base_url="https://example.invalid/v1",
        llm_provider="test-compatible",
        llm_max_retries=0,
        llm_input_cost_per_million=1.0,
        llm_output_cost_per_million=2.0,
    )
    provider = LangChainStructuredDecisionProvider(settings)
    call = provider.decide(
        "llm_intent",
        IntentDecision,
        "classify",
        {"message": "什么是示教编程"},
    )
    assert call.value.task_type.value == "knowledge_qa"
    assert call.trace["usage"]["total_tokens"] == 16
    assert call.trace["estimated_cost_usd"] > 0
    assert call.trace["attempts"] == 1
    assert "super-secret-value" not in json.dumps(call.trace)
    assert FakeChatOpenAI.init_kwargs["model"] == "test-model"
    assert FakeChatOpenAI.init_kwargs["base_url"] == "https://example.invalid/v1"
    assert provider.provider_name == "test-compatible"


def test_structured_provider_has_bounded_retry_and_clear_error(monkeypatch):
    import langchain_openai

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setenv("ROBOT_TEST_LLM_KEY", "secret")
    FakeChatOpenAI.failures = 5
    settings = Settings(
        agent_profile="agentic-online",
        llm_api_key_env="ROBOT_TEST_LLM_KEY",
        llm_max_retries=1,
    )
    provider = LangChainStructuredDecisionProvider(settings)
    with pytest.raises(DecisionProviderError) as captured:
        provider.decide(
            "llm_intent", IntentDecision, "classify", {"message": "课程问题"}
        )
    assert captured.value.attempts == 2
    FakeChatOpenAI.failures = 0


def test_provider_readiness_and_usage_fallback(monkeypatch):
    monkeypatch.delenv("ROBOT_MISSING_KEY", raising=False)
    with pytest.raises(ValueError, match="ROBOT_MISSING_KEY"):
        LangChainStructuredDecisionProvider(
            Settings(
                agent_profile="agentic-online",
                llm_api_key_env="ROBOT_MISSING_KEY",
            )
        )
    assert build_decision_provider(Settings(agent_profile="portable")) is None

    class MetadataRaw:
        usage_metadata = None
        response_metadata = {
            "token_usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
        }

    assert _usage(MetadataRaw()) == {
        "input_tokens": 3,
        "output_tokens": 2,
        "total_tokens": 5,
    }
