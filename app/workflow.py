import hashlib
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.config import Settings
from app.agentic_graph import ControlledAgentGraph
from app.decision_provider import DecisionProviderError
from app.alarm_codes import AlarmCodeService, split_equipment
from app.evidence import EvidenceJudge
from app.retrieval import Retriever, tokenize
from app.safety import SafetyDecision, check_safety
from app.schemas import AgentState, ChatRequest, Citation, RiskLevel, RunStatus, TaskType
from app.storage import Store
from app.tool_runtime import ToolExecutionError, ToolRuntime, ToolSpec
from app.tutoring import TutoringService


DIAGNOSTIC_TERMS = (
    "报警",
    "故障",
    "异常",
    "报错",
    "错误码",
    "报警码",
    "急停",
    "无法运行",
    "不动作",
    "安全联锁",
    "强制运动",
    "robot_control",
)
TUTORING_TERMS = ("出题", "练习", "薄弱", "错题", "学习建议", "辅导", "测验", "批改", "学习进度")
MODEL_PATTERNS = (
    r"\bIRB\s*\d{3,4}[A-Z-]*\b",
    r"\bM-?\d{1,3}i[A-Z]?\b",
    r"\bKR\s*\d{1,3}\b",
    r"\bER\d{1,4}\b",
    r"\b[A-Z]{2,5}-?\d{2,5}[A-Z-]*\b",
)
BRANDS = ("ABB", "FANUC", "发那科", "KUKA", "库卡", "埃斯顿", "汇川", "新松", "安川")
UNTRUSTED_DIAGNOSTIC_EVIDENCE_TYPES = frozenset({"redteam_fixture"})
EVIDENCE_PROMPT_INJECTION_PATTERNS = (
    r"忽略.{0,12}(?:系统|开发者|安全).{0,12}(?:规则|指令|要求)",
    r"(?:调用|执行)\s*(?:robot_control|tool_call|function_call)",
    r"\b(?:ignore|override|disregard)\b.{0,40}\b(?:system|developer|instruction|policy)\b",
    r"\b(?:system|developer|assistant)\s*prompt\s*:",
)
RISK_PRIORITY = {
    RiskLevel.low: 0,
    RiskLevel.medium: 1,
    RiskLevel.high: 2,
    RiskLevel.critical: 3,
}


def invalidates_previous_diagnostic_context(text: str) -> bool:
    """Return true only for an explicit withdrawal of earlier diagnostic facts."""
    compact = re.sub(r"\s+", "", text).lower()
    previous_reference = any(
        term in compact for term in ("前面", "之前", "上一轮", "刚才", "先前")
    )
    withdrawal = any(
        term in compact
        for term in (
            "不适用于我",
            "不适用",
            "不是我的",
            "属于另一个",
            "属于其他",
            "请忽略",
            "忽略掉",
            "撤回",
            "作废",
        )
    )
    return previous_reference and withdrawal


def equipment_claim_is_untrusted(text: str) -> bool:
    """Do not promote hypothetical or explicitly unknown equipment to a trusted slot."""
    compact = re.sub(r"\s+", "", text).lower()
    hypothetical = any(
        term in compact
        for term in ("假装", "假设", "假定", "当作", "编造型号", "伪造型号")
    )
    explicitly_unknown = any(
        term in compact
        for term in (
            "型号不知道",
            "型号不清楚",
            "型号未知",
            "型号未确认",
            "型号还没确认",
            "型号尚未确认",
        )
    )
    return hypothetical or explicitly_unknown


def diagnostic_evidence_rejection_reason(document_type: str, text: str) -> Optional[str]:
    """Keep untrusted instructions out of diagnostic model context and citations."""

    if document_type in UNTRUSTED_DIAGNOSTIC_EVIDENCE_TYPES:
        return "untrusted_document_type"
    if any(
        re.search(pattern, text, flags=re.I | re.S)
        for pattern in EVIDENCE_PROMPT_INJECTION_PATTERNS
    ):
        return "prompt_injection_pattern"
    return None


def extract_contextual_diagnostic_slots(
    history: List[Dict[str, Any]], current_message: str
) -> Dict[str, str]:
    """Merge trusted slots turn by turn so later confirmation can recover state."""
    previous_messages = [
        item["message"]
        for item in history
        if item["task_type"] == TaskType.fault_diagnosis.value
    ]
    if invalidates_previous_diagnostic_context(current_message):
        previous_messages = []
    slots: Dict[str, str] = {}
    equipment_fields = {
        "equipment",
        "equipment_brand",
        "equipment_model",
        "controller_version",
    }
    for message in previous_messages + [current_message]:
        if equipment_claim_is_untrusted(message):
            for field in equipment_fields:
                slots.pop(field, None)
        slots.update(extract_diagnostic_slots(message))
    return slots


def classify_intent(message: str, previous_task: Optional[str] = None) -> TaskType:
    compact = message.replace(" ", "")
    if any(term in compact for term in DIAGNOSTIC_TERMS):
        return TaskType.fault_diagnosis
    if "安全状态" in compact and any(
        term in compact for term in ("如何", "怎么", "处理", "处置", "怎么办", "恢复", "应该")
    ):
        return TaskType.fault_diagnosis
    if any(term in compact for term in TUTORING_TERMS):
        return TaskType.tutoring
    followup_terms = ("还是", "已经", "现在", "尝试", "没有变化", "仍然", "故障发生", "报警原文")
    if previous_task == TaskType.fault_diagnosis.value and (
        extract_diagnostic_slots(message) or any(term in compact for term in followup_terms)
    ):
        return TaskType.fault_diagnosis
    return TaskType.knowledge_qa


def extract_diagnostic_slots(text: str) -> Dict[str, str]:
    slots: Dict[str, str] = {}
    upper = text.upper()
    brand = next((candidate for candidate in BRANDS if candidate.upper() in upper), None)
    model = None
    for pattern in MODEL_PATTERNS:
        match = re.search(pattern, upper, flags=re.I)
        if match:
            model = re.sub(r"\s+", "", match.group(0))
            break
    if not equipment_claim_is_untrusted(text):
        if brand and model:
            slots["equipment"] = "%s %s" % (brand, model)
        elif model:
            slots["equipment"] = model
        elif brand:
            slots["equipment_brand"] = brand
    code_patterns = (
        r"(?:报警码|错误码|报警|报错)\s*[:：#]?\s*([A-Z]{0,5}[- ]?\d{3,8})",
        r"\b([A-Z]{1,5}[- ]\d{3,8})\b",
    )
    for pattern in code_patterns:
        match = re.search(pattern, upper, flags=re.I)
        if match:
            slots["error_code"] = re.sub(r"\s+", "", match.group(1))
            break
    for mode in ("自动模式", "手动模式", "示教模式", "自动状态", "手动状态", "示教状态"):
        if mode in text:
            slots["operating_mode"] = mode
            break
    if "operating_mode" not in slots:
        contextual_mode = re.search(r"(?:处于|是|为)\s*(自动|手动|示教)(?!编程)", text)
        if contextual_mode:
            slots["operating_mode"] = contextual_mode.group(1)
    version_match = re.search(r"(?:版本|控制器)\s*[:：]?\s*([A-Za-z0-9._-]{2,30})", text)
    if version_match:
        slots["controller_version"] = version_match.group(1)
    return slots


def best_sentences(query: str, excerpts: List[str], limit: int = 4) -> List[str]:
    query_tokens = set(tokenize(query))
    candidates: List[Tuple[float, str]] = []
    for excerpt in excerpts:
        for sentence in re.split(r"(?<=[。！？；\n])", excerpt):
            sentence = sentence.strip()
            if len(sentence) < 8:
                continue
            if sentence.startswith("#") or "适用问题" in sentence or sentence.endswith(("？", "?")):
                continue
            sentence_tokens = set(tokenize(sentence))
            overlap = len(query_tokens & sentence_tokens) / max(1, len(query_tokens))
            candidates.append((overlap, sentence))
    candidates.sort(key=lambda item: item[0], reverse=True)
    result = []
    seen = set()
    for _, sentence in candidates:
        normalized = re.sub(r"\s+", "", sentence)
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(sentence[:500])
        if len(result) >= limit:
            break
    return result


def ordered_steps(excerpts: List[str], limit: int = 10) -> List[str]:
    """Extract an intact ordered procedure from one evidence chunk."""
    for excerpt in excerpts:
        steps = []
        for line in excerpt.splitlines():
            cleaned = line.strip()
            match = re.match(r"^(\d+)[.、]\s*(.+)$", cleaned)
            if match:
                steps.append("%s. %s" % (match.group(1), match.group(2).strip()))
            elif steps and cleaned and not cleaned.startswith(("补充说明", "适用问题", "#")):
                steps[-1] = steps[-1] + " " + cleaned
            elif steps and cleaned.startswith(("补充说明", "适用问题", "#")):
                break
        if len(steps) >= 2:
            return steps[:limit]
    return []


class AgentWorkflow:
    """Bounded, explicit state machine. No prompt-driven loops."""

    def __init__(
        self,
        store: Store,
        retriever: Retriever,
        alarm_codes: AlarmCodeService,
        tutoring: TutoringService,
        settings: Settings,
        agentic_graph: Optional[ControlledAgentGraph] = None,
    ):
        self.store = store
        self.retriever = retriever
        self.alarm_codes = alarm_codes
        self.tutoring = tutoring
        self.settings = settings
        self.agentic_graph = agentic_graph
        self.tool_runtime = ToolRuntime()
        self.evidence_judge = EvidenceJudge(settings.evidence_threshold)

    def _event(self, state: AgentState, event_type: str, data: Dict[str, Any]) -> None:
        state.step_count += 1
        transition_events = {
            "state.changed",
            "intent.classified",
            "clarification.requested",
            "evidence.judged",
            "safety.checked",
            "answer.completed",
            "answer.abstained",
            "teacher.escalated",
            "run.failed",
        }
        if event_type in transition_events:
            state.transition_count += 1
        if state.transition_count > self.settings.max_agent_steps:
            raise RuntimeError("AGENT_STEP_LIMIT_EXCEEDED")
        self.store.append_event(state.run_id, event_type, data)
        self.store.save_state(state)

    def _record_model_decisions(self, state: AgentState, records: List[Dict[str, Any]]) -> None:
        usage = state.model_usage or {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
            "call_count": 0,
        }
        for record in records:
            state.decision_history.append(record)
            item_usage = record.get("usage", {})
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                usage[key] = int(usage.get(key, 0)) + int(item_usage.get(key, 0) or 0)
            usage["estimated_cost_usd"] = round(
                float(usage.get("estimated_cost_usd", 0.0))
                + float(record.get("estimated_cost_usd", 0.0) or 0.0),
                8,
            )
            usage["call_count"] = int(usage.get("call_count", 0)) + 1
            self.store.append_event(state.run_id, "model.decision.finished", record)
        state.model_usage = usage
        self.store.save_state(state)

    def _record_model_fallback(self, state: AgentState, node: str, exc: Exception) -> None:
        record = {
            "node": node,
            "provider": "configured",
            "model": self.settings.llm_model,
            "validation_result": "failed",
            "fallback_used": True,
            "fallback_reason": str(exc)[:300],
            "attempts": getattr(exc, "attempts", None),
        }
        state.decision_history.append(record)
        self.store.append_event(state.run_id, "model.decision.failed", record)
        self.store.save_state(state)

    def _agentic_evidence_supported(
        self,
        state: AgentState,
        citations: Optional[List[Citation]] = None,
    ) -> Optional[bool]:
        if not self.agentic_graph:
            return None
        try:
            result = self.agentic_graph.judge_evidence(
                state.normalized_query,
                citations if citations is not None else state.retrieved_evidence,
            )
            self._record_model_decisions(state, result.get("decisions", []))
            decision = result["evidence_decision"]
            state.evidence_details["llm_support"] = decision
            return bool(decision["supported"])
        except DecisionProviderError as exc:
            self._record_model_fallback(state, "llm_evidence_support", exc)
            if self.settings.agentic_fallback_to_portable:
                state.evidence_details["llm_support"] = {
                    "supported": None,
                    "fallback": "deterministic_evidence_judge",
                }
                return None
            raise

    @staticmethod
    def _validated_tool(state: AgentState, name: str) -> Optional[Dict[str, Any]]:
        plan = state.configuration.get("agentic_tool_plan", {})
        return next(
            (item for item in plan.get("validated_plan", []) if item.get("name") == name),
            None,
        )

    def _resolve_tool_arguments(
        self,
        state: AgentState,
        name: str,
        deterministic_arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Use validated model values, then resolve runtime-only deterministic fields."""
        validated = self._validated_tool(state, name)
        if not validated:
            return dict(deterministic_arguments)
        effective = dict(validated.get("arguments", {}))
        adjustments = state.configuration["agentic_tool_plan"].setdefault("adjustments", [])
        for argument_name, deterministic_value in deterministic_arguments.items():
            if argument_name not in effective:
                effective[argument_name] = deterministic_value
                continue
            if effective[argument_name] != deterministic_value:
                adjustments.append(
                    {
                        "action": "overrode_argument",
                        "reason": "runtime_dependency_or_control_value",
                        "tool_name": name,
                        "argument": argument_name,
                        "proposed_value": effective[argument_name],
                        "effective_value": deterministic_value,
                    }
                )
                effective[argument_name] = deterministic_value
        return effective

    def _record_tool_plan(self, state: AgentState, deterministic_plan: List[str]) -> None:
        state.tool_plan = deterministic_plan
        plan = state.configuration.get("agentic_tool_plan")
        if not plan:
            plan = {
                "proposed_plan": [],
                "validated_plan": [
                    {
                        "name": name,
                        "arguments": {},
                        "argument_sources": {},
                        "argument_validation": {},
                        "reason": "portable_deterministic_plan",
                        "validation_status": "deterministic",
                    }
                    for name in deterministic_plan
                ],
                "executed_plan": [],
                "adjustments": [],
            }
            state.configuration["agentic_tool_plan"] = plan
        else:
            validated_names = {item.get("name") for item in plan.get("validated_plan", [])}
            deterministic_control_tools = {
                "check_safety_constraint",
                "record_diagnostic_state",
                "generate_exercise",
            }
            for name in deterministic_plan:
                if name in validated_names or name not in deterministic_control_tools:
                    continue
                plan["validated_plan"].append(
                    {
                        "name": name,
                        "arguments": {},
                        "argument_sources": {},
                        "argument_validation": {},
                        "reason": "deterministic_safety_or_state_control",
                        "validation_status": "deterministic_control_plane",
                    }
                )
                plan.setdefault("adjustments", []).append(
                    {
                        "action": "added_tool",
                        "reason": "deterministic_safety_or_state_control",
                        "tool_name": name,
                    }
                )
        self._event(
            state,
            "tools.planned",
            {
                "tools": deterministic_plan,
                "proposed_plan": plan.get("proposed_plan", []),
                "validated_plan": plan.get("validated_plan", []),
                "executed_plan": plan.get("executed_plan", []),
                "adjustments": plan.get("adjustments", []),
            },
        )

    def _tool(
        self,
        state: AgentState,
        name: str,
        arguments: Dict[str, Any],
        operation: Callable[[], Any],
    ) -> Any:
        started = time.perf_counter()
        status = "success"
        error = None
        attempts = 0
        try:
            spec = ToolSpec(
                name=name,
                timeout_seconds=self.settings.tool_timeout_seconds,
                max_retries=self.settings.max_retries,
            )
            result = self.tool_runtime.execute(spec, operation)
            attempts = result.attempts
            return result.value
        except ToolExecutionError as exc:
            status = "error"
            attempts = exc.attempts
            error = {
                "type": type(exc).__name__,
                "code": exc.code,
                "message": str(exc)[:300],
                "retryable": exc.retryable,
            }
            raise
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            validated = self._validated_tool(state, name)
            argument_sources = {}
            for argument_name, argument_value in arguments.items():
                if (
                    validated
                    and validated.get("arguments", {}).get(argument_name) == argument_value
                ):
                    validation = validated.get("argument_validation", {}).get(
                        argument_name, "validated_control_value"
                    )
                    argument_sources[argument_name] = {
                        "source": validated.get("argument_sources", {}).get(
                            argument_name, "deterministic_control_plane"
                        ),
                        "validation": validation,
                    }
                else:
                    argument_sources[argument_name] = {
                        "source": "deterministic_runtime",
                        "validation": "not_model_controlled",
                    }
            record = {
                "tool_name": name,
                "arguments": arguments,
                "argument_sources": argument_sources,
                "status": status,
                "attempts": attempts,
                "duration_ms": duration_ms,
                "error": error,
            }
            state.tool_history.append(record)
            state.configuration.setdefault("agentic_tool_plan", {}).setdefault(
                "executed_plan", []
            ).append(
                {
                    "name": name,
                    "arguments": arguments,
                    "argument_sources": argument_sources,
                    "status": status,
                    "attempts": attempts,
                    "duration_ms": duration_ms,
                }
            )
            self.store.append_event(state.run_id, "tool.finished", record)
            self.store.save_state(state)

    def run(self, state: AgentState) -> AgentState:
        try:
            state.final_status = RunStatus.running
            is_neural = self.settings.retrieval_strategy.startswith("neural_")
            state.configuration = {
                "requested_agent_profile": self.settings.agent_profile,
                "effective_agent_profile": self.settings.agent_profile,
                "retrieval_strategy": self.settings.retrieval_strategy,
                "retrieval_top_k": self.settings.retrieval_top_k,
                "retrieval_candidate_k": self.settings.retrieval_candidate_k,
                "embedding": (
                    "%s@%s"
                    % (
                        self.settings.neural_embedding_model,
                        self.settings.neural_embedding_revision,
                    )
                    if is_neural
                    else "local_tfidf_lsa_v1"
                ),
                "embedding_dimension": self.settings.embedding_dimension,
                "reranker": (
                    "feature_reranker_v1"
                    if self.settings.retrieval_strategy == "hybrid_rerank"
                    else (
                        "%s@%s"
                        % (
                            self.settings.neural_reranker_model,
                            self.settings.neural_reranker_revision,
                        )
                        if self.settings.retrieval_strategy == "neural_hybrid_rerank"
                        else None
                    )
                ),
                "evidence_judge": "deterministic_evidence_judge_v2",
                "evidence_threshold": self.settings.evidence_threshold,
            }
            self._event(state, "state.changed", {"node": "classify_intent", "status": "running"})
            history = self.store.session_context(
                state.session_id, state.user_id, before_run_id=state.run_id
            )
            effective_history = (
                []
                if invalidates_previous_diagnostic_context(state.original_message)
                else history
            )
            previous_task = history[-1]["task_type"] if history else None
            deterministic_task = classify_intent(state.original_message, previous_task)
            preflight_safety = check_safety(
                state.original_message, evidence_sufficient=False
            )
            if preflight_safety.must_escalate:
                state.task_type = deterministic_task
                state.normalized_query = self._normalize_query(state, effective_history)
                self._event(
                    state,
                    "intent.classified",
                    {
                        "task_type": state.task_type.value,
                        "decision_source": "deterministic_safety_preflight",
                    },
                )
                self._event(
                    state,
                    "query.normalized",
                    {"normalized_query": state.normalized_query},
                )
                state.stop_reason = "deterministic_preflight_safety"
                return self._escalate(state, preflight_safety)
            if self.agentic_graph:
                deterministic_slots = extract_contextual_diagnostic_slots(
                    effective_history, state.original_message
                )
                try:
                    preflight = self.agentic_graph.run_preflight(
                        state.original_message,
                        effective_history,
                        deterministic_slots,
                        deterministic_task,
                    )
                    self._record_model_decisions(state, preflight.get("decisions", []))
                    state.task_type = TaskType(preflight["task_type"])
                    state.normalized_query = preflight["normalized_query"]
                    state.collected_slots = preflight.get("collected_slots", {})
                    state.field_provenance = preflight.get("field_provenance", {})
                    state.configuration["agentic_tool_plan"] = {
                        "proposed_plan": preflight.get("proposed_tools", []),
                        "validated_plan": preflight.get("validated_plan", []),
                        "executed_plan": [],
                        "adjustments": preflight.get("plan_adjustments", []),
                    }
                    state.configuration["agentic_preflight"] = {
                        "proposed_tools": preflight.get("proposed_tools", []),
                        "validated_tools": preflight.get("executed_tools", []),
                        "validated_plan": preflight.get("validated_plan", []),
                        "plan_adjustments": preflight.get("plan_adjustments", []),
                        "clarification_question": preflight.get("clarification_question"),
                    }
                    state.configuration["agentic_intent_control"] = preflight.get(
                        "intent_control", {}
                    )
                    state.configuration["agentic_query_rewrite"] = preflight.get(
                        "query_rewrite_control",
                        {
                            "proposed_query": state.normalized_query,
                            "validated_query": state.normalized_query,
                            "adjustments": [],
                        },
                    )
                except DecisionProviderError as exc:
                    self._record_model_fallback(state, "agentic_preflight", exc)
                    if not self.settings.agentic_fallback_to_portable:
                        raise
                    state.configuration["effective_agent_profile"] = "portable-fallback"
                    state.task_type = deterministic_task
                    state.normalized_query = self._normalize_query(
                        state, effective_history
                    )
            else:
                state.task_type = deterministic_task
                state.normalized_query = self._normalize_query(state, effective_history)
            self._event(
                state,
                "intent.classified",
                {
                    "task_type": state.task_type.value,
                    "decision_source": "llm_structured" if self.agentic_graph else "deterministic_rules",
                    **(
                        state.configuration.get("agentic_intent_control", {})
                        if self.agentic_graph
                        else {}
                    ),
                },
            )

            self._event(
                state,
                "query.normalized",
                {"normalized_query": state.normalized_query},
            )

            if state.task_type == TaskType.fault_diagnosis:
                result = self._diagnose(state, effective_history)
            elif state.task_type == TaskType.tutoring:
                result = self._tutor(state)
            else:
                result = self._answer_knowledge(state)
            if result.final_status in {RunStatus.completed, RunStatus.abstained, RunStatus.escalated}:
                self.store.record_learning(
                    result.user_id,
                    result.normalized_query,
                    result.final_status.value,
                    result.run_id,
                )
            return result
        except Exception as exc:
            state.final_status = RunStatus.failed
            state.answer = "请求处理失败，请稍后重试。错误代码：AGENT_EXECUTION_FAILED。"
            self.store.append_event(
                state.run_id,
                "run.failed",
                {"error_type": type(exc).__name__, "message": str(exc)[:300]},
            )
            self.store.save_state(state)
            self.store.create_bad_case(
                "bad_" + uuid.uuid4().hex,
                state,
                "agent_execution_failed",
                ["runtime_error"],
            )
            return state

    def _normalize_query(self, state: AgentState, history: List[Dict[str, Any]]) -> str:
        message = re.sub(r"\s+", " ", state.original_message).strip()
        if not history or len(message) > 25:
            return message
        if state.task_type != TaskType.fault_diagnosis:
            return message
        previous_messages = [item["message"] for item in history if item["task_type"] == TaskType.fault_diagnosis.value]
        if previous_messages:
            return "%s；补充信息：%s" % (previous_messages[-1][:500], message)
        return message

    def _diagnose(self, state: AgentState, history: List[Dict[str, Any]]) -> AgentState:
        current_slots = extract_diagnostic_slots(state.original_message)
        context_reset = invalidates_previous_diagnostic_context(state.original_message)
        untrusted_equipment = equipment_claim_is_untrusted(state.original_message)
        slots = extract_contextual_diagnostic_slots(history, state.original_message)
        for key, value in state.collected_slots.items():
            if context_reset and key not in current_slots:
                continue
            if untrusted_equipment and key in {
                "equipment",
                "equipment_brand",
                "equipment_model",
                "controller_version",
            }:
                continue
            slots.setdefault(key, value)
        state.required_slots = ["equipment", "error_code", "operating_mode"]
        state.collected_slots = slots
        self._event(
            state,
            "context.checked",
            {"required_slots": state.required_slots, "collected_slots": slots},
        )

        early_safety = check_safety(state.normalized_query, evidence_sufficient=False)
        if early_safety.must_escalate:
            return self._escalate(state, early_safety)

        missing = [slot for slot in state.required_slots if slot not in slots]
        if missing:
            prompts = {
                "equipment": "请先提供设备品牌和完整型号（例如 ABB IRB120），同一报警在不同型号上的含义可能不同。",
                "error_code": "请提供示教器显示的完整报警码或报警原文；如果没有报警码，请描述故障发生步骤和具体现象。",
                "operating_mode": "故障发生时设备处于自动、手动还是示教模式？",
            }
            agentic_question = state.configuration.get("agentic_preflight", {}).get(
                "clarification_question"
            )
            state.answer = agentic_question or prompts[missing[0]]
            state.final_status = RunStatus.waiting_for_user
            state.stop_reason = "missing_required_slot:%s" % missing[0]
            self._event(
                state,
                "clarification.requested",
                {"missing_slot": missing[0], "question": state.answer},
            )
            return state

        self._record_tool_plan(
            state,
            [
                "lookup_error_code",
                "manual_retrieval",
                "check_safety_constraint",
                "record_diagnostic_state",
            ],
        )
        equipment_brand, equipment_model = split_equipment(slots["equipment"])
        lookup_arguments = self._resolve_tool_arguments(
            state,
            "lookup_error_code",
            {
                "code": slots["error_code"],
                "equipment_brand": equipment_brand,
                "equipment_model": equipment_model,
                "controller_version": slots.get("controller_version"),
            },
        )
        lookup = self._tool(
            state,
            "lookup_error_code",
            lookup_arguments,
            lambda: self.alarm_codes.lookup(
                lookup_arguments["code"],
                lookup_arguments.get("equipment_brand", ""),
                lookup_arguments.get("equipment_model", ""),
                lookup_arguments.get("controller_version"),
            ),
        )
        manual_arguments = self._resolve_tool_arguments(
            state,
            "manual_retrieval",
            {"query": state.normalized_query, "equipment": slots.get("equipment")},
        )
        manual_evidence = self._tool(
            state,
            "manual_retrieval",
            manual_arguments,
            lambda: self.retriever.search(
                manual_arguments["query"],
                top_k=self.settings.retrieval_top_k,
                equipment_model=manual_arguments.get("equipment"),
            ),
        )
        evidence_rejections = {
            citation.document_id: diagnostic_evidence_rejection_reason(
                citation.document_type,
                "%s\n%s" % (citation.title, citation.excerpt),
            )
            for citation in manual_evidence
        }
        rejected_manual_evidence = [
            citation
            for citation in manual_evidence
            if evidence_rejections[citation.document_id]
        ]
        manual_evidence = [
            citation
            for citation in manual_evidence
            if not evidence_rejections[citation.document_id]
        ]
        state.configuration["diagnostic_evidence_filter"] = {
            "policy": "exclude_untrusted_from_judge_and_citations",
            "rejected": [
                {
                    "document_id": citation.document_id,
                    "title": citation.title,
                    "document_type": citation.document_type,
                    "reason": evidence_rejections[citation.document_id],
                    "excerpt_sha256": hashlib.sha256(
                        citation.excerpt.encode("utf-8")
                    ).hexdigest(),
                }
                for citation in rejected_manual_evidence
            ],
        }
        if rejected_manual_evidence:
            self._event(
                state,
                "evidence.quarantined",
                {
                    "count": len(rejected_manual_evidence),
                    "documents": state.configuration["diagnostic_evidence_filter"][
                        "rejected"
                    ],
                },
            )
        matches = lookup["matches"]
        highest_alarm_risk = (
            max(
                (RiskLevel(record["risk_level"]) for record in matches),
                key=lambda item: RISK_PRIORITY[item],
            )
            if matches
            else None
        )
        structured_evidence = [
            self.alarm_codes.citation(record, lookup["status"]) for record in matches
        ]
        seen = {citation.document_id for citation in structured_evidence}
        adopted_manual_evidence = [
            citation for citation in manual_evidence if citation.document_id not in seen
        ][:2]
        if not matches:
            state.configuration["diagnostic_evidence_filter"]["not_adopted"] = [
                {
                    "document_id": citation.document_id,
                    "title": citation.title,
                    "document_type": citation.document_type,
                    "reason": "no_structured_alarm_match",
                }
                for citation in adopted_manual_evidence
            ]
            adopted_manual_evidence = []
        state.retrieved_evidence = structured_evidence + adopted_manual_evidence
        sufficient = lookup["status"] in {
            "exact_match",
            "brand_match_model_unverified",
        } and bool(matches) and all(
            record["review_status"] != "draft" for record in matches
        )
        state.evidence_score = (
            1.0 if lookup["status"] == "exact_match" else 0.82 if sufficient else 0.0
        )
        state.evidence_details = {
            "judge": "structured_alarm_evidence_v1",
            "lookup_status": lookup["status"],
            "sufficient": sufficient,
            "match_count": len(matches),
            "available_scopes": lookup["available_scopes"],
            "rejected_untrusted_evidence_count": len(rejected_manual_evidence),
        }
        llm_supported = None
        if self.agentic_graph and structured_evidence and highest_alarm_risk not in {
            RiskLevel.high,
            RiskLevel.critical,
        }:
            state.evidence_details["llm_evidence_scope"] = "structured_alarm_only"
            llm_supported = self._agentic_evidence_supported(state, structured_evidence)
        elif self.agentic_graph:
            state.evidence_details["llm_support"] = {
                "supported": None,
                "skipped": (
                    "deterministic_high_risk"
                    if highest_alarm_risk in {RiskLevel.high, RiskLevel.critical}
                    else "no_structured_alarm_evidence"
                ),
            }
        authoritative_exact_match = (
            lookup["status"] == "exact_match"
            and bool(matches)
            and all(record["review_status"] == "source_verified" for record in matches)
        )
        if llm_supported is False and authoritative_exact_match:
            state.evidence_details["gate_override"] = {
                "proposed_supported": False,
                "effective_supported": True,
                "reason": "authoritative_exact_match_control_plane",
            }
        elif llm_supported is False:
            sufficient = False
            state.evidence_details["sufficient"] = False
            state.evidence_details["gate_override"] = {
                "proposed_supported": False,
                "effective_supported": False,
                "reason": "llm_lowered_non_authoritative_evidence",
            }
        self._event(
            state,
            "evidence.judged",
            {
                **state.evidence_details,
                "count": len(state.retrieved_evidence),
                "retrieval_strategy": "structured_alarm_lookup",
            },
        )
        safety = self._tool(
            state,
            "check_safety_constraint",
            {
                "query": state.normalized_query,
                "evidence_sufficient": sufficient,
                "alarm_risk": highest_alarm_risk.value if highest_alarm_risk else None,
            },
            lambda: check_safety(state.normalized_query, evidence_sufficient=sufficient),
        )
        if highest_alarm_risk:
            if RISK_PRIORITY[highest_alarm_risk] > RISK_PRIORITY[safety.risk_level]:
                safety = SafetyDecision(
                    highest_alarm_risk,
                    highest_alarm_risk in {RiskLevel.high, RiskLevel.critical},
                    safety.reasons
                    + ["结构化报警记录标记为%s风险" % highest_alarm_risk.value],
                    list(
                        dict.fromkeys(
                            safety.restrictions
                            + [
                                action
                                for record in matches
                                for action in record["forbidden_actions"]
                            ]
                        )
                    ),
                )
        state.risk_level = safety.risk_level
        state.current_hypotheses = list(
            dict.fromkeys(
                cause for record in matches for cause in record["likely_causes"]
            )
        )
        self._event(
            state,
            "safety.checked",
            {
                "risk_level": safety.risk_level.value,
                "must_escalate": safety.must_escalate,
                "reasons": safety.reasons,
                "restrictions": safety.restrictions,
            },
        )
        next_action = (
            "teacher_escalation"
            if safety.must_escalate or not sufficient
            else "collect_check_result"
        )
        self._tool(
            state,
            "record_diagnostic_state",
            {
                "run_id": state.run_id,
                "lookup_status": lookup["status"],
                "next_action": next_action,
            },
            lambda: self.store.record_diagnostic_state(
                state.run_id,
                slots["equipment"],
                slots["error_code"],
                lookup["status"],
                state.current_hypotheses,
                next_action,
            ),
        )
        if safety.must_escalate:
            return self._escalate(state, safety)
        if not sufficient:
            state.final_status = RunStatus.escalated
            state.stop_reason = "alarm_scope_or_evidence_gate_failed"
            state.answer = (
                "结构化报警码库无法对当前设备范围给出唯一、已核验的匹配，状态为“%s”。"
                "我不会根据相似编号或通用资料猜测含义。请核对完整型号、控制器版本和报警原文，"
                "并将问题转交教师或设备专业人员。" % lookup["status"]
            )
            self.store.create_escalation(
                "esc_" + uuid.uuid4().hex,
                state.run_id,
                RiskLevel.medium.value,
                "alarm_lookup_%s" % lookup["status"],
            )
            self._event(
                state,
                "teacher.escalated",
                {"reason": "alarm_lookup_%s" % lookup["status"]},
            )
            return state
        state.answer = self._compose_structured_diagnostic_answer(
            state, matches[0], safety, lookup["status"]
        )
        state.final_status = RunStatus.completed
        state.stop_reason = "diagnostic_evidence_and_safety_passed"
        self._event(
            state,
            "answer.completed",
            {
                "citation_count": len(state.retrieved_evidence),
                "lookup_status": lookup["status"],
            },
        )
        return state

    def _answer_knowledge(self, state: AgentState) -> AgentState:
        self._record_tool_plan(state, ["course_retrieval", "citation_resolver"])
        retrieval_arguments = self._resolve_tool_arguments(
            state,
            "course_retrieval",
            {
                "query": state.normalized_query,
                "top_k": self.settings.retrieval_top_k,
                "strategy": self.settings.retrieval_strategy,
            },
        )
        evidence = self._tool(
            state,
            "course_retrieval",
            retrieval_arguments,
            lambda: self.retriever.search(
                retrieval_arguments["query"], retrieval_arguments["top_k"]
            ),
        )
        state.retrieved_evidence = evidence
        decision = self.evidence_judge.judge(state.normalized_query, evidence)
        state.evidence_score = decision.score
        state.evidence_details = decision.as_dict()
        sufficient = decision.sufficient
        llm_supported = self._agentic_evidence_supported(state)
        if llm_supported is False:
            sufficient = False
            state.evidence_details["sufficient"] = False
            state.evidence_details["gate_override"] = "llm_could_only_lower_confidence"
        self._event(
            state,
            "evidence.judged",
            {**decision.as_dict(), "count": len(evidence), "retrieval_strategy": self.settings.retrieval_strategy},
        )
        safety = check_safety(state.normalized_query, evidence_sufficient=sufficient)
        state.risk_level = safety.risk_level
        if safety.must_escalate:
            return self._escalate(state, safety)
        if not sufficient:
            state.final_status = RunStatus.abstained
            state.stop_reason = "insufficient_evidence"
            state.answer = "当前课程资料中没有找到足够可靠的依据，我先不作推测。请补充具体课程章节、设备型号或问题背景。"
            self._event(state, "answer.abstained", {"reason": "insufficient_evidence"})
            return state
        excerpts = [item.excerpt for item in evidence]
        steps = ordered_steps(excerpts) if any(term in state.normalized_query for term in ("步骤", "流程", "顺序", "怎么")) else []
        if steps:
            body = "\n".join(steps)
        else:
            sentences = best_sentences(state.normalized_query, excerpts)
            body = "\n".join("%d. %s" % (index, sentence) for index, sentence in enumerate(sentences, 1))
        sources = "\n".join(
            "[%d] %s" % (index, citation.title) for index, citation in enumerate(evidence[:3], 1)
        )
        state.answer = "根据当前课程资料：\n%s\n\n资料来源：\n%s" % (body, sources)
        state.final_status = RunStatus.completed
        state.stop_reason = "knowledge_evidence_gate_passed"
        self._event(state, "answer.completed", {"citation_count": len(evidence)})
        return state

    def _tutor(self, state: AgentState) -> AgentState:
        self._record_tool_plan(
            state,
            [
                "get_student_profile",
                "identify_weak_topics",
                "course_retrieval",
                "generate_exercise",
            ],
        )
        profile_arguments = self._resolve_tool_arguments(
            state, "get_student_profile", {"user_id": "[OWNER]"}
        )
        progress = self._tool(
            state,
            "get_student_profile",
            profile_arguments,
            lambda: self.store.student_progress(state.user_id),
        )
        try:
            weak_topic_arguments = self._resolve_tool_arguments(
                state,
                "identify_weak_topics",
                {
                    "query": state.normalized_query,
                    "selection_rule": "explicit_alias_then_lowest_mastery",
                },
            )
            point = self._tool(
                state,
                "identify_weak_topics",
                weak_topic_arguments,
                lambda: self.tutoring.select_knowledge_point(
                    state.user_id, weak_topic_arguments["query"]
                ),
            )
            if not point:
                raise ValueError("没有可用的知识点，请先由教师导入知识点目录")
            retrieval_arguments = self._resolve_tool_arguments(
                state,
                "course_retrieval",
                {
                    "query": point["source_query"],
                    "strategy": self.settings.retrieval_strategy,
                },
            )
            evidence = self._tool(
                state,
                "course_retrieval",
                retrieval_arguments,
                lambda: self.retriever.search(retrieval_arguments["query"], top_k=3),
            )
            exercise = self._tool(
                state,
                "generate_exercise",
                {
                    "query": state.normalized_query,
                    "selection_rule": "explicit_alias_then_lowest_mastery",
                },
                lambda: self.tutoring.generate_exercise(
                    state.user_id,
                    knowledge_point_id=point["knowledge_point_id"],
                    source_run_id=state.run_id,
                    evidence=evidence,
                ),
            )
        except ValueError as exc:
            state.final_status = RunStatus.abstained
            state.answer = "%s。请联系教师补充课程资料或知识点目录。" % str(exc)
            self._event(state, "answer.abstained", {"reason": "exercise_generation_unavailable"})
            return state
        evidence = exercise.pop("evidence")
        state.retrieved_evidence = evidence
        state.evidence_score = evidence[0].score
        state.evidence_details = {
            "grounded": True,
            "knowledge_point_id": exercise["knowledge_point_id"],
            "selection_rule": "explicit_alias_then_lowest_mastery",
            "progress_items_considered": len(progress),
        }
        state.generated_exercise_id = exercise["exercise_id"]
        state.answer = (
            "本次练习聚焦：%s\n\n"
            "题目：%s\n\n"
            "练习编号：%s\n"
            "出题依据：[1] %s\n"
            "提交后会逐项说明已覆盖与待补充要点，并更新该知识点掌握度。"
            % (
                exercise["knowledge_point_name"],
                exercise["question"],
                exercise["exercise_id"],
                exercise["citation"]["title"],
            )
        )
        state.final_status = RunStatus.completed
        state.stop_reason = "tutoring_exercise_grounded"
        self._event(
            state,
            "answer.completed",
            {
                "citation_count": len(evidence),
                "mode": "rule_based_mastery_tutoring",
                "exercise_id": exercise["exercise_id"],
                "knowledge_point_id": exercise["knowledge_point_id"],
            },
        )
        return state

    def _compose_structured_diagnostic_answer(
        self,
        state: AgentState,
        record: Dict[str, Any],
        safety: SafetyDecision,
        lookup_status: str,
    ) -> str:
        scope_note = (
            "当前记录只核验到品牌范围，课程资料未标明该具体型号；以下内容用于辅助判断，不代表型号专用维修结论。"
            if lookup_status == "brand_match_model_unverified"
            else "报警码、品牌和型号范围已精确匹配结构化记录。"
        )
        causes = "\n".join(
            "%d. %s" % (index, item)
            for index, item in enumerate(record["likely_causes"], start=1)
        ) or "1. 当前记录没有列出可核验的候选原因。"
        checks = "\n".join(
            "%d. %s" % (index, item)
            for index, item in enumerate(record["safe_checks"], start=1)
        ) or "1. 保持设备安全状态并记录完整报警原文。"
        restrictions = list(
            dict.fromkeys(record["forbidden_actions"] + safety.restrictions)
        )
        restriction_text = "\n".join("- %s" % item for item in restrictions)
        source = record["source_title"]
        if record.get("source_locator"):
            source += "（%s）" % record["source_locator"]
        return (
            "已确认信息：%s\n风险等级：%s\n匹配结论：%s\n\n"
            "报警含义：%s\n\n候选原因（尚未确认）：\n%s\n\n"
            "可在不改变设备状态的前提下核对：\n%s\n\n安全限制：\n%s\n\n"
            "资料来源：[1] %s\n\n请反馈上述核对结果；若现场报警原文与记录不一致，立即停止并转交教师。"
            % (
                state.collected_slots,
                safety.risk_level.value,
                scope_note,
                record["meaning"],
                causes,
                checks,
                restriction_text or "- 按实训室安全规范操作",
                source,
            )
        )

    def _escalate(self, state: AgentState, decision: SafetyDecision) -> AgentState:
        state.risk_level = decision.risk_level
        state.final_status = RunStatus.escalated
        state.stop_reason = "deterministic_safety_escalation"
        reason_text = "；".join(decision.reasons) or "高风险操作"
        state.answer = (
            "该请求被判定为%s风险：%s。为避免人身或设备伤害，我不会提供确定性操作指令。"
            "请保持设备处于安全状态，并联系教师或具备资质的专业人员处理。"
            % (decision.risk_level.value, reason_text)
        )
        escalation_id = "esc_" + uuid.uuid4().hex
        self.store.create_escalation(escalation_id, state.run_id, decision.risk_level.value, reason_text)
        self._event(
            state,
            "teacher.escalated",
            {
                "escalation_id": escalation_id,
                "risk_level": decision.risk_level.value,
                "reason": reason_text,
                "restrictions": decision.restrictions,
            },
        )
        return state
