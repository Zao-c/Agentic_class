import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Sequence

import numpy as np

from app.config import Settings


class NeuralBackendError(RuntimeError):
    pass


class NeuralBackend:
    """Lazy, revision-pinned, local-only Sentence Transformers backend."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._embedding_model = None
        self._reranker = None
        self._lock = threading.RLock()
        # PyTorch CPU inference on Windows can spend tens of seconds rebuilding
        # thread-local runtime state when a loaded model is called from an
        # arbitrary worker. Keep model loading and every inference on one
        # dedicated thread; Agent tools may still run in their timeout workers.
        self._inference_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="neural-inference"
        )

    def _snapshot(self, model_id: str, revision: str) -> Path:
        try:
            from huggingface_hub import snapshot_download

            return Path(
                snapshot_download(
                    repo_id=model_id,
                    revision=revision,
                    cache_dir=str(self.settings.hf_cache_dir),
                    local_files_only=self.settings.neural_local_files_only,
                )
            )
        except Exception as exc:
            raise NeuralBackendError(
                "模型 %s@%s 不在本地缓存中，或缓存不完整：%s" % (model_id, revision, exc)
            ) from exc

    def _get_embedding_model(self):
        with self._lock:
            if self._embedding_model is None:
                try:
                    from sentence_transformers import SentenceTransformer

                    snapshot = self._snapshot(
                        self.settings.neural_embedding_model,
                        self.settings.neural_embedding_revision,
                    )
                    self._embedding_model = SentenceTransformer(
                        str(snapshot),
                        device=self.settings.neural_device,
                        local_files_only=True,
                    )
                except NeuralBackendError:
                    raise
                except Exception as exc:
                    raise NeuralBackendError("神经 Embedding 模型加载失败：%s" % exc) from exc
            return self._embedding_model

    def _get_reranker(self):
        with self._lock:
            if self._reranker is None:
                try:
                    from sentence_transformers import CrossEncoder

                    snapshot = self._snapshot(
                        self.settings.neural_reranker_model,
                        self.settings.neural_reranker_revision,
                    )
                    self._reranker = CrossEncoder(
                        str(snapshot),
                        device=self.settings.neural_device,
                        local_files_only=True,
                    )
                except NeuralBackendError:
                    raise
                except Exception as exc:
                    raise NeuralBackendError("Cross-Encoder 模型加载失败：%s" % exc) from exc
            return self._reranker

    def _encode_sync(self, texts: Sequence[str]) -> np.ndarray:
        model = self._get_embedding_model()
        try:
            vectors = model.encode(
                list(texts),
                batch_size=self.settings.neural_batch_size,
                show_progress_bar=False,
                normalize_embeddings=True,
                convert_to_numpy=True,
            )
            return np.asarray(vectors, dtype=np.float32)
        except Exception as exc:
            raise NeuralBackendError("神经 Embedding 编码失败：%s" % exc) from exc

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, 0), dtype=np.float32)
        return self._inference_executor.submit(self._encode_sync, list(texts)).result()

    def prepare_embedding(self) -> None:
        # Loading weights is not enough on Windows CPU: the first encode in a
        # worker may initialize PyTorch runtime state for tens of seconds.
        # Perform one real inference before the service reports ready.
        self._inference_executor.submit(
            self._encode_sync, ["工业机器人课程检索预热"]
        ).result()

    def prepare_reranker(self) -> None:
        self._inference_executor.submit(
            self._rerank_sync,
            "工业机器人课程检索预热",
            ["工业机器人课程资料"],
        ).result()

    def _rerank_sync(self, query: str, documents: Sequence[str]) -> np.ndarray:
        model = self._get_reranker()
        pairs: List[List[str]] = [[query, document] for document in documents]
        try:
            scores = model.predict(
                pairs,
                batch_size=self.settings.neural_batch_size,
                show_progress_bar=False,
            )
            return np.asarray(scores, dtype=np.float32).reshape(-1)
        except Exception as exc:
            raise NeuralBackendError("Cross-Encoder 精排失败：%s" % exc) from exc

    def rerank(self, query: str, documents: Sequence[str]) -> np.ndarray:
        if not documents:
            return np.empty((0,), dtype=np.float32)
        return self._inference_executor.submit(
            self._rerank_sync, query, list(documents)
        ).result()
