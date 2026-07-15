import hashlib
import math
import os
import re
import threading
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from app.config import Settings
from app.retrieval import QUESTION_BANK_MARKERS, tokenize
from app.schemas import RetrievalStrategy
from app.neural import NeuralBackend


@dataclass
class RetrievalIndex:
    fingerprint: Tuple[str, ...]
    chunks: List[Dict[str, Any]]
    token_counts: List[Counter]
    document_frequency: Counter
    average_length: float
    vectorizer: TfidfVectorizer
    dense_documents: np.ndarray
    reducer: Optional[TruncatedSVD]
    neural_documents: Optional[np.ndarray] = None
    neural_cache_key: Optional[str] = None


class LocalRetrievalEngine:
    """Deterministic BM25 + local LSA dense retrieval + RRF + feature rerank."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._index: Optional[RetrievalIndex] = None
        self._lock = threading.RLock()
        self.neural_backend = NeuralBackend(settings)

    def invalidate(self) -> None:
        with self._lock:
            self._index = None

    def prepare(
        self, chunks: List[Dict[str, Any]], strategy: Optional[RetrievalStrategy] = None
    ) -> None:
        index = self._ensure_index(chunks)
        if strategy in {
            RetrievalStrategy.neural_embedding,
            RetrievalStrategy.neural_hybrid,
            RetrievalStrategy.neural_hybrid_rerank,
        }:
            self.neural_backend.prepare_embedding()
            self._ensure_neural_documents(index)
        if strategy == RetrievalStrategy.neural_hybrid_rerank:
            self.neural_backend.prepare_reranker()

    def _build_index(self, chunks: List[Dict[str, Any]]) -> RetrievalIndex:
        fingerprint = tuple(chunk["chunk_id"] for chunk in chunks)
        token_counts = [Counter(chunk["tokens"]) for chunk in chunks]
        document_frequency: Counter = Counter()
        for counts in token_counts:
            document_frequency.update(counts.keys())
        average_length = sum(sum(counts.values()) for counts in token_counts) / max(1, len(chunks))
        vectorizer = TfidfVectorizer(
            analyzer="char",
            ngram_range=(2, 4),
            min_df=1,
            max_features=12000,
            sublinear_tf=True,
            norm="l2",
        )
        sparse_documents = vectorizer.fit_transform(chunk["content"] for chunk in chunks)
        reducer: Optional[TruncatedSVD] = None
        component_count = min(
            self.settings.embedding_dimension,
            max(0, sparse_documents.shape[0] - 1),
            max(0, sparse_documents.shape[1] - 1),
        )
        # SVD is unstable on toy corpora; keep exact TF-IDF vectors until the
        # corpus is large enough to support a meaningful latent space.
        if component_count >= 2 and sparse_documents.shape[0] >= 10:
            reducer = TruncatedSVD(n_components=component_count, random_state=42)
            dense_documents = reducer.fit_transform(sparse_documents)
        else:
            dense_documents = sparse_documents.toarray()
        dense_documents = normalize(dense_documents, norm="l2").astype(np.float32)
        return RetrievalIndex(
            fingerprint,
            chunks,
            token_counts,
            document_frequency,
            average_length,
            vectorizer,
            dense_documents,
            reducer,
        )

    def _ensure_index(self, chunks: List[Dict[str, Any]]) -> RetrievalIndex:
        fingerprint = tuple(chunk["chunk_id"] for chunk in chunks)
        with self._lock:
            if self._index is None or self._index.fingerprint != fingerprint:
                self._index = self._build_index(chunks)
            return self._index

    @staticmethod
    def _normalize_scores(scores: Dict[int, float]) -> Dict[int, float]:
        if not scores:
            return {}
        maximum = max(scores.values())
        if maximum <= 0:
            return {index: 0.0 for index in scores}
        # All callers already omit non-positive candidates. Dividing by the
        # maximum preserves weaker positive evidence; min-max would turn the
        # weakest valid candidate into zero and silently discard it.
        return {index: max(0.0, value / maximum) for index, value in scores.items()}

    def _bm25(self, query_tokens: Sequence[str], index: RetrievalIndex) -> Dict[int, float]:
        query_counts = Counter(query_tokens)
        total = len(index.chunks)
        k1, b = 1.5, 0.75
        scores: Dict[int, float] = {}
        for chunk_index, counts in enumerate(index.token_counts):
            document_length = sum(counts.values())
            score = 0.0
            for token, query_frequency in query_counts.items():
                frequency = counts.get(token, 0)
                if frequency == 0:
                    continue
                frequency_in_documents = index.document_frequency.get(token, 0)
                inverse_document_frequency = math.log(
                    1 + (total - frequency_in_documents + 0.5) / (frequency_in_documents + 0.5)
                )
                denominator = frequency + k1 * (
                    1 - b + b * document_length / max(index.average_length, 1.0)
                )
                score += inverse_document_frequency * (frequency * (k1 + 1) / denominator) * query_frequency
            if score > 0:
                scores[chunk_index] = score
        return scores

    def _embedding(self, query: str, index: RetrievalIndex) -> Dict[int, float]:
        sparse_query = index.vectorizer.transform([query])
        if sparse_query.nnz == 0:
            return {}
        if index.reducer is not None:
            dense_query = index.reducer.transform(sparse_query)
        else:
            dense_query = sparse_query.toarray()
        dense_query = normalize(dense_query, norm="l2").astype(np.float32)
        similarities = np.dot(index.dense_documents, dense_query[0])
        return {
            chunk_index: float(score)
            for chunk_index, score in enumerate(similarities)
            if float(score) > 0.00001
        }

    def _neural_cache_key(self, index: RetrievalIndex) -> str:
        digest = hashlib.sha256()
        digest.update(self.settings.neural_embedding_model.encode("utf-8"))
        digest.update(self.settings.neural_embedding_revision.encode("ascii"))
        for chunk_id in index.fingerprint:
            digest.update(chunk_id.encode("ascii"))
        return digest.hexdigest()[:24]

    def _ensure_neural_documents(self, index: RetrievalIndex) -> np.ndarray:
        cache_key = self._neural_cache_key(index)
        with self._lock:
            if index.neural_documents is not None and index.neural_cache_key == cache_key:
                return index.neural_documents
            model_slug = self.settings.neural_embedding_model.replace("/", "--")
            cache_path = self.settings.neural_index_cache_root / (
                "%s-%s.npz" % (model_slug, cache_key)
            )
            if cache_path.exists():
                try:
                    with np.load(str(cache_path), allow_pickle=False) as cached:
                        chunk_ids = tuple(str(item) for item in cached["chunk_ids"].tolist())
                        vectors = np.asarray(cached["vectors"], dtype=np.float32)
                    if chunk_ids == index.fingerprint and vectors.shape[0] == len(index.chunks):
                        index.neural_documents = vectors
                        index.neural_cache_key = cache_key
                        return vectors
                except Exception:
                    # A partial/stale cache is rebuilt from the pinned model.
                    pass
            vectors = self.neural_backend.encode([chunk["content"] for chunk in index.chunks])
            if vectors.shape[0] != len(index.chunks):
                raise RuntimeError("神经向量数量与知识片段数量不一致")
            self.settings.neural_index_cache_root.mkdir(parents=True, exist_ok=True)
            temporary_path = cache_path.with_suffix(".tmp")
            with temporary_path.open("wb") as handle:
                np.savez_compressed(
                    handle,
                    chunk_ids=np.asarray(index.fingerprint),
                    vectors=vectors,
                )
            os.replace(str(temporary_path), str(cache_path))
            index.neural_documents = vectors
            index.neural_cache_key = cache_key
            return vectors

    def _neural_embedding(self, query: str, index: RetrievalIndex) -> Dict[int, float]:
        documents = self._ensure_neural_documents(index)
        query_vector = self.neural_backend.encode([query])
        if query_vector.size == 0:
            return {}
        similarities = np.dot(documents, query_vector[0])
        return {
            chunk_index: float(score)
            for chunk_index, score in enumerate(similarities)
            if float(score) > 0.00001
        }

    def _cross_encoder_rerank(
        self,
        query: str,
        base_scores: Dict[int, float],
        chunks: List[Dict[str, Any]],
        base_components: Dict[int, Dict[str, float]],
    ) -> Tuple[Dict[int, float], Dict[int, Dict[str, float]]]:
        candidates = sorted(base_scores.items(), key=lambda item: item[1], reverse=True)[
            : self.settings.neural_rerank_top_n
        ]
        candidate_ids = [chunk_index for chunk_index, _ in candidates]
        cross_scores = self.neural_backend.rerank(
            query, [chunks[chunk_index]["content"] for chunk_index in candidate_ids]
        )
        raw_cross = {
            chunk_index: float(score)
            for chunk_index, score in zip(candidate_ids, cross_scores)
        }
        normalized_cross = self._normalize_scores(raw_cross)
        normalized_base = self._normalize_scores(dict(candidates))
        scores = {
            chunk_index: 0.15 * normalized_base.get(chunk_index, 0.0)
            + 0.85 * normalized_cross.get(chunk_index, 0.0)
            for chunk_index in candidate_ids
        }
        components = {
            chunk_index: {
                **base_components.get(chunk_index, {}),
                "base": normalized_base.get(chunk_index, 0.0),
                "cross_encoder": normalized_cross.get(chunk_index, 0.0),
                "cross_encoder_raw": raw_cross.get(chunk_index, 0.0),
            }
            for chunk_index in candidate_ids
        }
        return scores, components

    @staticmethod
    def _rank_positions(scores: Dict[int, float]) -> Dict[int, int]:
        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return {chunk_index: rank for rank, (chunk_index, _) in enumerate(ordered, start=1)}

    def _hybrid(self, bm25: Dict[int, float], embedding: Dict[int, float]) -> Tuple[Dict[int, float], Dict[int, Dict[str, float]]]:
        bm25_ranks = self._rank_positions(bm25)
        embedding_ranks = self._rank_positions(embedding)
        candidates = set(bm25_ranks) | set(embedding_ranks)
        scores: Dict[int, float] = {}
        components: Dict[int, Dict[str, float]] = {}
        for chunk_index in candidates:
            bm25_rrf = self.settings.rrf_bm25_weight / (60 + bm25_ranks[chunk_index]) if chunk_index in bm25_ranks else 0.0
            embedding_rrf = self.settings.rrf_embedding_weight / (60 + embedding_ranks[chunk_index]) if chunk_index in embedding_ranks else 0.0
            scores[chunk_index] = bm25_rrf + embedding_rrf
            components[chunk_index] = {
                "bm25_rrf": bm25_rrf,
                "embedding_rrf": embedding_rrf,
            }
        return scores, components

    @staticmethod
    def _token_coverage(query_tokens: Sequence[str], text: str) -> float:
        significant = {token for token in query_tokens if len(token) > 1 or token.isascii()}
        if not significant:
            return 0.0
        return len(significant & set(tokenize(text))) / len(significant)

    def _rerank(
        self,
        query: str,
        query_tokens: Sequence[str],
        base_scores: Dict[int, float],
        chunks: List[Dict[str, Any]],
        equipment_model: Optional[str],
    ) -> Tuple[Dict[int, float], Dict[int, Dict[str, float]]]:
        normalized_base = self._normalize_scores(base_scores)
        compact_query = re.sub(r"\s+", "", query.lower())
        is_procedure = any(term in query for term in ("步骤", "流程", "顺序", "怎么", "如何"))
        scores: Dict[int, float] = {}
        components: Dict[int, Dict[str, float]] = {}
        for chunk_index, base in normalized_base.items():
            chunk = chunks[chunk_index]
            content_coverage = self._token_coverage(query_tokens, chunk["content"])
            title_coverage = self._token_coverage(query_tokens, chunk["title"])
            exact_phrase = 1.0 if compact_query and compact_query in re.sub(r"\s+", "", chunk["content"].lower()) else 0.0
            procedure_structure = 1.0 if is_procedure and re.search(r"(?m)^\s*\d+[.、]", chunk["content"]) else 0.0
            source_penalty = 1.0 if any(marker in chunk["title"] for marker in QUESTION_BANK_MARKERS) else 0.0
            model_match = 0.0
            metadata_model = str(chunk["metadata"].get("equipment_model") or "")
            if equipment_model and metadata_model:
                model_match = 1.0 if equipment_model.lower() == metadata_model.lower() else -1.0
            final = (
                0.45 * base
                + 0.25 * content_coverage
                + 0.12 * title_coverage
                + 0.08 * exact_phrase
                + 0.06 * procedure_structure
                + 0.08 * model_match
                - 0.06 * source_penalty
            )
            scores[chunk_index] = max(0.0, final)
            components[chunk_index] = {
                "base": base,
                "content_coverage": content_coverage,
                "title_coverage": title_coverage,
                "exact_phrase": exact_phrase,
                "procedure_structure": procedure_structure,
                "model_match": model_match,
                "source_penalty": source_penalty,
            }
        return scores, components

    def rank(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        strategy: RetrievalStrategy,
        top_k: int,
        equipment_model: Optional[str] = None,
    ) -> List[Tuple[float, Dict[str, Any], Dict[str, float]]]:
        if not chunks or not query.strip():
            return []
        index = self._ensure_index(chunks)
        query_tokens = tokenize(query)
        bm25_raw = self._bm25(query_tokens, index)
        components: Dict[int, Dict[str, float]] = {}
        if strategy == RetrievalStrategy.bm25:
            scores = self._normalize_scores(bm25_raw)
            components = {item: {"bm25": score} for item, score in scores.items()}
        elif strategy == RetrievalStrategy.embedding:
            embedding_raw = self._embedding(query, index)
            scores = self._normalize_scores(embedding_raw)
            components = {item: {"embedding": score} for item, score in scores.items()}
        elif strategy == RetrievalStrategy.neural_embedding:
            neural_raw = self._neural_embedding(query, index)
            scores = self._normalize_scores(neural_raw)
            components = {item: {"neural_embedding": score} for item, score in scores.items()}
        else:
            is_neural = strategy in {
                RetrievalStrategy.neural_hybrid,
                RetrievalStrategy.neural_hybrid_rerank,
            }
            embedding_raw = (
                self._neural_embedding(query, index)
                if is_neural
                else self._embedding(query, index)
            )
            hybrid_scores, hybrid_components = self._hybrid(bm25_raw, embedding_raw)
            if is_neural:
                hybrid_components = {
                    item: {
                        "bm25_rrf": values["bm25_rrf"],
                        "neural_embedding_rrf": values["embedding_rrf"],
                    }
                    for item, values in hybrid_components.items()
                }
            if strategy == RetrievalStrategy.hybrid_rerank:
                candidate_ids = dict(
                    sorted(hybrid_scores.items(), key=lambda item: item[1], reverse=True)[
                        : self.settings.retrieval_candidate_k
                    ]
                )
                scores, rerank_components = self._rerank(
                    query, query_tokens, candidate_ids, chunks, equipment_model
                )
                components = {
                    item: {**hybrid_components.get(item, {}), **rerank_components.get(item, {})}
                    for item in scores
                }
            elif strategy == RetrievalStrategy.neural_hybrid_rerank:
                scores, components = self._cross_encoder_rerank(
                    query, hybrid_scores, chunks, hybrid_components
                )
            else:
                scores = self._normalize_scores(hybrid_scores)
                components = hybrid_components
        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        ordered = [(item, score) for item, score in ordered if score > 0][:top_k]
        if not ordered:
            return []
        normalized_final = self._normalize_scores(dict(ordered))
        return [
            (
                normalized_final[chunk_index],
                chunks[chunk_index],
                {key: round(value, 6) for key, value in components.get(chunk_index, {}).items()},
            )
            for chunk_index, _ in ordered
        ]
