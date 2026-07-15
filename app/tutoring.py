import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.retrieval import Retriever
from app.schemas import KnowledgePointRecord
from app.storage import Store


def normalize_answer(value: str) -> str:
    return re.sub(r"[\s，。；、：:,.!?！？（）()\-]", "", value).lower()


class TutoringService:
    """Deterministic, source-grounded exercise generation and grading."""

    def __init__(self, store: Store, retriever: Retriever):
        self.store = store
        self.retriever = retriever

    def import_file(self, path: Path) -> Dict[str, int]:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        raw_records = payload.get("records", payload) if isinstance(payload, dict) else payload
        records = [
            KnowledgePointRecord.model_validate(item).model_dump(mode="json")
            for item in raw_records
        ]
        return self.store.upsert_knowledge_points(records)

    def import_records(self, records: List[KnowledgePointRecord]) -> Dict[str, int]:
        return self.store.upsert_knowledge_points(
            [record.model_dump(mode="json") for record in records]
        )

    def select_knowledge_point(
        self, user_id: str, query: str = "", knowledge_point_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        if knowledge_point_id:
            return self.store.get_knowledge_point(knowledge_point_id)
        points = self.store.list_knowledge_points()
        compact = normalize_answer(query)
        if compact:
            ranked = []
            for point in points:
                terms = [point["name"], *point["aliases"]]
                hits = [len(normalize_answer(term)) for term in terms if normalize_answer(term) in compact]
                if hits:
                    ranked.append((max(hits), point))
            if ranked:
                return max(ranked, key=lambda item: item[0])[1]
        progress = {
            item["knowledge_point_id"]: item
            for item in self.store.student_progress(user_id)
        }
        priority = {"needs_review": 0, "developing": 1, "not_started": 2, "proficient": 3}
        return min(
            points,
            key=lambda point: (
                priority[progress[point["knowledge_point_id"]]["mastery_status"]],
                progress[point["knowledge_point_id"]]["mastery_score"] or 0,
                point["name"],
            ),
            default=None,
        )

    def generate_exercise(
        self,
        user_id: str,
        query: str = "",
        knowledge_point_id: Optional[str] = None,
        difficulty: Optional[str] = None,
        source_run_id: Optional[str] = None,
        evidence: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        point = self.select_knowledge_point(user_id, query, knowledge_point_id)
        if not point:
            raise ValueError("没有可用的知识点，请先由教师导入知识点目录")
        evidence = evidence or self.retriever.search(point["source_query"], top_k=3)
        if not evidence:
            raise ValueError("当前课程资料不足，无法生成可追溯练习")
        citation = evidence[0].model_dump(mode="json")
        exercise = self.store.create_exercise(
            {
                "exercise_id": "ex_" + uuid.uuid4().hex,
                "user_id": user_id,
                "knowledge_point_id": point["knowledge_point_id"],
                "question": point["question_template"],
                "criteria": point["criteria"],
                "citation": citation,
                "difficulty": difficulty or point["difficulty"],
                "source_run_id": source_run_id,
            }
        )
        public = {key: value for key, value in exercise.items() if key != "criteria"}
        public["evidence"] = evidence
        return public

    def grade_answer(self, exercise_id: str, user_id: str, answer: str) -> Dict[str, Any]:
        exercise = self.store.get_exercise(exercise_id, include_private=True)
        if not exercise:
            raise KeyError(exercise_id)
        if exercise["user_id"] != user_id:
            raise PermissionError("不能提交其他学生的练习")
        normalized = normalize_answer(answer)
        matched = []
        missing = []
        criterion_results = []
        for criterion in exercise["criteria"]:
            hit = next(
                (
                    keyword
                    for keyword in criterion["keywords"]
                    if normalize_answer(keyword) in normalized
                ),
                None,
            )
            criterion_results.append(
                {"label": criterion["label"], "matched": hit is not None, "matched_keyword": hit}
            )
            (matched if hit else missing).append(criterion["label"])
        score = round(100 * len(matched) / len(exercise["criteria"]), 2)
        feedback = (
            "已覆盖：%s。待补充：%s。评分依据为教师确认的知识点要点，来源：%s。"
            % (
                "、".join(matched) if matched else "暂无",
                "、".join(missing) if missing else "无",
                exercise["citation"]["title"],
            )
        )
        saved = self.store.save_exercise_attempt(
            {
                "attempt_id": "attempt_" + uuid.uuid4().hex,
                "exercise_id": exercise_id,
                "user_id": user_id,
                "answer": answer,
                "score": score,
                "matched_points": matched,
                "missing_points": missing,
                "criterion_results": criterion_results,
                "feedback": feedback,
            }
        )
        saved["citation"] = exercise["citation"]
        return saved
