import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional
from urllib.parse import quote

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings, settings as default_settings
from app.agentic_graph import ControlledAgentGraph
from app.decision_provider import DecisionProvider, build_decision_provider
from app.alarm_codes import AlarmCodeService, normalize_code
from app.bad_cases import BadCaseService
from app.evaluation import EvaluationService
from app.diagnostic_evaluation import DiagnosticEvaluationService
from app.retrieval import Retriever, ensure_under_root
from app.schemas import (
    AgentState,
    AlarmCodeImportRequest,
    BadCaseReviewRequest,
    ChatRequest,
    DocumentImportRequest,
    DiagnosticEvaluationRequest,
    EvaluationRequest,
    ExerciseGenerateRequest,
    ExerciseSubmitRequest,
    FeedbackRequest,
    KnowledgePointImportRequest,
    RegressionRunRequest,
    RunAccepted,
    RunStatus,
    RunView,
    TERMINAL_STATUSES,
    TutoringEvaluationRequest,
)
from app.storage import Store
from app.tutoring import TutoringService
from app.tutoring_evaluation import TutoringEvaluationService
from app.workflow import AgentWorkflow


def create_app(
    settings: Settings = default_settings,
    decision_provider_override: Optional[DecisionProvider] = None,
) -> FastAPI:
    settings.ensure_directories()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(settings.database_path)
        retriever = Retriever(store, settings)
        alarm_codes = AlarmCodeService(store)
        tutoring = TutoringService(store, retriever)
        decision_provider = (
            decision_provider_override
            if decision_provider_override is not None
            else build_decision_provider(settings)
        )
        agentic_graph = (
            ControlledAgentGraph(decision_provider, settings) if decision_provider else None
        )
        workflow = AgentWorkflow(
            store, retriever, alarm_codes, tutoring, settings, agentic_graph=agentic_graph
        )
        evaluator = EvaluationService(
            retriever,
            settings.evaluation_root,
            settings.reports_root,
            settings.evidence_threshold,
        )
        app.state.settings = settings
        app.state.store = store
        app.state.retriever = retriever
        app.state.alarm_codes = alarm_codes
        app.state.tutoring = tutoring
        app.state.decision_provider = decision_provider
        app.state.agentic_graph = agentic_graph
        app.state.bad_cases = BadCaseService(store, settings)
        app.state.workflow = workflow
        app.state.evaluator = evaluator
        app.state.diagnostic_evaluator = DiagnosticEvaluationService(settings)
        app.state.tutoring_evaluator = TutoringEvaluationService(settings)
        app.state.ingestion_result = None
        app.state.alarm_ingestion_result = None
        app.state.knowledge_point_ingestion_result = None
        if settings.auto_ingest and settings.knowledge_root.exists():
            app.state.ingestion_result = retriever.import_directory(
                settings.knowledge_root,
                include_binary=settings.ingest_binary_documents,
            )
        if settings.auto_ingest_alarm_codes and settings.alarm_code_data_path.exists():
            app.state.alarm_ingestion_result = alarm_codes.import_file(
                settings.alarm_code_data_path
            )
        if (
            settings.auto_ingest_knowledge_points
            and settings.knowledge_point_data_path.exists()
        ):
            app.state.knowledge_point_ingestion_result = tutoring.import_file(
                settings.knowledge_point_data_path
            )
        if settings.retrieval_strategy.startswith("neural_"):
            retriever.prepare(settings.retrieval_strategy)
        yield

    app = FastAPI(
        title=settings.app_name,
        version="0.5.0",
        description="面向工业机器人教学的可评测、可观测、受控 Agent 平台。",
        lifespan=lifespan,
    )
    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-User-ID", "X-Role"],
    )

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    def require_role(role: Optional[str], allowed: set) -> None:
        if role not in allowed:
            raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "当前角色无权执行该操作"})

    def owned_state(run_id: str, user_id: str) -> AgentState:
        state = app.state.store.get_state(run_id)
        if not state:
            raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND", "message": "运行记录不存在"})
        if state.user_id != user_id:
            raise HTTPException(status_code=403, detail={"code": "SESSION_ACCESS_DENIED", "message": "不能访问其他用户的运行记录"})
        return state

    def to_run_view(state: AgentState) -> RunView:
        return RunView(
            request_id=state.request_id,
            run_id=state.run_id,
            session_id=state.session_id,
            task_type=state.task_type,
            status=state.final_status,
            answer=state.answer,
            citations=state.retrieved_evidence,
            risk_level=state.risk_level,
            required_slots=state.required_slots,
            collected_slots=state.collected_slots,
            generated_exercise_id=state.generated_exercise_id,
            created_at=state.created_at,
            updated_at=state.updated_at,
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        return JSONResponse(
            status_code=422,
            content={"code": "INVALID_INPUT", "message": str(exc), "request_id": request.headers.get("X-Request-ID")},
        )

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(str(static_dir / "index.html"))

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": settings.app_name}

    @app.get("/ready")
    async def ready():
        neural_required = settings.retrieval_strategy.startswith("neural_")
        return {
            "status": "ready",
            "database": True,
            "indexed_chunks": app.state.store.count_chunks(),
            "startup_ingestion": app.state.ingestion_result,
            "alarm_codes": {
                "active_records": app.state.store.count_alarm_codes(),
                "startup_ingestion": app.state.alarm_ingestion_result,
            },
            "knowledge_points": {
                "active_records": len(app.state.store.list_knowledge_points()),
                "startup_ingestion": app.state.knowledge_point_ingestion_result,
            },
            "retrieval": {
                "strategy": settings.retrieval_strategy,
                "neural_required": neural_required,
                "hf_cache_exists": settings.hf_cache_dir.exists() if neural_required else None,
                "local_files_only": settings.neural_local_files_only if neural_required else None,
            },
            "agent": {
                "requested_profile": settings.agent_profile,
                "effective_profile": settings.agent_profile,
                "langgraph_enabled": app.state.agentic_graph is not None,
                "provider": (
                    app.state.decision_provider.provider_name
                    if app.state.decision_provider
                    else None
                ),
                "model": settings.llm_model if app.state.decision_provider else None,
                "fallback_to_portable": settings.agentic_fallback_to_portable,
            },
        }

    @app.post("/api/v1/chat", response_model=RunAccepted, status_code=status.HTTP_202_ACCEPTED)
    async def create_chat(payload: ChatRequest, background_tasks: BackgroundTasks):
        request_id = "req_" + uuid.uuid4().hex
        run_id = "run_" + uuid.uuid4().hex
        state = AgentState(
            request_id=request_id,
            run_id=run_id,
            session_id=payload.session_id,
            user_id=payload.user_id,
            original_message=payload.message,
            final_status=RunStatus.queued,
        )
        app.state.store.create_run(state)
        background_tasks.add_task(app.state.workflow.run, state)
        return RunAccepted(
            request_id=request_id,
            run_id=run_id,
            status=RunStatus.queued,
            stream_url="/api/v1/runs/%s/stream?user_id=%s" % (run_id, quote(payload.user_id)),
        )

    @app.get("/api/v1/runs/{run_id}", response_model=RunView)
    async def get_run(run_id: str, x_user_id: str = Header(..., alias="X-User-ID")):
        return to_run_view(owned_state(run_id, x_user_id))

    @app.get("/api/v1/runs/{run_id}/stream")
    async def stream_run(
        request: Request,
        run_id: str,
        user_id: str = Query(..., min_length=1),
        after: int = Query(default=0, ge=0),
    ):
        owned_state(run_id, user_id)

        async def event_generator() -> AsyncIterator[str]:
            sequence = after
            idle_ticks = 0
            while True:
                if await request.is_disconnected():
                    break
                events = app.state.store.get_events(run_id, sequence)
                for event in events:
                    sequence = event["sequence"]
                    yield "id: %d\nevent: %s\ndata: %s\n\n" % (
                        sequence,
                        event["event_type"],
                        json.dumps(event, ensure_ascii=False),
                    )
                run_status = app.state.store.run_status(run_id)
                if run_status in TERMINAL_STATUSES and not app.state.store.get_events(run_id, sequence):
                    yield "event: stream.closed\ndata: %s\n\n" % json.dumps(
                        {"run_id": run_id, "status": run_status}, ensure_ascii=False
                    )
                    break
                idle_ticks += 1
                if idle_ticks % 20 == 0:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.1)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/v1/runs/{run_id}/feedback")
    async def submit_feedback(
        run_id: str,
        payload: FeedbackRequest,
        x_user_id: str = Header(..., alias="X-User-ID"),
    ):
        state = owned_state(run_id, x_user_id)
        feedback = payload.model_dump()
        app.state.store.save_feedback(run_id, x_user_id, feedback)
        state.feedback = feedback
        app.state.store.save_state(state)
        bad_case_id = None
        if not payload.helpful or payload.rating <= 2:
            bad_case_id = "bad_" + uuid.uuid4().hex
            tags = list(dict.fromkeys(payload.tags + ["negative_feedback"]))
            app.state.store.create_bad_case(bad_case_id, state, payload.comment or "negative_feedback", tags)
        app.state.store.append_event(
            run_id,
            "feedback.collected",
            {"rating": payload.rating, "helpful": payload.helpful, "bad_case_id": bad_case_id},
        )
        return {"status": "saved", "bad_case_id": bad_case_id}

    @app.post("/api/v1/knowledge/documents", status_code=201)
    async def import_document(
        payload: DocumentImportRequest,
        x_role: str = Header(..., alias="X-Role"),
    ):
        require_role(x_role, {"teacher", "maintainer"})
        metadata = payload.model_dump(exclude={"content", "source_path", "title", "document_type"})
        if payload.content:
            return app.state.retriever.import_text(
                payload.title,
                payload.content,
                payload.document_type,
                metadata=metadata,
            )
        path = ensure_under_root(Path(payload.source_path), settings.knowledge_root)
        return app.state.retriever.import_path(path, payload.document_type, metadata)

    @app.get("/api/v1/knowledge/documents")
    async def list_documents(x_role: str = Header(..., alias="X-Role")):
        require_role(x_role, {"teacher", "maintainer"})
        return {"items": app.state.store.list_documents()}

    @app.post("/api/v1/knowledge/alarm-codes", status_code=201)
    async def import_alarm_codes(
        payload: AlarmCodeImportRequest,
        x_role: str = Header(..., alias="X-Role"),
    ):
        require_role(x_role, {"teacher", "maintainer"})
        return app.state.alarm_codes.import_records(payload.records)

    @app.get("/api/v1/knowledge/alarm-codes")
    async def list_alarm_codes(
        code: Optional[str] = Query(default=None),
        limit: int = Query(default=500, ge=1, le=1000),
        x_role: str = Header(..., alias="X-Role"),
    ):
        require_role(x_role, {"teacher", "maintainer"})
        return {
            "items": app.state.store.list_alarm_codes(
                normalize_code(code) if code else None, limit
            )
        }

    @app.get("/api/v1/traces/{request_id}")
    async def get_trace(
        request_id: str,
        x_user_id: Optional[str] = Header(default=None, alias="X-User-ID"),
        x_role: Optional[str] = Header(default=None, alias="X-Role"),
    ):
        state = app.state.store.get_state_by_request(request_id)
        if not state:
            raise HTTPException(status_code=404, detail={"code": "TRACE_NOT_FOUND", "message": "Trace 不存在"})
        if x_role not in {"teacher", "maintainer"} and state.user_id != x_user_id:
            raise HTTPException(status_code=403, detail={"code": "TRACE_ACCESS_DENIED", "message": "无权读取该 Trace"})
        return app.state.store.trace(state)

    @app.get("/api/v1/bad-cases")
    async def list_bad_cases(
        limit: int = Query(default=100, ge=1, le=500),
        x_role: str = Header(..., alias="X-Role"),
    ):
        require_role(x_role, {"teacher", "maintainer"})
        return {"items": app.state.store.list_bad_cases(limit)}

    def bad_case_call(operation):
        try:
            return operation()
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "BAD_CASE_NOT_FOUND", "message": "bad case 不存在"},
            ) from exc

    @app.get("/api/v1/bad-cases/{bad_case_id}")
    async def get_bad_case(
        bad_case_id: str,
        x_role: str = Header(..., alias="X-Role"),
    ):
        require_role(x_role, {"teacher", "maintainer"})
        return bad_case_call(lambda: app.state.bad_cases.detail(bad_case_id))

    @app.get("/api/v1/bad-cases/{bad_case_id}/export")
    async def export_bad_case(
        bad_case_id: str,
        x_role: str = Header(..., alias="X-Role"),
    ):
        require_role(x_role, {"teacher", "maintainer"})
        return bad_case_call(lambda: app.state.bad_cases.export_package(bad_case_id))

    @app.put("/api/v1/bad-cases/{bad_case_id}/review")
    async def review_bad_case(
        bad_case_id: str,
        payload: BadCaseReviewRequest,
        x_role: str = Header(..., alias="X-Role"),
    ):
        require_role(x_role, {"teacher", "maintainer"})
        return bad_case_call(lambda: app.state.bad_cases.review(bad_case_id, payload))

    @app.post("/api/v1/bad-cases/{bad_case_id}/replay")
    async def replay_bad_case(
        bad_case_id: str,
        x_role: str = Header(..., alias="X-Role"),
    ):
        require_role(x_role, {"teacher", "maintainer"})
        return bad_case_call(lambda: app.state.bad_cases.replay(bad_case_id))

    @app.post("/api/v1/bad-cases/{bad_case_id}/promote", status_code=201)
    async def promote_bad_case(
        bad_case_id: str,
        x_role: str = Header(..., alias="X-Role"),
    ):
        require_role(x_role, {"teacher", "maintainer"})
        return bad_case_call(lambda: app.state.bad_cases.promote(bad_case_id))

    @app.get("/api/v1/regressions")
    async def list_regressions(
        limit: int = Query(default=500, ge=1, le=500),
        x_role: str = Header(..., alias="X-Role"),
    ):
        require_role(x_role, {"teacher", "maintainer"})
        return {"items": app.state.store.list_regression_cases(limit)}

    @app.post("/api/v1/regressions/run")
    async def run_regressions(
        payload: RegressionRunRequest,
        x_role: str = Header(..., alias="X-Role"),
    ):
        require_role(x_role, {"teacher", "maintainer"})
        return app.state.bad_cases.run_regressions(payload.limit)

    @app.get("/api/v1/operations/metrics")
    async def operations_metrics(
        hours: int = Query(default=24, ge=1, le=24 * 365),
        x_role: str = Header(..., alias="X-Role"),
    ):
        require_role(x_role, {"teacher", "maintainer"})
        return app.state.store.operational_metrics(hours)

    @app.get("/api/v1/students/{user_id}/learning-records")
    async def get_learning_records(
        user_id: str,
        x_user_id: Optional[str] = Header(default=None, alias="X-User-ID"),
        x_role: Optional[str] = Header(default=None, alias="X-Role"),
    ):
        if x_role not in {"teacher", "maintainer"} and x_user_id != user_id:
            raise HTTPException(status_code=403, detail={"code": "PROFILE_ACCESS_DENIED", "message": "无权读取该学习记录"})
        return {"items": app.state.store.learning_records(user_id)}

    @app.get("/api/v1/knowledge/points")
    async def list_knowledge_points(
        x_role: Optional[str] = Header(default=None, alias="X-Role"),
    ):
        items = app.state.store.list_knowledge_points()
        if x_role not in {"teacher", "maintainer"}:
            items = [
                {key: value for key, value in item.items() if key != "criteria"}
                for item in items
            ]
        return {"items": items}

    @app.post("/api/v1/knowledge/points", status_code=201)
    async def import_knowledge_points(
        payload: KnowledgePointImportRequest,
        x_role: str = Header(..., alias="X-Role"),
    ):
        require_role(x_role, {"teacher", "maintainer"})
        return app.state.tutoring.import_records(payload.records)

    def require_profile_access(
        user_id: str, x_user_id: Optional[str], x_role: Optional[str]
    ) -> None:
        if x_role not in {"teacher", "maintainer"} and x_user_id != user_id:
            raise HTTPException(
                status_code=403,
                detail={"code": "PROFILE_ACCESS_DENIED", "message": "无权访问该学生的辅导数据"},
            )

    @app.post("/api/v1/students/{user_id}/exercises", status_code=201)
    async def generate_exercise(
        user_id: str,
        payload: ExerciseGenerateRequest,
        x_user_id: Optional[str] = Header(default=None, alias="X-User-ID"),
        x_role: Optional[str] = Header(default=None, alias="X-Role"),
    ):
        require_profile_access(user_id, x_user_id, x_role)
        exercise = app.state.tutoring.generate_exercise(
            user_id,
            knowledge_point_id=payload.knowledge_point_id,
            difficulty=payload.difficulty,
        )
        exercise.pop("evidence", None)
        return exercise

    @app.get("/api/v1/students/{user_id}/exercises")
    async def list_student_exercises(
        user_id: str,
        x_user_id: Optional[str] = Header(default=None, alias="X-User-ID"),
        x_role: Optional[str] = Header(default=None, alias="X-Role"),
    ):
        require_profile_access(user_id, x_user_id, x_role)
        return {"items": app.state.store.list_exercises(user_id)}

    @app.post("/api/v1/exercises/{exercise_id}/submit")
    async def submit_exercise(
        exercise_id: str,
        payload: ExerciseSubmitRequest,
        x_user_id: str = Header(..., alias="X-User-ID"),
    ):
        try:
            return app.state.tutoring.grade_answer(exercise_id, x_user_id, payload.answer)
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail={"code": "EXERCISE_NOT_FOUND", "message": "练习不存在"},
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=403,
                detail={"code": "EXERCISE_ACCESS_DENIED", "message": str(exc)},
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "EXERCISE_ALREADY_GRADED", "message": str(exc)},
            )

    @app.get("/api/v1/students/{user_id}/progress")
    async def get_student_progress(
        user_id: str,
        x_user_id: Optional[str] = Header(default=None, alias="X-User-ID"),
        x_role: Optional[str] = Header(default=None, alias="X-Role"),
    ):
        require_profile_access(user_id, x_user_id, x_role)
        return {"items": app.state.store.student_progress(user_id)}

    @app.delete("/api/v1/students/{user_id}/learning-records")
    async def clear_learning_records(
        user_id: str,
        x_user_id: str = Header(..., alias="X-User-ID"),
    ):
        if x_user_id != user_id:
            raise HTTPException(status_code=403, detail={"code": "PROFILE_ACCESS_DENIED", "message": "只能清除自己的学习记录"})
        return {"deleted": app.state.store.clear_learning_records(user_id)}

    @app.get("/api/v1/classes/learning-summary")
    async def class_learning_summary(x_role: str = Header(..., alias="X-Role")):
        require_role(x_role, {"teacher", "maintainer"})
        return {"items": app.state.store.aggregate_learning(), "privacy": "aggregated_without_user_id"}

    @app.get("/api/v1/classes/progress-summary")
    async def class_progress_summary(x_role: str = Header(..., alias="X-Role")):
        require_role(x_role, {"teacher", "maintainer"})
        return {
            "items": app.state.store.aggregate_mastery(),
            "privacy": "knowledge_point_aggregate_without_user_id",
        }

    @app.post("/api/v1/evaluations/run")
    async def run_evaluation(
        payload: EvaluationRequest,
        x_role: str = Header(..., alias="X-Role"),
    ):
        require_role(x_role, {"teacher", "maintainer"})
        if payload.compare:
            return app.state.evaluator.run_comparison(
                payload.dataset_path, payload.limit, payload.include_neural
            )
        return app.state.evaluator.run(payload.dataset_path, payload.limit, payload.strategy.value)

    @app.post("/api/v1/evaluations/diagnosis")
    async def run_diagnostic_evaluation(
        payload: DiagnosticEvaluationRequest,
        x_role: str = Header(..., alias="X-Role"),
    ):
        require_role(x_role, {"teacher", "maintainer"})
        return app.state.diagnostic_evaluator.run(payload.dataset_path, payload.limit)

    @app.post("/api/v1/evaluations/tutoring")
    async def run_tutoring_evaluation(
        payload: TutoringEvaluationRequest,
        x_role: str = Header(..., alias="X-Role"),
    ):
        require_role(x_role, {"teacher", "maintainer"})
        return app.state.tutoring_evaluator.run(payload.dataset_path, payload.limit)

    return app


app = create_app()
