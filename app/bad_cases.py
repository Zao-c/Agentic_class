import hashlib
import json
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.alarm_codes import AlarmCodeService
from app.config import Settings
from app.retrieval import Retriever
from app.runtime_dirs import isolated_directory
from app.schemas import AgentState, BadCaseReviewRequest, RetrievalStrategy
from app.storage import Store
from app.tutoring import TutoringService
from app.workflow import AgentWorkflow


class BadCaseService:
    def __init__(self, store: Store, settings: Settings):
        self.store = store
        self.settings = settings

    def detail(self, bad_case_id: str) -> Dict[str, Any]:
        detail = self.store.bad_case_detail(bad_case_id)
        if not detail:
            raise KeyError(bad_case_id)
        return detail

    def review(self, bad_case_id: str, request: BadCaseReviewRequest) -> Dict[str, Any]:
        assertions = request.model_dump(mode="json", exclude={"status"})
        result = self.store.save_bad_case_assertions(
            bad_case_id, assertions, request.status
        )
        result["assertions"] = assertions
        return result

    def export_package(self, bad_case_id: str) -> Dict[str, Any]:
        detail = self.detail(bad_case_id)
        trace = detail["trace"]
        explicit = detail.get("assertions")
        assertions = self.store._redact_value(self._default_assertions(trace, explicit))
        history = self.store.prior_session_messages(detail["run_id"])
        package = {
            "schema_version": "1.0.0",
            "package_type": "courseops_bad_case",
            "bad_case_id": bad_case_id,
            "source": {
                "request_id": detail["request_id"],
                "run_id": detail["run_id"],
                "reason": detail["reason"],
                "tags": detail["tags"],
                "status": detail["status"],
                "created_at": detail["created_at"],
            },
            "input": {
                "prior_messages": history,
                "message": detail["message"],
            },
            "original_result": {
                "task_type": trace["task_type"],
                "status": trace["status"],
                "risk_level": trace["state"]["risk_level"],
                "answer": detail.get("answer"),
                "citation_count": len(trace["state"].get("retrieved_evidence", [])),
            },
            "feedback": self.store._redact_value(detail.get("feedback")),
            "assertions": assertions,
            "configuration": trace["state"].get("configuration", {}),
            "trace": trace,
            "exported_at": trace["exported_at"],
        }
        hash_payload = {
            "bad_case_id": bad_case_id,
            "input": package["input"],
            "assertions": assertions,
            "configuration": package["configuration"],
            "state": trace["state"],
            "events": trace["events"],
        }
        canonical = json.dumps(hash_payload, ensure_ascii=False, sort_keys=True)
        package["package_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return package

    @staticmethod
    def _default_assertions(
        trace: Dict[str, Any], explicit: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        assertions = {
            "must_not_fail": True,
            "expected_status": None,
            "expected_task_type": trace["task_type"],
            "expected_risk_level": (
                trace["state"]["risk_level"]
                if trace["state"]["risk_level"] in {"high", "critical"}
                else None
            ),
            "answer_must_contain": [],
            "answer_must_not_contain": [],
            "require_citations": None,
            "review_note": None,
        }
        if explicit:
            for key in assertions:
                if key in explicit and explicit[key] is not None:
                    assertions[key] = explicit[key]
        return assertions

    def replay(self, bad_case_id: str) -> Dict[str, Any]:
        return self.replay_package(self.export_package(bad_case_id))

    def replay_package(self, package: Dict[str, Any]) -> Dict[str, Any]:
        strategy = package.get("configuration", {}).get(
            "retrieval_strategy", self.settings.retrieval_strategy
        )
        try:
            RetrievalStrategy(strategy)
        except ValueError:
            strategy = self.settings.retrieval_strategy

        with isolated_directory(
            self.settings.database_path.parent / "replays", "bad-case-"
        ) as temporary:
            replay_settings = replace(
                self.settings,
                database_path=temporary / "replay.db",
                retrieval_strategy=strategy,
                agent_profile="portable",
                auto_ingest=False,
                auto_ingest_alarm_codes=False,
            )
            replay_store = Store(replay_settings.database_path)
            self._copy_knowledge(replay_store)
            retriever = Retriever(replay_store, replay_settings)
            alarm_codes = AlarmCodeService(replay_store)
            tutoring = TutoringService(replay_store, retriever)
            current_alarm_records = self.store.list_alarm_codes(limit=10000)
            if current_alarm_records:
                replay_store.upsert_alarm_codes(current_alarm_records)
            current_points = self.store.list_knowledge_points(include_inactive=True)
            if current_points:
                replay_store.upsert_knowledge_points(current_points)
            if strategy.startswith("neural_"):
                retriever.prepare(strategy)
            workflow = AgentWorkflow(
                replay_store, retriever, alarm_codes, tutoring, replay_settings
            )

            session_id = "replay-" + uuid.uuid4().hex
            user_id = "courseops-replay"
            for message in package["input"].get("prior_messages", []):
                self._run_message(replay_store, workflow, session_id, user_id, message)
            final = self._run_message(
                replay_store,
                workflow,
                session_id,
                user_id,
                package["input"]["message"],
            )

        checks = self._evaluate_assertions(final, package["assertions"])
        return {
            "replay_id": "replay_" + uuid.uuid4().hex,
            "bad_case_id": package["bad_case_id"],
            "package_hash": package["package_hash"],
            "passed": all(check["passed"] for check in checks),
            "checks": checks,
            "result": {
                "task_type": final.task_type.value,
                "status": final.final_status.value,
                "risk_level": final.risk_level.value,
                "answer": final.answer,
                "citation_count": len(final.retrieved_evidence),
                "tool_count": len(final.tool_history),
            },
            "configuration": {"retrieval_strategy": strategy},
        }

    def _copy_knowledge(self, target: Store) -> None:
        chunks_by_document: Dict[str, List[Dict[str, Any]]] = {}
        for chunk in self.store.active_chunks():
            chunks_by_document.setdefault(chunk["document_id"], []).append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "chunk_index": chunk["chunk_index"],
                    "content": chunk["content"],
                    "tokens": chunk["tokens"],
                    "metadata": chunk["metadata"],
                }
            )
        for document in self.store.list_documents():
            if not document["is_active"] or document["document_id"] not in chunks_by_document:
                continue
            target.add_document(
                document["document_id"],
                document["title"],
                document["source_path"],
                document["document_type"],
                document["metadata"],
                document["content_hash"],
                sorted(
                    chunks_by_document[document["document_id"]],
                    key=lambda item: item["chunk_index"],
                ),
            )

    @staticmethod
    def _run_message(
        store: Store,
        workflow: AgentWorkflow,
        session_id: str,
        user_id: str,
        message: str,
    ) -> AgentState:
        state = AgentState(
            request_id="req_" + uuid.uuid4().hex,
            run_id="run_" + uuid.uuid4().hex,
            session_id=session_id,
            user_id=user_id,
            original_message=message,
        )
        store.create_run(state)
        return workflow.run(state)

    @staticmethod
    def _evaluate_assertions(
        state: AgentState, assertions: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        checks = []

        def add(name: str, expected: Any, actual: Any, passed: bool) -> None:
            checks.append(
                {"name": name, "expected": expected, "actual": actual, "passed": passed}
            )

        if assertions.get("must_not_fail"):
            add("must_not_fail", True, state.final_status.value, state.final_status.value != "failed")
        for key, actual in (
            ("expected_status", state.final_status.value),
            ("expected_task_type", state.task_type.value),
            ("expected_risk_level", state.risk_level.value),
        ):
            expected = assertions.get(key)
            if expected is not None:
                add(key, expected, actual, actual == expected)
        answer = state.answer or ""
        for phrase in assertions.get("answer_must_contain", []):
            add("answer_must_contain", phrase, phrase in answer, phrase in answer)
        for phrase in assertions.get("answer_must_not_contain", []):
            add("answer_must_not_contain", phrase, phrase in answer, phrase not in answer)
        if assertions.get("require_citations") is not None:
            actual = bool(state.retrieved_evidence)
            add("require_citations", assertions["require_citations"], actual, actual == assertions["require_citations"])
        return checks

    def promote(self, bad_case_id: str) -> Dict[str, Any]:
        detail = self.detail(bad_case_id)
        if not detail.get("assertions"):
            raise ValueError("bad case 必须先由教师填写回归断言")
        package = self.export_package(bad_case_id)
        regression_case_id = "reg_" + hashlib.sha256(
            bad_case_id.encode("utf-8")
        ).hexdigest()[:24]
        return self.store.save_regression_case(
            regression_case_id, bad_case_id, package
        )

    def run_regressions(self, limit: Optional[int] = None) -> Dict[str, Any]:
        cases = self.store.list_regression_cases(limit or 500)
        results = []
        for case in cases:
            replay = self.replay_package(case["package"])
            results.append(
                {
                    "regression_case_id": case["regression_case_id"],
                    "bad_case_id": case["bad_case_id"],
                    "passed": replay["passed"],
                    "checks": replay["checks"],
                    "result": replay["result"],
                }
            )
        now = datetime.now(timezone.utc)
        run_id = "regression_%s_%s" % (
            now.strftime("%Y%m%dT%H%M%SZ"),
            uuid.uuid4().hex[:8],
        )
        passed = sum(item["passed"] for item in results)
        report = {
            "schema_version": "1.0.0",
            "regression_run_id": run_id,
            "case_count": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "pass_rate": round(passed / len(results), 4) if results else None,
            "cases": results,
            "created_at": now.isoformat(),
        }
        self.settings.reports_root.mkdir(parents=True, exist_ok=True)
        json_path = self.settings.reports_root / (run_id + ".json")
        markdown_path = self.settings.reports_root / (run_id + ".md")
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(
            "\n".join(
                [
                    "# Bad case 自动回归",
                    "",
                    "- 运行 ID：`%s`" % run_id,
                    "- 用例数：%d" % len(results),
                    "- 通过：%d" % passed,
                    "- 失败：%d" % (len(results) - passed),
                    "- 通过率：%s" % report["pass_rate"],
                ]
            ),
            encoding="utf-8",
        )
        report["report_files"] = [str(json_path), str(markdown_path)]
        return report
