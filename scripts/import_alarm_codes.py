import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.alarm_codes import AlarmCodeService
from app.config import settings
from app.storage import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="导入版本化结构化报警码数据")
    parser.add_argument("--path", default=str(settings.alarm_code_data_path))
    args = parser.parse_args()

    source = Path(args.path)
    if not source.is_absolute():
        source = (PROJECT_ROOT / source).resolve()
    result = AlarmCodeService(Store(settings.database_path)).import_file(source)
    print(
        json.dumps(
            {"source": str(source), **result}, ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
