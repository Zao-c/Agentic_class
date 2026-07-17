"""Contract tests for the fair-comparison Benchmark protocol v2.

These tests intentionally describe the next protocol boundary.  They keep all
providers and tools local so they test orchestration and reporting rather than
network availability.
"""

import json
import sys
from dataclasses import replace
from typing import Any, Dict, Type

import pytest
from pydantic import BaseModel

from app.decision_provider import DecisionCall
from app.config import Settings
from app.schemas import RunStatus, TaskType
from scripts.agent_benchmark import (
    BenchmarkCase,
    BenchmarkDataset,
    BenchmarkValidationError,
    FreeAgentFinal,
    FreeAgentPlan,
    FreeLLMAgentRunner,
    RunnerObservation,
    aggregate_runner,
    run_benchmark,
)
from scripts.run_agent_benchmark import _experiment_metadata, main as benchmark_main


def _case(case_id: str = "V2-1") -> BenchmarkCase:
    return BenchmarkCase.model_validate(
        {
            "id": case_id,
            "category": "protocol-v2",
            "turns": ["第一轮：工具坐标系是什么？", "最后一轮：请查手册补充说明。"],
            "expected": {
                "task_type": "knowledge_qa",
                "normalized_query_must_include": ["最后一轮"],
                "slots": {},
                "forbidden_slot_values": [],
                "tools": ["manual_retrieval"],
                "final_status": "completed",
                "citation_titles_any": ["最后一轮真实资料"],
                "citation_required": True,
                "refusal": False,
                "safety_escalation": False,
            },
        }
    )


class _PerTurnProvider:
    provider_name = "fake"
    model_name = "fake-per-turn"

    def __init__(self):
        self.plan_calls = 0
        self.final_calls = 0

    @staticmethod
    def _current_turn(payload: Dict[str, Any]) -> str:
        for key in ("message", "current_turn", "turn"):
            if payload.get(key):
                return str(payload[key])
        turns = payload.get("turns", [])
        return str(turns[-1]) if turns else ""

    def decide(
        self,
        node: str,
        schema: Type[BaseModel],
        system_instruction: str,
        payload: Dict[str, Any],
    ) -> DecisionCall:
        current_turn = self._current_turn(payload)
        if schema is FreeAgentPlan:
            self.plan_calls += 1
            tool_name = (
                "manual_retrieval" if "最后一轮" in current_turn else "course_retrieval"
            )
            value = FreeAgentPlan.model_validate(
                {
                    "task_type": "knowledge_qa",
                    "normalized_query": current_turn,
                    "collected_slots": {},
                    "tools": [
                        {
                            "name": tool_name,
                            "arguments": {"query": current_turn},
                            "reason": "逐轮检索",
                        }
                    ],
                    "decision_basis": "只处理当前轮并参考此前历史",
                }
            )
        else:
            assert schema is FreeAgentFinal
            self.final_calls += 1
            value = FreeAgentFinal(
                final_status=RunStatus.completed,
                answer="已根据工具证据回答。",
                citation_titles=["模型自报的伪造标题"],
                refusal=False,
                safety_escalation=False,
                decision_basis="工具已返回结果",
            )
        return DecisionCall(
            value=value,
            trace={
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                "estimated_cost_usd": 0.001,
                "fallback_used": False,
            },
        )


class _EvidenceToolbox:
    def __init__(self):
        self.calls = []

    def call(self, name: str, arguments: Dict[str, str]):
        self.calls.append((name, arguments))
        title = (
            "最后一轮真实资料" if name == "manual_retrieval" else "第一轮真实资料"
        )
        return [{"title": title, "excerpt": "来自实际工具结果的证据"}]


def test_free_agent_runs_plan_and_final_per_turn_and_accumulates_usage():
    provider = _PerTurnProvider()
    toolbox = _EvidenceToolbox()
    observation = FreeLLMAgentRunner(provider, toolbox).run_case(_case(), 1)

    assert provider.plan_calls == 2
    assert provider.final_calls == 2
    assert [name for name, _ in toolbox.calls] == [
        "course_retrieval",
        "manual_retrieval",
    ]
    assert observation.executed_tools == ["manual_retrieval"]
    assert observation.proposed_tools == ["manual_retrieval"]
    assert observation.total_tokens == 60
    assert observation.estimated_cost_usd == 0.004


def test_free_agent_citations_are_grounded_in_final_turn_tool_results():
    provider = _PerTurnProvider()
    observation = FreeLLMAgentRunner(provider, _EvidenceToolbox()).run_case(_case(), 1)

    assert observation.citation_titles == ["最后一轮真实资料"]
    assert observation.metadata["model_reported_citation_titles"] == [
        "模型自报的伪造标题"
    ]
    assert "模型自报的伪造标题" not in observation.citation_titles


def _observation(*, fallback_used: bool) -> RunnerObservation:
    return RunnerObservation(
        runner="controlled-langgraph",
        case_id="V2-1",
        repetition=2 if fallback_used else 1,
        task_type=TaskType.knowledge_qa,
        normalized_query="最后一轮：请查手册补充说明。",
        executed_tools=["manual_retrieval"],
        final_status=RunStatus.completed,
        citation_titles=["最后一轮真实资料"],
        answer="已根据资料回答。",
        refusal=False,
        safety_escalation=False,
        latency_ms=20.0 if fallback_used else 10.0,
        fallback_used=fallback_used,
    )


def test_controlled_fallback_is_ineligible_and_metrics_are_separated():
    case = _case()
    report = aggregate_runner(
        "controlled-langgraph",
        {case.id: case},
        [_observation(fallback_used=False), _observation(fallback_used=True)],
    )

    assert report["comparison_eligible"] is False
    assert report["metrics"]["sample_count"] == 2
    assert report["clean_metrics"]["sample_count"] == 1
    assert report["fallback_metrics"]["sample_count"] == 1
    repetitions = {item["repetition"]: item for item in report["repetition_reports"]}
    assert repetitions[1]["comparison_eligible"] is True
    assert repetitions[1]["fallback_case_ids"] == []
    assert repetitions[2]["comparison_eligible"] is False
    assert repetitions[2]["fallback_case_ids"] == ["V2-1"]


class _FailedRunner:
    def __init__(self, name: str):
        self.name = name

    def run_case(self, case: BenchmarkCase, repetition: int) -> RunnerObservation:
        return RunnerObservation(
            runner=self.name,
            case_id=case.id,
            repetition=repetition,
            task_type=TaskType.other,
            normalized_query=case.turns[-1],
            final_status=RunStatus.failed,
            answer="隔离运行失败。",
            refusal=True,
            safety_escalation=False,
            latency_ms=1.0,
            fallback_used=True,
            error="provider unavailable",
        )


def _dataset() -> BenchmarkDataset:
    return BenchmarkDataset(
        schema_version="1.0.0",
        dataset_id="protocol-v2-test",
        version="v0.2-test",
        status="frozen_engineering_validation",
        teacher_reviewed=False,
        disclaimer="test only",
        cases=[_case()],
    )


def _synthetic_dataset() -> BenchmarkDataset:
    return BenchmarkDataset(
        schema_version="1.0.0",
        dataset_id="synthetic-classroom-test",
        version="v1-test",
        status="synthetic_engineering_validation",
        teacher_reviewed=False,
        data_origin="synthetic_public",
        actor_mode="simulated",
        label_authority="simulation",
        metric_eligibility="synthetic_engineering_only",
        formal_comparison_eligible=False,
        disclaimer="simulated teacher and student; engineering use only",
        cases=[_case()],
    )


def _gold_dataset() -> BenchmarkDataset:
    return BenchmarkDataset(
        schema_version="1.0.0",
        dataset_id="verified-gold-test",
        version="v1-test",
        status="teacher_reviewed_gold",
        teacher_reviewed=True,
        data_origin="real_course_data",
        actor_mode="human_or_unknown",
        label_authority="verified_human_teacher",
        metric_eligibility="formal_gold",
        formal_comparison_eligible=True,
        disclaimer="verified Gold test fixture",
        cases=[_case()],
    )


def _three_failed_runners():
    return [
        _FailedRunner("portable"),
        _FailedRunner("free-llm-agent"),
        _FailedRunner("controlled-langgraph"),
    ]


def test_formal_comparison_rejects_engineering_and_synthetic_before_running_cases():
    runner = _FailedRunner("portable")
    for dataset in (_dataset(), _synthetic_dataset()):
        with pytest.raises(BenchmarkValidationError, match="verified, non-synthetic"):
            run_benchmark(
                dataset,
                [runner],
                repetitions=3,
                formal_comparison=True,
            )


def test_formal_gold_requires_all_three_runners_and_three_repetitions():
    with pytest.raises(BenchmarkValidationError):
        run_benchmark(
            _gold_dataset(),
            [_FailedRunner("portable")],
            repetitions=3,
            formal_comparison=True,
        )

    with pytest.raises(BenchmarkValidationError):
        run_benchmark(
            _gold_dataset(),
            _three_failed_runners(),
            repetitions=2,
            formal_comparison=True,
        )


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"teacher_reviewed": True}, "simulation-only"),
        ({"status": "teacher_reviewed_gold"}, "simulation-only"),
        ({"metric_eligibility": "formal_gold"}, "simulation-only"),
        ({"formal_comparison_eligible": True}, "simulation-only"),
    ],
)
def test_synthetic_identity_cannot_be_promoted_by_flipping_gold_fields(updates, message):
    payload = _synthetic_dataset().model_dump(mode="json")
    payload.update(updates)

    with pytest.raises(ValueError, match=message):
        BenchmarkDataset.model_validate(payload)


def test_legacy_engineering_dataset_defaults_remain_non_formal():
    dataset = _dataset()

    assert dataset.data_origin == "engineering_source"
    assert dataset.actor_mode == "human_or_unknown"
    assert dataset.label_authority == "engineering_spec"
    assert dataset.metric_eligibility == "engineering_only"
    assert dataset.formal_comparison_eligible is False


def test_all_failed_formal_run_is_not_completed_and_preserves_metadata():
    metadata = {
        "protocol_version": "0.2",
        "provider": {"name": "fake", "model": "fake-v2", "temperature": 0},
        "artifacts": {"knowledge_sha256": "knowledge-hash", "alarm_sha256": "alarm-hash"},
    }
    report = run_benchmark(
        _gold_dataset(),
        _three_failed_runners(),
        repetitions=3,
        formal_comparison=True,
        experiment_metadata=metadata,
    )

    assert report["experiment_status"] != "completed"
    assert report["formal_comparison"] is True
    assert report["dataset"]["status"] == "teacher_reviewed_gold"
    assert report["dataset"]["metric_eligibility"] == "formal_gold"
    assert report["experiment_metadata"] == metadata


def test_cli_rejects_partial_formal_comparison_before_requesting_a_key(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_agent_benchmark.py",
            "--formal-comparison",
            "--runner",
            "portable",
            "--repetitions",
            "3",
        ],
    )
    with pytest.raises(SystemExit, match="requires --runner all"):
        benchmark_main()


def test_cli_rejects_engineering_formal_dataset_before_requesting_a_key(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_agent_benchmark.py",
            "--formal-comparison",
            "--runner",
            "all",
            "--repetitions",
            "3",
            "--dataset",
            "agent_benchmark_frozen_v0.1.json",
        ],
    )
    monkeypatch.setattr(
        "scripts.run_agent_benchmark.getpass.getpass",
        lambda *_args, **_kwargs: pytest.fail("API key prompt must not run for non-Gold data"),
    )

    with pytest.raises(SystemExit, match="verified, non-synthetic"):
        benchmark_main()


def test_cli_rejects_missing_knowledge_root_before_running_cases(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_agent_benchmark.py",
            "--runner",
            "portable",
            "--knowledge-root",
            str(tmp_path / "missing-corpus"),
        ],
    )

    with pytest.raises(SystemExit, match="existing directory"):
        benchmark_main()


def test_experiment_metadata_fingerprints_inputs_without_secrets(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "sample.txt").write_text("public fixture", encoding="utf-8")
    alarms = tmp_path / "alarms.json"
    alarms.write_text('{"records": []}', encoding="utf-8")
    knowledge_points = tmp_path / "knowledge.json"
    knowledge_points.write_text('{"knowledge_points": []}', encoding="utf-8")
    settings = replace(
        Settings(),
        knowledge_root=corpus,
        alarm_code_data_path=alarms,
        knowledge_point_data_path=knowledge_points,
    )

    metadata = _experiment_metadata(settings, include_binary=False)

    assert len(metadata["corpus_sha256"]) == 64
    assert len(metadata["alarm_codes_sha256"]) == 64
    assert len(metadata["knowledge_points_sha256"]) == 64
    assert len(metadata["configuration_sha256"]) == 64
    assert len(metadata["model_configuration_sha256"]) == 64
    assert "api_key" not in json.dumps(metadata).lower()
