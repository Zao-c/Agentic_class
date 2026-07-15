import threading
from pathlib import Path

import numpy as np
import pytest

from app.config import Settings
from app.neural import NeuralBackend, NeuralBackendError


class FakeEmbeddingModel:
    def __init__(self):
        self.thread_ids = []

    def encode(self, texts, **kwargs):
        self.thread_ids.append(threading.get_ident())
        return np.asarray([[1.0, float(index + 1)] for index, _ in enumerate(texts)], dtype=np.float32)


class FakeReranker:
    def __init__(self):
        self.thread_ids = []

    def predict(self, pairs, **kwargs):
        self.thread_ids.append(threading.get_ident())
        return np.asarray([float(index) for index, _ in enumerate(pairs)], dtype=np.float32)


def test_neural_inference_is_warmed_and_kept_on_one_thread(tmp_path: Path, monkeypatch):
    backend = NeuralBackend(Settings(neural_index_cache_root=tmp_path))
    embedding = FakeEmbeddingModel()
    reranker = FakeReranker()
    monkeypatch.setattr(backend, "_get_embedding_model", lambda: embedding)
    monkeypatch.setattr(backend, "_get_reranker", lambda: reranker)
    try:
        backend.prepare_embedding()
        vectors = backend.encode(["问题一", "问题二"])
        backend.prepare_reranker()
        scores = backend.rerank("问题", ["资料一", "资料二"])

        assert vectors.shape == (2, 2)
        assert scores.tolist() == [0.0, 1.0]
        assert len(embedding.thread_ids) == 2
        assert len(reranker.thread_ids) == 2
        assert len(set(embedding.thread_ids + reranker.thread_ids)) == 1
        assert backend.encode([]).shape == (0, 0)
        assert backend.rerank("问题", []).shape == (0,)
    finally:
        backend._inference_executor.shutdown(wait=True)


def test_neural_encode_failure_is_structured(tmp_path: Path, monkeypatch):
    backend = NeuralBackend(Settings(neural_index_cache_root=tmp_path))

    class BrokenModel:
        def encode(self, texts, **kwargs):
            raise RuntimeError("broken encoder")

    monkeypatch.setattr(backend, "_get_embedding_model", lambda: BrokenModel())
    try:
        with pytest.raises(NeuralBackendError, match="编码失败"):
            backend.encode(["问题"])
    finally:
        backend._inference_executor.shutdown(wait=True)

