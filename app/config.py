import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "工业机器人课程智能助教")
    environment: str = os.getenv("APP_ENV", "development")
    database_path: Path = Path(
        os.getenv("DATABASE_PATH", str(PROJECT_ROOT / "runtime" / "robot_tutor.db"))
    )
    knowledge_root: Path = Path(
        os.getenv("KNOWLEDGE_ROOT", str(PROJECT_ROOT / "data" / "active"))
    )
    evaluation_root: Path = Path(
        os.getenv("EVALUATION_ROOT", str(PROJECT_ROOT / "data" / "eval"))
    )
    alarm_code_data_path: Path = Path(
        os.getenv(
            "ALARM_CODE_DATA_PATH",
            str(PROJECT_ROOT / "data" / "structured" / "alarm_codes_v1.json"),
        )
    )
    knowledge_point_data_path: Path = Path(
        os.getenv(
            "KNOWLEDGE_POINT_DATA_PATH",
            str(PROJECT_ROOT / "data" / "structured" / "knowledge_points_v1.json"),
        )
    )
    reports_root: Path = Path(
        os.getenv("REPORTS_ROOT", str(PROJECT_ROOT / "reports"))
    )
    auto_ingest: bool = _bool_env("AUTO_INGEST", True)
    auto_ingest_alarm_codes: bool = _bool_env("AUTO_INGEST_ALARM_CODES", True)
    auto_ingest_knowledge_points: bool = _bool_env("AUTO_INGEST_KNOWLEDGE_POINTS", True)
    ingest_binary_documents: bool = _bool_env("INGEST_BINARY_DOCUMENTS", False)
    retrieval_top_k: int = int(os.getenv("RETRIEVAL_TOP_K", "5"))
    retrieval_strategy: str = os.getenv("RETRIEVAL_STRATEGY", "hybrid_rerank")
    retrieval_candidate_k: int = int(os.getenv("RETRIEVAL_CANDIDATE_K", "30"))
    embedding_dimension: int = int(os.getenv("EMBEDDING_DIMENSION", "128"))
    rrf_bm25_weight: float = float(os.getenv("RRF_BM25_WEIGHT", "0.55"))
    rrf_embedding_weight: float = float(os.getenv("RRF_EMBEDDING_WEIGHT", "0.45"))
    hf_cache_dir: Path = Path(
        os.getenv(
            "HF_CACHE_DIR",
            str(Path(os.getenv("HF_HOME", str(Path.home() / ".cache" / "huggingface"))) / "hub"),
        )
    )
    neural_index_cache_root: Path = Path(
        os.getenv("NEURAL_INDEX_CACHE_ROOT", str(PROJECT_ROOT / "runtime" / "neural_indexes"))
    )
    neural_embedding_model: str = os.getenv("NEURAL_EMBEDDING_MODEL", "BAAI/bge-small-zh")
    neural_embedding_revision: str = os.getenv(
        "NEURAL_EMBEDDING_REVISION", "1d2363c5de6ce9ba9c890c8e23a4c72dce540ca8"
    )
    neural_reranker_model: str = os.getenv("NEURAL_RERANKER_MODEL", "BAAI/bge-reranker-base")
    neural_reranker_revision: str = os.getenv(
        "NEURAL_RERANKER_REVISION", "2cfc18c9415c912f9d8155881c133215df768a70"
    )
    neural_local_files_only: bool = _bool_env("NEURAL_LOCAL_FILES_ONLY", True)
    neural_device: str = os.getenv("NEURAL_DEVICE", "cpu")
    neural_batch_size: int = int(os.getenv("NEURAL_BATCH_SIZE", "16"))
    neural_rerank_top_n: int = int(os.getenv("NEURAL_RERANK_TOP_N", "8"))
    evidence_threshold: float = float(os.getenv("EVIDENCE_THRESHOLD", "0.55"))
    max_agent_steps: int = int(os.getenv("MAX_AGENT_STEPS", "8"))
    max_retries: int = int(os.getenv("MAX_RETRIES", "1"))
    tool_timeout_seconds: float = float(os.getenv("TOOL_TIMEOUT_SECONDS", "10"))
    agent_profile: str = os.getenv("AGENT_PROFILE", "portable")
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4.1-mini")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")
    llm_api_key_env: str = os.getenv("LLM_API_KEY_ENV", "OPENAI_API_KEY")
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "20"))
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "1"))
    llm_structured_output_method: str = os.getenv(
        "LLM_STRUCTURED_OUTPUT_METHOD", "json_schema"
    )
    llm_thinking_mode: str = os.getenv("LLM_THINKING_MODE", "")
    llm_input_cost_per_million: float = float(
        os.getenv("LLM_INPUT_COST_PER_MILLION", "0")
    )
    llm_output_cost_per_million: float = float(
        os.getenv("LLM_OUTPUT_COST_PER_MILLION", "0")
    )
    agentic_fallback_to_portable: bool = _bool_env(
        "AGENTIC_FALLBACK_TO_PORTABLE", True
    )
    cors_origins: str = os.getenv("CORS_ORIGINS", "http://localhost:8000")

    def ensure_directories(self) -> None:
        if self.agent_profile not in {"portable", "agentic-online", "agentic-quality"}:
            raise ValueError("AGENT_PROFILE 必须是 portable、agentic-online 或 agentic-quality")
        if self.llm_structured_output_method not in {
            "json_schema",
            "json_mode",
            "function_calling",
        }:
            raise ValueError("LLM_STRUCTURED_OUTPUT_METHOD 配置无效")
        if self.llm_thinking_mode not in {"", "enabled", "disabled"}:
            raise ValueError("LLM_THINKING_MODE 必须为空、enabled 或 disabled")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.reports_root.mkdir(parents=True, exist_ok=True)
        self.neural_index_cache_root.mkdir(parents=True, exist_ok=True)


settings = Settings()
