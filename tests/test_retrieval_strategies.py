from pathlib import Path

from app.config import Settings
from app.evidence import EvidenceJudge
from app.retrieval import Retriever
from app.schemas import Citation
from app.storage import Store
import numpy as np


def build_retriever(tmp_path: Path) -> Retriever:
    settings = Settings(
        database_path=tmp_path / "ranking.db",
        knowledge_root=tmp_path,
        evaluation_root=tmp_path,
        reports_root=tmp_path / "reports",
        auto_ingest=False,
        retrieval_strategy="hybrid_rerank",
        evidence_threshold=0.55,
        neural_index_cache_root=tmp_path / "neural_indexes",
    )
    retriever = Retriever(Store(settings.database_path), settings)
    retriever.import_text(
        "示教编程操作规程",
        """示教编程的一般步骤如下：
1. 检查机器人本体、工具和现场安全条件。
2. 在手动模式下示教目标点位。
3. 设置速度、工具坐标和工件坐标。
4. 采用低速和单步方式试运行，确认轨迹后再正式运行。""",
        document_type="operation_programming",
    )
    retriever.import_text(
        "系统备份规程",
        "系统备份前应确认保存位置，执行备份后检查程序和参数文件是否完整。",
        document_type="operation_programming",
    )
    retriever.import_text(
        "工业机器人教材习题答案",
        "问题：示教编程的步骤是什么？答案：检查后进行示教。",
        document_type="question_bank",
    )
    return retriever


class FakeNeuralBackend:
    @staticmethod
    def encode(texts):
        vectors = []
        for text in texts:
            if "示教" in text or "低速" in text:
                vector = [1.0, 0.1, 0.0]
            elif "备份" in text:
                vector = [0.0, 1.0, 0.1]
            else:
                vector = [0.2, 0.0, 1.0]
            array = np.asarray(vector, dtype=np.float32)
            vectors.append(array / np.linalg.norm(array))
        return np.asarray(vectors, dtype=np.float32)

    @staticmethod
    def rerank(query, documents):
        return np.asarray(
            [0.95 if "低速和单步" in document else 0.2 for document in documents],
            dtype=np.float32,
        )

    @staticmethod
    def prepare_reranker():
        return None


def test_all_retrieval_strategies_return_traceable_citations(tmp_path):
    retriever = build_retriever(tmp_path)
    for strategy in ("bm25", "embedding", "hybrid", "hybrid_rerank"):
        citations = retriever.search("示教编程低速单步调试步骤", strategy=strategy, top_k=3)
        assert citations
        assert citations[0].chunk_id
        assert citations[0].retrieval_method == strategy
        assert citations[0].score_components
        assert any(item.title == "示教编程操作规程" for item in citations)


def test_feature_rerank_exposes_explainable_components(tmp_path):
    citation = build_retriever(tmp_path).search(
        "示教编程的一般步骤是什么？", strategy="hybrid_rerank", top_k=1
    )[0]
    assert citation.score_components["procedure_structure"] == 1.0
    assert citation.score_components["source_penalty"] == 0.0
    assert citation.title == "示教编程操作规程"


def test_neural_embedding_and_cross_encoder_strategies(tmp_path):
    retriever = build_retriever(tmp_path)
    retriever.engine.neural_backend = FakeNeuralBackend()
    for strategy in ("neural_embedding", "neural_hybrid"):
        citations = retriever.search("示教编程低速调试", strategy=strategy, top_k=3)
        assert citations[0].title == "示教编程操作规程"
        assert citations[0].retrieval_method == strategy
    reranked = retriever.search(
        "示教编程低速调试", strategy="neural_hybrid_rerank", top_k=3
    )
    assert reranked[0].title == "示教编程操作规程"
    assert "cross_encoder" in reranked[0].score_components


def test_evidence_judge_accepts_supported_query_and_rejects_unrelated_query(tmp_path):
    citations = build_retriever(tmp_path).search(
        "示教编程低速单步调试步骤", strategy="hybrid_rerank", top_k=3
    )
    judge = EvidenceJudge(0.55)
    accepted = judge.judge("示教编程低速单步调试步骤", citations)
    assert accepted.sufficient is True
    assert accepted.coverage >= 0.5

    rejected = judge.judge("量子引力火星殖民计划", citations)
    assert rejected.sufficient is False
    assert "关键查询词覆盖不足" in rejected.reasons


def test_evidence_judge_rejects_isolated_generic_bigram_matches():
    citations = [
        Citation(
            document_id="safety",
            title="实训安全要求",
            excerpt="焊接区域可能出现火星，所有操作必须符合现场安全要求。",
            score=1.0,
        ),
        Citation(
            document_id="course",
            title="机器人教学规范",
            excerpt="学生应完成工业机器人示教和系统恢复练习。",
            score=0.95,
        ),
    ]
    decision = EvidenceJudge(0.55).judge(
        "量子引力和火星殖民的课程要求是什么？", citations
    )
    assert decision.coverage >= 0.25
    assert decision.sufficient is False
    assert "关键查询词覆盖不足" in decision.reasons


def test_evidence_judge_detects_safety_conflict():
    citations = [
        Citation(document_id="a", title="资料 A", excerpt="维修时可以旁路安全装置。", score=1),
        Citation(document_id="b", title="资料 B", excerpt="任何情况下不得旁路安全装置。", score=0.9),
    ]
    decision = EvidenceJudge(0.1).judge("是否可以旁路安全装置", citations)
    assert decision.sufficient is False
    assert decision.conflicts


def test_evidence_judge_requires_exact_model_and_alarm_code(tmp_path):
    citations = build_retriever(tmp_path).search("机器人报警怎么处理", top_k=3)
    decision = EvidenceJudge(0.1).judge(
        "ABB IRB120 报警 50056 的准确含义是什么？", citations
    )
    assert decision.sufficient is False
    assert any("IRB120" in reason and "50056" in reason for reason in decision.reasons)
