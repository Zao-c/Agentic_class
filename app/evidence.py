from dataclasses import dataclass
import re
from typing import Dict, List, Set

from app.retrieval import QUESTION_BANK_MARKERS, tokenize
from app.schemas import Citation


QUERY_STOP_TOKENS = {
    "什么",
    "怎么",
    "如何",
    "哪些",
    "一下",
    "一般",
    "这个",
    "那个",
    "请问",
    "介绍",
    "说明",
    "课程",
    "实验",
    "步骤",
    "流程",
    "指令",
    "含义",
    "工业",
    "机器",
    "器人",
    "机器人",
}

FUNCTION_CHARS = set("的是了在有与和及或为把被让请问这那哪怎什么前后中时需应可")


def significant_query_tokens(query: str) -> Set[str]:
    result = set()
    for token in tokenize(query):
        if not (len(token) > 1 or token.isascii()) or token in QUERY_STOP_TOKENS:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]{2}", token) and any(
            character in FUNCTION_CHARS for character in token
        ):
            continue
        result.add(token)
    return result


def required_exact_entities(query: str) -> Set[str]:
    """Model names and alarm-like numeric codes must occur verbatim in evidence."""
    return {
        match.upper().replace(" ", "")
        for match in re.findall(r"\b(?:[A-Za-z]{2,}[ -]?\d{2,}[A-Za-z-]*|\d{4,8})\b", query)
    }


@dataclass(frozen=True)
class EvidenceDecision:
    score: float
    sufficient: bool
    coverage: float
    retrieval_support: float
    source_quality: float
    source_diversity: float
    conflicts: List[str]
    reasons: List[str]

    def as_dict(self) -> Dict[str, object]:
        return {
            "score": self.score,
            "sufficient": self.sufficient,
            "coverage": self.coverage,
            "retrieval_support": self.retrieval_support,
            "source_quality": self.source_quality,
            "source_diversity": self.source_diversity,
            "conflicts": self.conflicts,
            "reasons": self.reasons,
        }


class EvidenceJudge:
    """Independent, deterministic evidence gate used by Agent and evaluation."""

    def __init__(self, threshold: float):
        self.threshold = threshold

    @staticmethod
    def _detect_conflicts(citations: List[Citation]) -> List[str]:
        excerpts = "\n".join(item.excerpt for item in citations[:5])
        conflicts = []
        if "可以旁路" in excerpts and any(term in excerpts for term in ("禁止旁路", "不得旁路")):
            conflicts.append("旁路安全装置的证据互相冲突")
        if "无需断电" in excerpts and any(term in excerpts for term in ("必须断电", "应断电")):
            conflicts.append("能源隔离要求的证据互相冲突")
        return conflicts

    def judge(self, query: str, citations: List[Citation]) -> EvidenceDecision:
        if not citations:
            return EvidenceDecision(0.0, False, 0.0, 0.0, 0.0, 0.0, [], ["没有召回证据"])
        query_tokens = significant_query_tokens(query)
        evidence_text = "\n".join(item.excerpt for item in citations[:5])
        evidence_tokens = set(tokenize(evidence_text))
        matched_token_count = len(query_tokens & evidence_tokens)
        coverage = matched_token_count / max(1, len(query_tokens))
        minimum_token_matches = (
            1 if len(query_tokens) <= 3 else 2 if len(query_tokens) <= 6 else 3
        )
        required_entities = required_exact_entities(query)
        compact_evidence = re.sub(r"\s+", "", evidence_text.upper())
        missing_entities = sorted(entity for entity in required_entities if entity not in compact_evidence)
        retrieval_support = sum(
            citation.score * weight
            for citation, weight in zip(citations[:3], (0.6, 0.3, 0.1))
        )
        retrieval_support = min(1.0, retrieval_support)
        quality_values = [
            0.55 if any(marker in citation.title for marker in QUESTION_BANK_MARKERS) else 1.0
            for citation in citations[:3]
        ]
        source_quality = sum(quality_values) / len(quality_values)
        source_diversity = min(1.0, len({item.document_id for item in citations[:3]}) / 2)
        conflicts = self._detect_conflicts(citations)
        score = round(
            0.55 * coverage
            + 0.25 * retrieval_support
            + 0.15 * source_quality
            + 0.05 * source_diversity,
            4,
        )
        reasons = []
        if coverage < 0.25 or matched_token_count < minimum_token_matches:
            reasons.append("关键查询词覆盖不足")
        if retrieval_support < 0.35:
            reasons.append("检索支持度不足")
        if conflicts:
            reasons.append("证据存在安全相关冲突")
        if missing_entities:
            reasons.append("证据未精确包含设备型号或报警码：%s" % "、".join(missing_entities))
        if score < self.threshold:
            reasons.append("综合证据分低于阈值")
        sufficient = (
            coverage >= 0.25
            and matched_token_count >= minimum_token_matches
            and retrieval_support >= 0.35
            and score >= self.threshold
            and not conflicts
            and not missing_entities
        )
        if sufficient:
            reasons.append("证据通过阈值和最低覆盖约束")
        return EvidenceDecision(
            score,
            sufficient,
            round(coverage, 4),
            round(retrieval_support, 4),
            round(source_quality, 4),
            round(source_diversity, 4),
            conflicts,
            reasons,
        )
