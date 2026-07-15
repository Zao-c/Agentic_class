import time

import pytest

from app.tool_runtime import ToolExecutionError, ToolPlanValidator, ToolRuntime, ToolSpec


def test_timeout_returns_structured_error():
    runtime = ToolRuntime()
    spec = ToolSpec("slow_tool", timeout_seconds=0.01)
    with pytest.raises(ToolExecutionError) as caught:
        runtime.execute(spec, lambda: time.sleep(0.05))
    assert caught.value.code == "TOOL_TIMEOUT"
    assert caught.value.retryable is True


def test_retry_can_recover():
    runtime = ToolRuntime()
    attempts = {"count": 0}

    def flaky():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ConnectionError("temporary")
        return "ok"

    result = runtime.execute(ToolSpec("flaky", timeout_seconds=1, max_retries=1), flaky)
    assert result.value == "ok"
    assert result.attempts == 2


def test_circuit_opens_after_repeated_failures():
    runtime = ToolRuntime(failure_threshold=2, reset_seconds=60)
    spec = ToolSpec("broken", timeout_seconds=1)
    for _ in range(2):
        with pytest.raises(ToolExecutionError):
            runtime.execute(spec, lambda: (_ for _ in ()).throw(ValueError("broken")))
    with pytest.raises(ToolExecutionError) as caught:
        runtime.execute(spec, lambda: "not called")
    assert caught.value.code == "TOOL_CIRCUIT_OPEN"


def test_tool_plan_removes_unknown_arguments_and_rejects_cross_task_tool():
    result = ToolPlanValidator().validate(
        "knowledge_qa",
        [
            {
                "name": "course_retrieval",
                "arguments": {"query": "示教编程", "top_k": "999"},
                "reason": "检索课程资料",
            },
            {
                "name": "get_student_profile",
                "arguments": {},
                "reason": "尝试读取不需要的资料",
            },
        ],
        {"normalized_query": "示教编程"},
        {"normalized_query": "llm_structured_query_rewrite"},
    )

    assert result["validated_plan"] == [
        {
            "name": "course_retrieval",
            "arguments": {"query": "示教编程"},
            "argument_sources": {
                "query": "normalized_query:llm_structured_query_rewrite"
            },
            "argument_validation": {"query": "accepted_model_value"},
            "reason": "检索课程资料",
            "validation_status": "passed",
        }
    ]
    assert any(
        item["action"] == "removed_argument" and item.get("argument") == "top_k"
        for item in result["adjustments"]
    )
    assert any(
        item["action"] == "rejected_tool"
        and item["tool_name"] == "get_student_profile"
        for item in result["adjustments"]
    )


def test_tool_plan_overrides_hallucinated_slot_values_and_adds_required_tool():
    result = ToolPlanValidator().validate(
        "fault_diagnosis",
        [
            {
                "name": "lookup_error_code",
                "arguments": {
                    "code": "FAKE-999",
                    "equipment_brand": "KUKA",
                    "equipment_model": "KR999",
                    "controller_version": "invented",
                },
                "reason": "查询报警码",
            }
        ],
        {
            "normalized_query": "ABB IRB120 报警38213，手动模式",
            "slots.equipment": "ABB IRB120",
            "slots.error_code": "38213",
            "slots.equipment_brand": "ABB",
            "slots.equipment_model": "IRB120",
            "slots.controller_version": None,
        },
        {
            "slots.error_code": "user_current",
            "slots.equipment_brand": "user_current",
            "slots.equipment_model": "user_current",
        },
    )

    lookup = next(item for item in result["validated_plan"] if item["name"] == "lookup_error_code")
    assert lookup["arguments"] == {
        "code": "38213",
        "equipment_brand": "ABB",
        "equipment_model": "IRB120",
    }
    assert lookup["argument_validation"] == {
        "code": "overridden_by_control_plane",
        "equipment_brand": "overridden_by_control_plane",
        "equipment_model": "overridden_by_control_plane",
    }
    assert any(item["name"] == "manual_retrieval" for item in result["validated_plan"])
    assert any(
        item["reason"] == "trusted_source_missing"
        and item.get("argument") == "controller_version"
        for item in result["adjustments"]
    )
