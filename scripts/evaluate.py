import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.evaluation import EvaluationService
from app.retrieval import Retriever
from app.storage import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="运行可复现检索评测")
    parser.add_argument("--dataset", default=None, help="data/eval 下的 CSV 文件名")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--strategy",
        choices=[
            "bm25",
            "embedding",
            "hybrid",
            "hybrid_rerank",
            "neural_embedding",
            "neural_hybrid",
            "neural_hybrid_rerank",
        ],
        default="hybrid_rerank",
    )
    parser.add_argument("--compare", action="store_true", help="依次运行四种检索策略并生成消融报告")
    parser.add_argument("--neural", action="store_true", help="对比时同时运行本地缓存的神经模型策略")
    args = parser.parse_args()
    settings.ensure_directories()
    store = Store(settings.database_path)
    retriever = Retriever(store, settings)
    if store.count_chunks() == 0:
        retriever.import_directory(settings.knowledge_root, include_binary=False)
    evaluator = EvaluationService(
        retriever,
        settings.evaluation_root,
        settings.reports_root,
        settings.evidence_threshold,
    )
    if args.compare:
        report = evaluator.run_comparison(args.dataset, args.limit, include_neural=args.neural)
        summary = {
            "comparison_id": report["comparison_id"],
            "strategies": report["strategies"],
            "report_files": report["report_files"],
        }
    else:
        report = evaluator.run(args.dataset, args.limit, args.strategy)
        summary = {
            "evaluation_run_id": report["evaluation_run_id"],
            "metrics": report["metrics"],
            "report_files": report["report_files"],
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
