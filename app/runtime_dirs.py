import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def isolated_directory(root: Path, prefix: str) -> Iterator[Path]:
    """Create an inheriting-ACL work directory and clean only that directory."""
    resolved_root = Path(root).resolve()
    resolved_root.mkdir(parents=True, exist_ok=True)
    candidate = (resolved_root / (prefix + uuid.uuid4().hex)).resolve()
    if candidate.parent != resolved_root:
        raise ValueError("隔离目录必须位于配置的运行根目录内")
    candidate.mkdir()
    try:
        yield candidate
    finally:
        if candidate.parent == resolved_root and candidate.exists():
            shutil.rmtree(candidate, ignore_errors=True)
