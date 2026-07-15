import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.main import app


def main() -> None:
    parser = argparse.ArgumentParser(description="导出当前 FastAPI OpenAPI 文档")
    parser.add_argument(
        "--output", default=str(PROJECT_ROOT / "docs" / "openapi.json")
    )
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = (PROJECT_ROOT / output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    schema = app.openapi()
    output.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "version": schema["info"]["version"],
                "path_count": len(schema["paths"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
