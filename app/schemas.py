from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskType(str, Enum):
    knowledge_qa = "knowledge_qa"
    fault_diagnosis = "fault_diagnosis"
    tutoring = "tutoring"
    other = "other"


class RunStatus(str, Enum):
    queued = "queued"
    running = "running"
    waiting_for_user = "waiting_for_user"
    completed = "completed"
    abstained = "abstained"
    escalated = "escalated"
    failed = "failed"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class RetrievalStrategy(str, Enum):
    bm25 = "bm25"
    embedding = "embedding"
    hybrid = "hybrid"
    hybrid_rerank = "hybrid_rerank"
    neural_embedding = "neural_embedding"
    neural_hybrid = "neural_hybrid"
    neural_hybrid_rerank = "neural_hybrid_rerank"


TERMINAL_STATUSES = {
    RunStatus.waiting_for_user.value,
    RunStatus.completed.value,
    RunStatus.abstained.value,
    RunStatus.escalated.value,
    RunStatus.failed.value,
}


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=8000)
    role: str = Field(default="student", pattern="^(student|teacher|maintainer)$")
    context: Dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    document_id: str
    chunk_id: Optional[str] = None
    title: str
    document_type: str = "unknown"
    chapter: Optional[str] = None
    page: Optional[int] = None
    excerpt: str
    score: float
    retrieval_method: str = "unknown"
    score_components: Dict[str, float] = Field(default_factory=dict)


class AgentState(BaseModel):
    request_id: str
    run_id: str
    session_id: str
    user_id: str
    task_type: TaskType = TaskType.other
    original_message: str
    normalized_query: str = ""
    required_slots: List[str] = Field(default_factory=list)
    collected_slots: Dict[str, str] = Field(default_factory=dict)
    tool_plan: List[str] = Field(default_factory=list)
    tool_history: List[Dict[str, Any]] = Field(default_factory=list)
    retrieved_evidence: List[Citation] = Field(default_factory=list)
    evidence_score: float = 0.0
    evidence_details: Dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.low
    current_hypotheses: List[str] = Field(default_factory=list)
    retry_count: int = 0
    step_count: int = 0
    transition_count: int = 0
    final_status: RunStatus = RunStatus.queued
    answer: Optional[str] = None
    feedback: Optional[Dict[str, Any]] = None
    generated_exercise_id: Optional[str] = None
    configuration: Dict[str, Any] = Field(default_factory=dict)
    decision_history: List[Dict[str, Any]] = Field(default_factory=list)
    field_provenance: Dict[str, str] = Field(default_factory=dict)
    model_usage: Dict[str, Any] = Field(default_factory=dict)
    stop_reason: Optional[str] = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class RunAccepted(BaseModel):
    request_id: str
    run_id: str
    status: RunStatus
    stream_url: str


class RunView(BaseModel):
    request_id: str
    run_id: str
    session_id: str
    task_type: TaskType
    status: RunStatus
    answer: Optional[str] = None
    citations: List[Citation] = Field(default_factory=list)
    risk_level: RiskLevel
    required_slots: List[str] = Field(default_factory=list)
    collected_slots: Dict[str, str] = Field(default_factory=dict)
    generated_exercise_id: Optional[str] = None
    created_at: str
    updated_at: str


class FeedbackRequest(BaseModel):
    rating: int = Field(ge=1, le=5)
    helpful: bool
    comment: Optional[str] = Field(default=None, max_length=2000)
    tags: List[str] = Field(default_factory=list)


class DocumentImportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: Optional[str] = Field(default=None, max_length=2_000_000)
    source_path: Optional[str] = None
    document_type: str = "course_material"
    course: str = "工业机器人"
    equipment_brand: Optional[str] = None
    equipment_model: Optional[str] = None
    chapter: Optional[str] = None
    version: str = "1"
    effective_date: Optional[str] = None
    access_scope: str = "public"

    @model_validator(mode="after")
    def require_source(self):
        if not self.content and not self.source_path:
            raise ValueError("content 和 source_path 至少提供一个")
        return self


class AlarmCodeRecord(BaseModel):
    alarm_id: Optional[str] = None
    equipment_brand: str = Field(min_length=1, max_length=100)
    equipment_models: List[str] = Field(min_length=1)
    controller_versions: List[str] = Field(default_factory=list)
    code: str = Field(min_length=2, max_length=80)
    title: str = Field(min_length=1, max_length=300)
    meaning: str = Field(min_length=1, max_length=4000)
    likely_causes: List[str] = Field(default_factory=list)
    safe_checks: List[str] = Field(default_factory=list)
    forbidden_actions: List[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.medium
    source_title: str = Field(min_length=1, max_length=300)
    source_locator: Optional[str] = Field(default=None, max_length=300)
    source_excerpt: Optional[str] = Field(default=None, max_length=2000)
    version: str = Field(default="1", max_length=80)
    effective_date: Optional[str] = None
    review_status: str = Field(
        default="source_verified",
        pattern="^(draft|source_verified|teacher_confirmed)$",
    )
    access_scope: str = Field(default="public", max_length=100)
    is_active: bool = True


class AlarmCodeImportRequest(BaseModel):
    records: List[AlarmCodeRecord] = Field(min_length=1, max_length=1000)


class KnowledgePointCriterion(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    keywords: List[str] = Field(min_length=1, max_length=20)


class KnowledgePointRecord(BaseModel):
    knowledge_point_id: str = Field(pattern=r"^kp_[a-z0-9_]{3,80}$")
    name: str = Field(min_length=1, max_length=200)
    aliases: List[str] = Field(default_factory=list, max_length=30)
    category: str = Field(default="operation_programming", max_length=100)
    description: str = Field(min_length=1, max_length=2000)
    source_query: str = Field(min_length=1, max_length=300)
    question_template: str = Field(min_length=1, max_length=1000)
    criteria: List[KnowledgePointCriterion] = Field(min_length=1, max_length=20)
    difficulty: str = Field(default="basic", pattern="^(basic|intermediate|advanced)$")
    version: str = Field(default="1", max_length=80)
    review_status: str = Field(
        default="source_verified",
        pattern="^(draft|source_verified|teacher_confirmed)$",
    )
    is_active: bool = True


class KnowledgePointImportRequest(BaseModel):
    records: List[KnowledgePointRecord] = Field(min_length=1, max_length=1000)


class ExerciseGenerateRequest(BaseModel):
    knowledge_point_id: Optional[str] = Field(default=None, max_length=100)
    difficulty: Optional[str] = Field(
        default=None, pattern="^(basic|intermediate|advanced)$"
    )


class ExerciseSubmitRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=8000)


class EvaluationRequest(BaseModel):
    dataset_path: Optional[str] = None
    limit: Optional[int] = Field(default=None, ge=1, le=1000)
    strategy: RetrievalStrategy = RetrievalStrategy.hybrid_rerank
    compare: bool = False
    include_neural: bool = False


class DiagnosticEvaluationRequest(BaseModel):
    dataset_path: Optional[str] = None
    limit: Optional[int] = Field(default=None, ge=1, le=500)


class TutoringEvaluationRequest(BaseModel):
    dataset_path: Optional[str] = None
    limit: Optional[int] = Field(default=None, ge=1, le=500)


class BadCaseReviewRequest(BaseModel):
    expected_status: Optional[RunStatus] = None
    expected_task_type: Optional[TaskType] = None
    expected_risk_level: Optional[RiskLevel] = None
    answer_must_contain: List[str] = Field(default_factory=list, max_length=20)
    answer_must_not_contain: List[str] = Field(default_factory=list, max_length=20)
    require_citations: Optional[bool] = None
    review_note: Optional[str] = Field(default=None, max_length=2000)
    status: str = Field(default="reviewed", pattern="^(open|reviewed|resolved)$")


class RegressionRunRequest(BaseModel):
    limit: Optional[int] = Field(default=None, ge=1, le=500)


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: Optional[str] = None
