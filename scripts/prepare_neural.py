import argparse
import json
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(
        description="只使用本地模型缓存预热神经检索并构建课程向量索引"
    )
    parser.add_argument(
        "--strategy",
        choices=["neural_embedding", "neural_hybrid", "neural_hybrid_rerank"],
        default=None,
    )
    parser.add_argument(
        "--profile",
        choices=["neural-online", "neural-quality"],
        default="neural-online",
    )
    parser.add_argument("--include-binary", action="store_true")
    args = parser.parse_args()

    import sys

    sys.path.insert(0, str(PROJECT_ROOT))
    from scripts.run_profile import load_profile

    values = load_profile(args.profile)
    strategy = args.strategy or values["RETRIEVAL_STRATEGY"]

    from app.config import settings
    from app.retrieval import Retriever
    from app.storage import Store

    settings.ensure_directories()
    store = Store(settings.database_path)
    retriever = Retriever(store, settings)
    if store.count_chunks() == 0:
        retriever.import_directory(
            settings.knowledge_root,
            include_binary=args.include_binary,
        )

    started = time.perf_counter()
    retriever.prepare(strategy)
    elapsed_ms = (time.perf_counter() - started) * 1000
    print(
        json.dumps(
            {
                "status": "ready",
                "profile": args.profile,
                "strategy": strategy,
                "indexed_chunks": store.count_chunks(),
                "prepare_ms": round(elapsed_ms, 2),
                "embedding_model": settings.neural_embedding_model,
                "embedding_revision": settings.neural_embedding_revision,
                "reranker_model": (
                    settings.neural_reranker_model
                    if strategy == "neural_hybrid_rerank"
                    else None
                ),
                "reranker_revision": (
                    settings.neural_reranker_revision
                    if strategy == "neural_hybrid_rerank"
                    else None
                ),
                "local_files_only": settings.neural_local_files_only,
                "device": settings.neural_device,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
