import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Protocol, Type

from pydantic import BaseModel, SecretStr

from app.config import Settings
from app.decision_schemas import DECISION_SCHEMA_VERSION


class DecisionProviderError(RuntimeError):
    def __init__(self, message: str, attempts: int):
        super().__init__(message)
        self.attempts = attempts


@dataclass(frozen=True)
class DecisionCall:
    value: BaseModel
    trace: Dict[str, Any]


class DecisionProvider(Protocol):
    provider_name: str
    model_name: str

    def decide(
        self,
        node: str,
        schema: Type[BaseModel],
        system_instruction: str,
        payload: Dict[str, Any],
    ) -> DecisionCall: ...


def _usage(raw: Any) -> Dict[str, int]:
    usage = getattr(raw, "usage_metadata", None) or {}
    if usage:
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": int(usage.get("total_tokens", input_tokens + output_tokens) or 0),
        }
    metadata = getattr(raw, "response_metadata", None) or {}
    token_usage = metadata.get("token_usage", {})
    input_tokens = int(token_usage.get("prompt_tokens", 0) or 0)
    output_tokens = int(token_usage.get("completion_tokens", 0) or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": int(token_usage.get("total_tokens", input_tokens + output_tokens) or 0),
    }


class LangChainStructuredDecisionProvider:
    """Real structured LLM calls. The API key is read at runtime and never traced."""

    def __init__(self, settings: Settings):
        key = os.getenv(settings.llm_api_key_env)
        if not key:
            raise ValueError("缺少模型密钥环境变量：%s" % settings.llm_api_key_env)
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover - depends on optional installation
            raise ValueError("agentic 档需要安装 requirements-agentic.txt") from exc
        self.settings = settings
        self.provider_name = settings.llm_provider
        self.model_name = settings.llm_model
        kwargs: Dict[str, Any] = {
            "model": settings.llm_model,
            "api_key": SecretStr(key),
            "temperature": 0,
            "timeout": settings.llm_timeout_seconds,
            "max_retries": 0,
        }
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        if settings.llm_thinking_mode:
            kwargs["extra_body"] = {
                "thinking": {"type": settings.llm_thinking_mode}
            }
        self._model = ChatOpenAI(**kwargs)

    def decide(
        self,
        node: str,
        schema: Type[BaseModel],
        system_instruction: str,
        payload: Dict[str, Any],
    ) -> DecisionCall:
        started = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(1, self.settings.llm_max_retries + 2):
            try:
                runnable = self._model.with_structured_output(
                    schema,
                    include_raw=True,
                    method=self.settings.llm_structured_output_method,
                )
                schema_instruction = json.dumps(
                    schema.model_json_schema(), ensure_ascii=False, separators=(",", ":")
                )
                result = runnable.invoke(
                    [
                        (
                            "system",
                            system_instruction
                            + " 必须仅输出符合下列 JSON Schema 的 JSON 对象，字段名必须完全一致："
                            + schema_instruction,
                        ),
                        ("human", json.dumps(payload, ensure_ascii=False)),
                    ]
                )
                parsed = result.get("parsed")
                parsing_error = result.get("parsing_error")
                if parsing_error or parsed is None:
                    raise ValueError("模型结构化输出校验失败：%s" % parsing_error)
                usage = _usage(result.get("raw"))
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                estimated_cost = round(
                    usage["input_tokens"] * self.settings.llm_input_cost_per_million / 1_000_000
                    + usage["output_tokens"] * self.settings.llm_output_cost_per_million / 1_000_000,
                    8,
                )
                return DecisionCall(
                    parsed,
                    {
                        "decision_id": "dec_" + uuid.uuid4().hex,
                        "node": node,
                        "schema_name": schema.__name__,
                        "schema_version": DECISION_SCHEMA_VERSION,
                        "input_fields": sorted(payload.keys()),
                        "output": parsed.model_dump(mode="json"),
                        "provider": self.provider_name,
                        "model": self.model_name,
                        "usage": usage,
                        "estimated_cost_usd": estimated_cost,
                        "duration_ms": duration_ms,
                        "attempts": attempt,
                        "validation_result": "passed",
                        "fallback_used": False,
                    },
                )
            except Exception as exc:  # provider and schema failures share the bounded retry budget
                last_error = exc
        raise DecisionProviderError(str(last_error)[:500], self.settings.llm_max_retries + 1)


def build_decision_provider(settings: Settings) -> DecisionProvider | None:
    if settings.agent_profile == "portable":
        return None
    return LangChainStructuredDecisionProvider(settings)
