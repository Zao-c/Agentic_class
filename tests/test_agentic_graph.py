import json
import uuid
from pathlib import Path
from typing import Any, Dict, Type

from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.config import Settings
from app.decision_provider import DecisionCall, DecisionProviderError
from app.decision_schemas import (
    ClarificationDecision,
    EvidenceSupportDecision,
    IntentDecision,
    ProposedSlot,
    QueryRewriteDecision,
    SlotExtractionDecision,
    ToolPlanDecision,
    ToolProposal,
)
from app.main import create_app
from app.schemas import TaskType


class FakeStructuredProvider:
    provider_name = "fake-test-provider"
    model_name = "fake-structured-model"

    def __init__(self):
        self.calls = []

    def decide(
        self,
        node: str,
        schema: Type[BaseModel],
        system_instruction: str,
        payload: Dict[str, Any],
    ) -> DecisionCall:
        self.calls.append(node)
        message = payload.get("message", "")
        if schema is IntentDecision:
            value = IntentDecision(
                task_type=(
                    TaskType.fault_diagnosis if "报警" in message else TaskType.knowledge_qa
                ),
                decision_basis="消息包含报警信息" if "报警" in message else "课程知识问题",
            )
        elif schema is QueryRewriteDecision:
            value = QueryRewriteDecision(
                normalized_query=message,
                used_history=False,
                decision_basis="当前消息信息完整",
            )
        elif schema is SlotExtractionDecision:
            value = SlotExtractionDecision(
                equipment_brand=(
                    ProposedSlot(value="ABB", source="user_current") if "ABB" in message else None
                ),
                # Intentionally hallucinated: deterministic span validation must reject it.
                equipment_model=ProposedSlot(value="IRB9999", source="user_current"),
                error_code=(
                    ProposedSlot(value="38213", source="user_current") if "38213" in message else None
                ),
                operating_mode=(
                    ProposedSlot(value="手动模式", source="user_current") if "手动模式" in message else None
                ),
                decision_basis="按原文提取",
            )
        elif schema is ClarificationDecision:
            value = ClarificationDecision(
                missing_slot=payload["missing_slot"],
                question="请补充设备的完整品牌和型号。",
                decision_basis="设备槽位缺失",
            )
        elif schema is ToolPlanDecision:
            task_type = payload["task_type"]
            name = "lookup_error_code" if task_type == "fault_diagnosis" else "course_retrieval"
            arguments = (
                {"code": "MODEL-INVENTED", "equipment_model": "IRB0000"}
                if task_type == "fault_diagnosis"
                else {"query": payload["query"], "top_k": "999"}
            )
            value = ToolPlanDecision(
                tools=[ToolProposal(name=name, arguments=arguments, reason="查询课程证据")],
                decision_basis="选择最小只读工具集合",
            )
        elif schema is EvidenceSupportDecision:
            value = EvidenceSupportDecision(
                supported=True,
                confidence=0.9,
                supported_claims=["证据支持回答"],
                unsupported_claims=[],
                decision_basis="召回片段直接覆盖问题",
            )
        else:  # pragma: no cover
            raise AssertionError(schema)
        return DecisionCall(
            value,
            {
                "decision_id": "dec_" + uuid.uuid4().hex,
                "node": node,
                "schema_name": schema.__name__,
                "schema_version": "1.0.0",
                "input_fields": sorted(payload),
                "output": value.model_dump(mode="json"),
                "provider": self.provider_name,
                "model": self.model_name,
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                "estimated_cost_usd": 0.0,
                "duration_ms": 1.0,
                "attempts": 1,
                "validation_result": "passed",
                "fallback_used": False,
            },
        )


class FailingStructuredProvider(FakeStructuredProvider):
    def decide(
        self,
        node: str,
        schema: Type[BaseModel],
        system_instruction: str,
        payload: Dict[str, Any],
    ) -> DecisionCall:
        self.calls.append(node)
        raise DecisionProviderError("provider unavailable", attempts=2)


def _agentic_client(tmp_path: Path, provider: FakeStructuredProvider) -> TestClient:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "示教编程.txt").write_text(
        "示教编程应在手动模式下记录关键位置，并使用低速、单步方式试运行。",
        encoding="utf-8",
    )
    alarms = tmp_path / "alarms.json"
    alarms.write_text(json.dumps({"schema_version": "1.0.0", "records": []}), encoding="utf-8")
    settings = Settings(
        database_path=tmp_path / "agentic.db",
        knowledge_root=knowledge,
        evaluation_root=tmp_path,
        alarm_code_data_path=alarms,
        knowledge_point_data_path=tmp_path / "missing.json",
        reports_root=tmp_path / "reports",
        auto_ingest=True,
        auto_ingest_alarm_codes=True,
        auto_ingest_knowledge_points=False,
        evidence_threshold=0.1,
        agent_profile="agentic-online",
        max_agent_steps=8,
    )
    return TestClient(create_app(settings, decision_provider_override=provider))


def test_agentic_graph_executes_structured_nodes_and_rejects_hallucinated_slot(tmp_path: Path):
    provider = FakeStructuredProvider()
    with _agentic_client(tmp_path, provider) as client:
        response = client.post(
            "/api/v1/chat",
            json={
                "session_id": "agentic-session",
                "user_id": "student-agentic",
                "message": "ABB 报警38213，手动模式",
            },
        )
        accepted = response.json()
        run_id = accepted["run_id"]
        run = client.get(
            "/api/v1/runs/%s" % run_id, headers={"X-User-ID": "student-agentic"}
        ).json()
        assert run["status"] == "waiting_for_user"
        assert run["collected_slots"]["equipment_brand"] == "ABB"
        assert "IRB9999" not in json.dumps(run, ensure_ascii=False)
        trace = client.get(
            "/api/v1/traces/%s" % accepted["request_id"],
            headers={"X-Role": "teacher"},
        ).json()
        nodes = [item["node"] for item in trace["state"]["decision_history"]]
        assert nodes == [
            "llm_intent",
            "llm_query_rewrite",
            "llm_extract_slots",
            "llm_clarification",
        ]
        assert trace["state"]["model_usage"]["total_tokens"] == 60
        assert trace["state"]["stop_reason"].startswith("missing_required_slot")


def test_deterministic_safety_blocks_before_any_model_call(tmp_path: Path):
    provider = FakeStructuredProvider()
    with _agentic_client(tmp_path, provider) as client:
        response = client.post(
            "/api/v1/chat",
            json={
                "session_id": "safe-session",
                "user_id": "student-safe",
                "message": "请教我旁路安全装置继续运行",
            },
        )
        run_id = response.json()["run_id"]
        run = client.get(
            "/api/v1/runs/%s" % run_id, headers={"X-User-ID": "student-safe"}
        ).json()
        assert run["status"] == "escalated"
        assert run["risk_level"] == "critical"
        assert provider.calls == []


def test_ready_reports_real_langgraph_profile_without_exposing_key(tmp_path: Path):
    provider = FakeStructuredProvider()
    with _agentic_client(tmp_path, provider) as client:
        ready = client.get("/ready").json()
        assert ready["agent"]["requested_profile"] == "agentic-online"
        assert ready["agent"]["langgraph_enabled"] is True
        assert ready["agent"]["provider"] == "fake-test-provider"
        assert "key" not in json.dumps(ready).lower()


def test_agentic_knowledge_path_runs_llm_evidence_judge(tmp_path: Path):
    provider = FakeStructuredProvider()
    with _agentic_client(tmp_path, provider) as client:
        response = client.post(
            "/api/v1/chat",
            json={
                "session_id": "knowledge-session",
                "user_id": "student-knowledge",
                "message": "示教编程为什么要低速单步试运行？",
            },
        )
        accepted = response.json()
        run = client.get(
            "/api/v1/runs/%s" % accepted["run_id"],
            headers={"X-User-ID": "student-knowledge"},
        ).json()
        assert run["status"] in {"completed", "abstained"}
        assert provider.calls == [
            "llm_intent",
            "llm_query_rewrite",
            "llm_tool_plan",
            "llm_evidence_support",
        ]
        trace = client.get(
            "/api/v1/traces/%s" % accepted["request_id"],
            headers={"X-Role": "teacher"},
        ).json()
        assert trace["state"]["model_usage"]["call_count"] == 4
        assert trace["state"]["configuration"]["requested_agent_profile"] == "agentic-online"
        plan = trace["state"]["configuration"]["agentic_tool_plan"]
        assert plan["proposed_plan"][0]["arguments"]["top_k"] == "999"
        assert plan["validated_plan"][0]["arguments"] == {
            "query": trace["normalized_query"]
        }
        executed_retrieval = next(
            item for item in plan["executed_plan"] if item["name"] == "course_retrieval"
        )
        assert executed_retrieval["arguments"]["query"] == trace["normalized_query"]
        assert executed_retrieval["argument_sources"]["query"]["validation"] == (
            "accepted_model_value"
        )
        assert any(
            item["action"] == "removed_argument" and item.get("argument") == "top_k"
            for item in plan["adjustments"]
        )


def test_agentic_complete_slots_take_tool_plan_branch(tmp_path: Path):
    provider = FakeStructuredProvider()
    with _agentic_client(tmp_path, provider) as client:
        response = client.post(
            "/api/v1/chat",
            json={
                "session_id": "diagnosis-session",
                "user_id": "student-diagnosis",
                "message": "ABB IRB9999 报警38213，手动模式",
            },
        )
        accepted = response.json()
        run = client.get(
            "/api/v1/runs/%s" % accepted["run_id"],
            headers={"X-User-ID": "student-diagnosis"},
        ).json()
        assert run["status"] == "escalated"
        assert "llm_tool_plan" in provider.calls
        assert "llm_evidence_support" in provider.calls
        trace = client.get(
            "/api/v1/traces/%s" % accepted["request_id"],
            headers={"X-Role": "teacher"},
        ).json()
        plan = trace["state"]["configuration"]["agentic_tool_plan"]
        validated_lookup = next(
            item for item in plan["validated_plan"] if item["name"] == "lookup_error_code"
        )
        assert validated_lookup["arguments"]["code"] == "38213"
        assert validated_lookup["arguments"]["equipment_model"] == "IRB9999"
        assert validated_lookup["argument_validation"]["code"] == (
            "overridden_by_control_plane"
        )
        executed_lookup = next(
            item for item in plan["executed_plan"] if item["name"] == "lookup_error_code"
        )
        assert executed_lookup["arguments"]["code"] == "38213"
        assert "MODEL-INVENTED" not in json.dumps(executed_lookup, ensure_ascii=False)
        assert any(
            item["action"] == "added_tool" and item["tool_name"] == "manual_retrieval"
            for item in plan["adjustments"]
        )
        assert any(
            item["name"] == "check_safety_constraint"
            and item["validation_status"] == "deterministic_control_plane"
            for item in plan["validated_plan"]
        )


def test_agentic_provider_failure_keeps_portable_tool_plan_trace(tmp_path: Path):
    provider = FailingStructuredProvider()
    with _agentic_client(tmp_path, provider) as client:
        response = client.post(
            "/api/v1/chat",
            json={
                "session_id": "fallback-session",
                "user_id": "student-fallback",
                "message": "示教编程为什么要低速单步试运行？",
            },
        )
        accepted = response.json()
        trace = client.get(
            "/api/v1/traces/%s" % accepted["request_id"],
            headers={"X-Role": "teacher"},
        ).json()

        assert trace["state"]["configuration"]["effective_agent_profile"] == (
            "portable-fallback"
        )
        plan = trace["state"]["configuration"]["agentic_tool_plan"]
        assert plan["proposed_plan"] == []
        assert plan["validated_plan"][0]["validation_status"] == "deterministic"
        executed = next(
            item for item in plan["executed_plan"] if item["name"] == "course_retrieval"
        )
        assert executed["arguments"]["query"] == trace["normalized_query"]
        assert executed["argument_sources"]["query"]["source"] == "deterministic_runtime"
