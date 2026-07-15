import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Type

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class _StrictToolArguments(BaseModel):
    """Arguments accepted by the deterministic tool control plane."""

    model_config = ConfigDict(extra="forbid", strict=True)


class CourseRetrievalArguments(_StrictToolArguments):
    query: str = Field(min_length=1, max_length=1200)


class ManualRetrievalArguments(_StrictToolArguments):
    query: str = Field(min_length=1, max_length=1200)
    equipment: Optional[str] = Field(default=None, min_length=1, max_length=200)


class LookupErrorCodeArguments(_StrictToolArguments):
    code: str = Field(min_length=1, max_length=100)
    equipment_brand: Optional[str] = Field(default=None, min_length=1, max_length=100)
    equipment_model: Optional[str] = Field(default=None, min_length=1, max_length=100)
    controller_version: Optional[str] = Field(default=None, min_length=1, max_length=100)


class GetStudentProfileArguments(_StrictToolArguments):
    pass


class IdentifyWeakTopicsArguments(_StrictToolArguments):
    query: str = Field(min_length=1, max_length=1200)


TOOL_ALLOWLIST = {
    "knowledge_qa": {"course_retrieval"},
    "fault_diagnosis": {"lookup_error_code", "manual_retrieval"},
    "tutoring": {
        "get_student_profile",
        "identify_weak_topics",
        "course_retrieval",
    },
}

TOOL_ARGUMENT_SCHEMAS: Dict[str, Type[_StrictToolArguments]] = {
    "course_retrieval": CourseRetrievalArguments,
    "manual_retrieval": ManualRetrievalArguments,
    "lookup_error_code": LookupErrorCodeArguments,
    "get_student_profile": GetStudentProfileArguments,
    "identify_weak_topics": IdentifyWeakTopicsArguments,
}

# Each model-controlled argument must resolve to one deterministic, auditable field.
TOOL_ARGUMENT_SOURCES: Dict[str, Dict[str, str]] = {
    "course_retrieval": {"query": "normalized_query"},
    "manual_retrieval": {
        "query": "normalized_query",
        "equipment": "slots.equipment",
    },
    "lookup_error_code": {
        "code": "slots.error_code",
        "equipment_brand": "slots.equipment_brand",
        "equipment_model": "slots.equipment_model",
        "controller_version": "slots.controller_version",
    },
    "get_student_profile": {},
    "identify_weak_topics": {"query": "normalized_query"},
}


def _plan_adjustment(
    action: str,
    reason: str,
    tool_name: str,
    argument: Optional[str] = None,
    proposed_value: Any = None,
    effective_value: Any = None,
) -> Dict[str, Any]:
    item = {"action": action, "reason": reason, "tool_name": tool_name}
    if argument is not None:
        item["argument"] = argument
    if proposed_value is not None:
        item["proposed_value"] = proposed_value
    if effective_value is not None:
        item["effective_value"] = effective_value
    return item


class ToolPlanValidator:
    """Turn an LLM proposal into a grounded, allowlisted, typed read-only plan."""

    def validate(
        self,
        task_type: str,
        proposed_plan: List[Dict[str, Any]],
        grounded_values: Mapping[str, Any],
        field_provenance: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        allowed = TOOL_ALLOWLIST.get(task_type, {"course_retrieval"})
        provenance = field_provenance or {}
        validated: List[Dict[str, Any]] = []
        adjustments: List[Dict[str, Any]] = []
        seen = set()

        for proposal in proposed_plan:
            name = proposal.get("name")
            if name not in allowed:
                adjustments.append(
                    _plan_adjustment("rejected_tool", "tool_not_allowed_for_task", str(name))
                )
                continue
            if name in seen:
                adjustments.append(_plan_adjustment("rejected_tool", "duplicate_tool", name))
                continue
            schema = TOOL_ARGUMENT_SCHEMAS[name]
            source_rules = TOOL_ARGUMENT_SOURCES[name]
            proposed_arguments = proposal.get("arguments") or {}
            if not isinstance(proposed_arguments, dict):
                adjustments.append(
                    _plan_adjustment("rejected_arguments", "arguments_not_an_object", name)
                )
                proposed_arguments = {}

            for unknown_name, unknown_value in proposed_arguments.items():
                if unknown_name not in source_rules:
                    adjustments.append(
                        _plan_adjustment(
                            "removed_argument",
                            "argument_not_declared_by_tool_schema",
                            name,
                            unknown_name,
                            proposed_value=unknown_value,
                        )
                    )

            effective_arguments: Dict[str, Any] = {}
            argument_sources: Dict[str, str] = {}
            argument_validation: Dict[str, str] = {}
            for argument_name, source_name in source_rules.items():
                trusted_value = grounded_values.get(source_name)
                proposed_value = proposed_arguments.get(argument_name)
                if trusted_value in (None, ""):
                    if proposed_value not in (None, ""):
                        adjustments.append(
                            _plan_adjustment(
                                "removed_argument",
                                "trusted_source_missing",
                                name,
                                argument_name,
                                proposed_value=proposed_value,
                            )
                        )
                    continue
                effective_arguments[argument_name] = trusted_value
                argument_sources[argument_name] = "%s:%s" % (
                    source_name,
                    provenance.get(source_name, "deterministic_control_plane"),
                )
                if proposed_value in (None, ""):
                    argument_validation[argument_name] = "filled_by_control_plane"
                    adjustments.append(
                        _plan_adjustment(
                            "filled_argument",
                            "required_trusted_value",
                            name,
                            argument_name,
                            effective_value=trusted_value,
                        )
                    )
                elif proposed_value != trusted_value:
                    argument_validation[argument_name] = "overridden_by_control_plane"
                    adjustments.append(
                        _plan_adjustment(
                            "overrode_argument",
                            "value_not_grounded_in_declared_source",
                            name,
                            argument_name,
                            proposed_value=proposed_value,
                            effective_value=trusted_value,
                        )
                    )
                else:
                    argument_validation[argument_name] = "accepted_model_value"

            try:
                typed_arguments = schema.model_validate(effective_arguments)
            except ValidationError as exc:
                adjustments.append(
                    _plan_adjustment(
                        "rejected_tool",
                        "typed_argument_validation_failed:%s" % exc.errors()[0]["type"],
                        name,
                    )
                )
                continue
            seen.add(name)
            validated.append(
                {
                    "name": name,
                    "arguments": typed_arguments.model_dump(mode="json", exclude_none=True),
                    "argument_sources": argument_sources,
                    "argument_validation": argument_validation,
                    "reason": proposal.get("reason", "model_proposed_read_only_tool"),
                    "validation_status": "passed",
                }
            )

        # These are the minimum read-only tools required by the bounded workflow. The
        # model may propose their order/arguments, but cannot remove an evidence gate.
        for required_name in sorted(allowed):
            if required_name in seen:
                continue
            source_rules = TOOL_ARGUMENT_SOURCES[required_name]
            effective_arguments = {
                argument_name: grounded_values[source_name]
                for argument_name, source_name in source_rules.items()
                if grounded_values.get(source_name) not in (None, "")
            }
            try:
                typed_arguments = TOOL_ARGUMENT_SCHEMAS[required_name].model_validate(
                    effective_arguments
                )
            except ValidationError:
                adjustments.append(
                    _plan_adjustment(
                        "rejected_tool", "required_trusted_value_missing", required_name
                    )
                )
                continue
            validated.append(
                {
                    "name": required_name,
                    "arguments": typed_arguments.model_dump(mode="json", exclude_none=True),
                    "argument_sources": {
                        argument_name: "%s:%s"
                        % (source_name, provenance.get(source_name, "deterministic_control_plane"))
                        for argument_name, source_name in source_rules.items()
                        if source_name in grounded_values and grounded_values[source_name] not in (None, "")
                    },
                    "argument_validation": {
                        argument_name: "added_by_control_plane"
                        for argument_name in effective_arguments
                    },
                    "reason": "required_by_deterministic_task_contract",
                    "validation_status": "added_by_control_plane",
                }
            )
            adjustments.append(
                _plan_adjustment(
                    "added_tool", "required_by_deterministic_task_contract", required_name
                )
            )
        return {"validated_plan": validated, "adjustments": adjustments}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    timeout_seconds: float
    max_retries: int = 0
    retryable_exceptions: Tuple[Type[Exception], ...] = (TimeoutError, ConnectionError)
    permission: str = "student"
    redact_fields: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolResult:
    value: Any
    attempts: int
    duration_ms: float


class ToolExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, attempts: int, retryable: bool):
        super().__init__(message)
        self.code = code
        self.attempts = attempts
        self.retryable = retryable


class ToolRuntime:
    """Unified timeout/retry/circuit-breaker wrapper for all Agent tools."""

    def __init__(self, failure_threshold: int = 3, reset_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.reset_seconds = reset_seconds
        self._circuits: Dict[str, Dict[str, float]] = {}
        self._lock = threading.RLock()

    def _circuit_is_open(self, name: str) -> bool:
        with self._lock:
            circuit = self._circuits.get(name)
            if not circuit or circuit["failures"] < self.failure_threshold:
                return False
            if time.monotonic() - circuit["opened_at"] >= self.reset_seconds:
                self._circuits[name] = {"failures": 0, "opened_at": 0}
                return False
            return True

    def _record_failure(self, name: str) -> None:
        with self._lock:
            circuit = self._circuits.setdefault(name, {"failures": 0, "opened_at": 0})
            circuit["failures"] += 1
            if circuit["failures"] >= self.failure_threshold:
                circuit["opened_at"] = time.monotonic()

    def _record_success(self, name: str) -> None:
        with self._lock:
            self._circuits[name] = {"failures": 0, "opened_at": 0}

    def execute(self, spec: ToolSpec, operation: Callable[[], Any]) -> ToolResult:
        if self._circuit_is_open(spec.name):
            raise ToolExecutionError(
                "TOOL_CIRCUIT_OPEN",
                "工具 %s 的熔断器已打开" % spec.name,
                attempts=0,
                retryable=True,
            )
        started = time.perf_counter()
        last_error: Optional[Exception] = None
        for attempt in range(1, spec.max_retries + 2):
            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tool-%s" % spec.name)
            future = executor.submit(operation)
            try:
                value = future.result(timeout=spec.timeout_seconds)
                # The task is complete; wait for the worker to leave so local
                # resources such as SQLite handles are released deterministically.
                executor.shutdown(wait=True, cancel_futures=True)
                self._record_success(spec.name)
                return ToolResult(value, attempt, round((time.perf_counter() - started) * 1000, 2))
            except FutureTimeoutError:
                future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                last_error = TimeoutError("工具执行超过 %.2f 秒" % spec.timeout_seconds)
                retryable = True
            except Exception as exc:
                executor.shutdown(wait=True, cancel_futures=True)
                last_error = exc
                retryable = isinstance(exc, spec.retryable_exceptions)
            if not retryable or attempt > spec.max_retries:
                self._record_failure(spec.name)
                code = "TOOL_TIMEOUT" if isinstance(last_error, TimeoutError) else "TOOL_EXECUTION_FAILED"
                raise ToolExecutionError(code, str(last_error), attempt, retryable) from last_error
        raise AssertionError("unreachable")
