"""
Person C — Stage 6: dense (semantic) index over evidence units.

Text embedded for each unit is *enriched with concept metadata* (canonical
name, aliases, description from the sector schema), per the spec.

Backends (same interface, chosen automatically):
  * sentence-transformers  — if installed and SECTORVIEW_DENSE=sbert
  * TF-IDF char n-grams    — scikit-learn, no model download (default, local/CI)
  * hashed char n-grams    — pure numpy last resort

Vectors are L2-normalised, so cosine similarity is a matrix product. For a
corpus of a few thousand units a flat numpy matrix is exact and fast; the
`search` interface is the same one a FAISS IndexFlatIP would expose.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np


class _TfidfBackend:
    name = "tfidf-char-ngram"

    def __init__(self, texts: Sequence[str]):
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5),
                                   lowercase=True, sublinear_tf=True)
        self.matrix = self.vec.fit_transform(texts)          # sparse, L2-normalised

    def encode_query(self, q: str):
        return self.vec.transform([q])

    def similarities(self, qv) -> np.ndarray:
        return np.asarray((self.matrix @ qv.T).todense()).ravel()

    @property
    def nbytes(self) -> int:
        m = self.matrix
        return m.data.nbytes + m.indices.nbytes + m.indptr.nbytes


class _HashBackend:
    name = "hashed-char-ngram"
    DIM = 1024

    def __init__(self, texts: Sequence[str]):
        self.matrix = np.vstack([self._enc(t) for t in texts]) if texts else np.zeros((0, self.DIM))

    def _enc(self, text: str) -> np.ndarray:
        v = np.zeros(self.DIM, dtype=np.float32)
        t = f"  {text.lower()}  "
        for i in range(len(t) - 2):
            v[hash(t[i:i + 3]) % self.DIM] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def encode_query(self, q: str):
        return self._enc(q)

    def similarities(self, qv) -> np.ndarray:
        return self.matrix @ qv

    @property
    def nbytes(self) -> int:
        return self.matrix.nbytes


class _SBertBackend:
    def __init__(self, texts: Sequence[str]):
        from sentence_transformers import SentenceTransformer

        model_name = os.environ.get("SECTORVIEW_SBERT_MODEL", "all-MiniLM-L6-v2")
        self.name = f"sbert:{model_name}"
        self.model = SentenceTransformer(model_name)
        self.matrix = self.model.encode(list(texts), normalize_embeddings=True)

    def encode_query(self, q: str):
        return self.model.encode([q], normalize_embeddings=True)[0]

    def similarities(self, qv) -> np.ndarray:
        return self.matrix @ qv

    @property
    def nbytes(self) -> int:
        return self.matrix.nbytes


def _make_backend(texts: Sequence[str]):
    if os.environ.get("SECTORVIEW_DENSE") == "sbert":
        try:
            return _SBertBackend(texts)
        except Exception:                      # model/dep missing -> fall through
            pass
    try:
        return _TfidfBackend(texts)
    except Exception:
        return _HashBackend(texts)


class DenseIndex:
    def __init__(self, units: Sequence[Dict[str, Any]], texts: Sequence[str]):
        t0 = time.perf_counter()
        self.units = list(units)
        self.backend = _make_backend(texts)
        self.build_time = time.perf_counter() - t0

    @property
    def backend_name(self) -> str:
        return self.backend.name

    @property
    def num_units(self) -> int:
        return len(self.units)

    @property
    def index_size(self) -> int:
        return int(self.backend.nbytes)

    def search(
        self, query: str, k: int = 10, allowed: Optional[Set[int]] = None
    ) -> List[Tuple[int, float]]:
        if not self.units:
            return []
        sims = self.backend.similarities(self.backend.encode_query(query))
        idx = range(len(sims)) if allowed is None else sorted(allowed)
        ranked = sorted(((i, float(sims[i])) for i in idx), key=lambda x: (-x[1], x[0]))
        return ranked[:k]
