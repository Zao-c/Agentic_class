from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import TaskType


class StrictDecision(BaseModel):
    """Machine-validated LLM output. Free-form chain-of-thought is never requested."""

    model_config = ConfigDict(extra="forbid")


class IntentDecision(StrictDecision):
    task_type: TaskType
    decision_basis: str = Field(min_length=1, max_length=300)


class QueryRewriteDecision(StrictDecision):
    normalized_query: str = Field(min_length=1, max_length=1200)
    used_history: bool = False
    decision_basis: str = Field(min_length=1, max_length=300)


class ProposedSlot(StrictDecision):
    value: str = Field(min_length=1, max_length=200)
    source: Literal["user_current", "session_history"]


class SlotExtractionDecision(StrictDecision):
    equipment_brand: Optional[ProposedSlot] = None
    equipment_model: Optional[ProposedSlot] = None
    error_code: Optional[ProposedSlot] = None
    operating_mode: Optional[ProposedSlot] = None
    controller_version: Optional[ProposedSlot] = None
    decision_basis: str = Field(min_length=1, max_length=300)


class ClarificationDecision(StrictDecision):
    missing_slot: Literal["equipment", "error_code", "operating_mode"]
    question: str = Field(min_length=2, max_length=500)
    decision_basis: str = Field(min_length=1, max_length=300)


class ToolProposal(StrictDecision):
    name: Literal[
        "course_retrieval",
        "manual_retrieval",
        "lookup_error_code",
        "get_student_profile",
        "identify_weak_topics",
    ]
    arguments: Dict[str, str] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=300)


class ToolPlanDecision(StrictDecision):
    tools: List[ToolProposal] = Field(min_length=1, max_length=5)
    decision_basis: str = Field(min_length=1, max_length=300)


class EvidenceSupportDecision(StrictDecision):
    supported: bool
    confidence: float = Field(ge=0.0, le=1.0)
    supported_claims: List[str] = Field(default_factory=list, max_length=10)
    unsupported_claims: List[str] = Field(default_factory=list, max_length=10)
    decision_basis: str = Field(min_length=1, max_length=500)


DECISION_SCHEMA_VERSION = "1.0.0"
