import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.retrieval import Retriever
from app.storage import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="导入工业机器人课程知识库")
    parser.add_argument("--include-binary", action="store_true", help="同时解析 PDF、DOCX 和 PPTX")
    args = parser.parse_args()
    settings.ensure_directories()
    store = Store(settings.database_path)
    result = Retriever(store, settings).import_directory(settings.knowledge_root, args.include_binary)
    result["indexed_chunks"] = store.count_chunks()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
