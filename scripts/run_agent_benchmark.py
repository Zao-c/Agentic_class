"""Run one or more Agent designs on the exact same frozen task file."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import sys
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="在同一冻结任务集上运行 portable、自由 LLM Agent 与受控 LangGraph"
    )
    parser.add_argument(
        "--dataset",
        default="agent_benchmark_frozen_v0.1.json",
        help="data/eval 下的数据集文件名或绝对路径",
    )
    parser.add_argument(
        "--runner",
        choices=["portable", "free-llm-agent", "controlled-langgraph", "all"],
        default="portable",
    )
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument(
        "--controlled-profile",
        choices=["agentic-online", "agentic-quality"],
        default="agentic-online",
    )
    parser.add_argument("--include-binary", action="store_true")
    parser.add_argument(
        "--formal-comparison",
        action="store_true",
        help="require all three runners, >=3 repetitions and fail-closed controlled execution",
    )
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def _resolve_dataset(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / "data" / "eval" / path
    path = path.resolve()
    evaluation_root = (PROJECT_ROOT / "data" / "eval").resolve()
    if path != evaluation_root and evaluation_root not in path.parents:
        raise ValueError("dataset 必须位于 data/eval")
    return path


def _sha256_path(path: Path) -> str | None:
    path = path.resolve()
    if not path.exists():
        return None
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
        return digest.hexdigest()
    for item in sorted((entry for entry in path.rglob("*") if entry.is_file())):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _experiment_metadata(settings: Any, include_binary: bool) -> Dict[str, Any]:
    safe_configuration = {
        "agent_profile": settings.agent_profile,
        "retrieval_strategy": settings.retrieval_strategy,
        "retrieval_top_k": settings.retrieval_top_k,
        "retrieval_candidate_k": settings.retrieval_candidate_k,
        "evidence_threshold": settings.evidence_threshold,
        "max_agent_steps": settings.max_agent_steps,
        "max_retries": settings.max_retries,
        "tool_timeout_seconds": settings.tool_timeout_seconds,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "llm_timeout_seconds": settings.llm_timeout_seconds,
        "llm_max_retries": settings.llm_max_retries,
        "llm_structured_output_method": settings.llm_structured_output_method,
        "agentic_fallback_to_portable": settings.agentic_fallback_to_portable,
        "include_binary": include_binary,
    }
    configuration_bytes = json.dumps(
        safe_configuration, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    model_bytes = json.dumps(
        {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "structured_output_method": settings.llm_structured_output_method,
        },
        sort_keys=True,
    ).encode("utf-8")
    return {
        "corpus_sha256": _sha256_path(settings.knowledge_root),
        "alarm_codes_sha256": _sha256_path(settings.alarm_code_data_path),
        "knowledge_points_sha256": _sha256_path(settings.knowledge_point_data_path),
        "configuration_sha256": hashlib.sha256(configuration_bytes).hexdigest(),
        "model_configuration_sha256": hashlib.sha256(model_bytes).hexdigest(),
        "configuration": safe_configuration,
        "runner_protocol": {
            "turn_execution": "sequential_prefix_history",
            "free_agent_visible_tools": [
                "check_safety_constraint",
                "course_retrieval",
                "generate_exercise",
                "get_student_profile",
                "identify_weak_topics",
                "lookup_error_code",
                "manual_retrieval",
                "record_diagnostic_state",
            ],
            "free_agent_executable_tools": [
                "course_retrieval",
                "lookup_error_code",
                "manual_retrieval",
            ],
            "free_agent_output": "isolated_not_student_facing",
            "citations": "derived_from_executed_tool_results",
        },
        "remote_model_weights_pinned": False,
    }


class ConcreteReadOnlyToolbox:
    def __init__(self, retriever: Any, alarm_codes: Any):
        self.retriever = retriever
        self.alarm_codes = alarm_codes

    def clear_fixtures(self) -> None:
        store = self.retriever.store
        with store._lock, store._connect() as connection:
            rows = connection.execute(
                "SELECT document_id FROM documents WHERE metadata_json LIKE ?",
                ('%"benchmark_fixture"%',),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "DELETE FROM chunks WHERE document_id=?", (row["document_id"],)
                )
                connection.execute(
                    "DELETE FROM documents WHERE document_id=?", (row["document_id"],)
                )
        self.retriever._chunks_cache = None
        self.retriever._chunks_cache_count = -1

    def import_fixture(self, case_id: str, fixture: Any) -> None:
        self.retriever.import_text(
            fixture.title,
            fixture.content,
            fixture.document_type,
            metadata={"benchmark_fixture": case_id, "access_scope": "public"},
        )

    def call(self, name: str, arguments: Dict[str, str]) -> Any:
        if name in {"course_retrieval", "manual_retrieval"}:
            query = arguments.get("query") or arguments.get("normalized_query")
            if not query:
                raise ValueError("检索工具缺少 query")
            return [
                item.model_dump(mode="json")
                for item in self.retriever.search(query, top_k=5)
            ]
        if name == "lookup_error_code":
            return self.alarm_codes.lookup(
                arguments.get("code") or arguments.get("error_code", ""),
                arguments.get("equipment_brand"),
                arguments.get("equipment_model"),
                arguments.get("controller_version"),
            )
        raise PermissionError("工具不在隔离只读允许列表")


def main() -> None:
    args = _parse_args()
    dataset_path = _resolve_dataset(args.dataset)
    if args.formal_comparison and args.runner != "all":
        raise SystemExit("--formal-comparison requires --runner all")
    if args.formal_comparison and args.repetitions < 3:
        raise SystemExit("--formal-comparison requires --repetitions >= 3")
    needs_llm = args.runner in {"free-llm-agent", "controlled-langgraph", "all"}

    # Profile must be loaded before importing Settings because its dataclass
    # defaults are environment-derived at module import time.
    from scripts.run_profile import load_profile

    load_profile(args.controlled_profile if needs_llm else "portable")
    from scripts.agent_benchmark import (
        BenchmarkValidationError,
        FreeLLMAgentRunner,
        WorkflowBenchmarkRunner,
        assert_formal_dataset_eligible,
        load_dataset,
        run_benchmark,
        write_report,
    )

    dataset = load_dataset(dataset_path)
    if args.formal_comparison:
        try:
            assert_formal_dataset_eligible(dataset)
        except BenchmarkValidationError as exc:
            raise SystemExit(str(exc)) from None
    if needs_llm:
        key_env = os.environ.get("LLM_API_KEY_ENV", "OPENAI_API_KEY")
        if not os.environ.get(key_env):
            os.environ[key_env] = getpass.getpass(
                "%s（只保存在当前进程，不写入报告）: " % key_env
            )

    from app.agentic_graph import ControlledAgentGraph
    from app.alarm_codes import AlarmCodeService
    from app.config import Settings
    from app.decision_provider import build_decision_provider
    from app.retrieval import Retriever
    from app.runtime_dirs import isolated_directory
    from app.storage import Store
    from app.tutoring import TutoringService
    from app.workflow import AgentWorkflow
    base_settings = Settings()
    if args.formal_comparison:
        base_settings = replace(base_settings, agentic_fallback_to_portable=False)
    experiment_metadata = _experiment_metadata(base_settings, args.include_binary)
    selected = (
        ["portable", "free-llm-agent", "controlled-langgraph"]
        if args.runner == "all"
        else [args.runner]
    )
    with ExitStack() as stack:
        runners: List[Any] = []

        def resources(label: str, settings: Settings):
            temporary = stack.enter_context(
                isolated_directory(PROJECT_ROOT / "runtime" / "benchmark-runs", label + "-")
            )
            runtime_settings = replace(
                settings,
                database_path=temporary / "benchmark.db",
                reports_root=temporary / "reports",
                auto_ingest=False,
                auto_ingest_alarm_codes=False,
                auto_ingest_knowledge_points=False,
            )
            runtime_settings.ensure_directories()
            store = Store(runtime_settings.database_path)
            retriever = Retriever(store, runtime_settings)
            retriever.import_directory(
                runtime_settings.knowledge_root, include_binary=args.include_binary
            )
            alarm_codes = AlarmCodeService(store)
            alarm_codes.import_file(runtime_settings.alarm_code_data_path)
            tutoring = TutoringService(store, retriever)
            if runtime_settings.knowledge_point_data_path.exists():
                tutoring.import_file(runtime_settings.knowledge_point_data_path)
            return runtime_settings, store, retriever, alarm_codes, tutoring

        if "portable" in selected:
            portable = replace(
                base_settings,
                agent_profile="portable",
                retrieval_strategy="hybrid_rerank",
            )
            runtime, store, retriever, alarms, tutoring = resources("portable", portable)
            workflow = AgentWorkflow(store, retriever, alarms, tutoring, runtime)
            runners.append(WorkflowBenchmarkRunner("portable", store, workflow))

        provider = build_decision_provider(base_settings) if needs_llm else None
        if "free-llm-agent" in selected:
            runtime, store, retriever, alarms, tutoring = resources("free-agent", base_settings)
            runners.append(
                FreeLLMAgentRunner(provider, ConcreteReadOnlyToolbox(retriever, alarms))
            )

        if "controlled-langgraph" in selected:
            runtime, store, retriever, alarms, tutoring = resources("controlled", base_settings)
            graph = ControlledAgentGraph(provider, runtime)
            workflow = AgentWorkflow(
                store, retriever, alarms, tutoring, runtime, agentic_graph=graph
            )
            runners.append(
                WorkflowBenchmarkRunner("controlled-langgraph", store, workflow)
            )

        report = run_benchmark(
            dataset,
            runners,
            args.repetitions,
            dataset_path=dataset_path,
            formal_comparison=args.formal_comparison,
            experiment_metadata=experiment_metadata,
        )

    output = args.report or (
        PROJECT_ROOT
        / "reports"
        / (
            "agent_comparison_%s.json"
            % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        )
    )
    write_report(report, output)
    print(
        json.dumps(
            {
                "evaluation_run_id": report["evaluation_run_id"],
                "protocol_version": report["protocol_version"],
                "experiment_status": report["experiment_status"],
                "formal_comparison": report["formal_comparison"],
                "dataset": report["dataset"],
                "repetitions": report["repetitions"],
                "metrics": {
                    item["runner"]: item["metrics"] for item in report["runner_reports"]
                },
                "comparison_eligible": {
                    item["runner"]: item["comparison_eligible"]
                    for item in report["runner_reports"]
                },
                "experiment_fingerprints": {
                    key: value
                    for key, value in report["experiment_metadata"].items()
                    if key.endswith("sha256")
                },
                "claim_boundary": report["claim_boundary"],
                "report": str(output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
