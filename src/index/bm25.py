"""
Person C — Stage 6: BM25 (lexical) index over evidence units.

Dependency-free Okapi BM25. The corpus is a few hundred to a few thousand
evidence units (Person A's output), so an in-memory inverted index is enough
locally; the same `search(query, k, allowed)` interface can be backed by
OpenSearch later without touching the callers.

Records build_time, index_size and num_units (spec Stage 6).
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter, defaultdict
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> List[str]:
    """Lowercase word tokens. `R&D` -> r, and, d  (same rule for docs and queries);
    CamelCase metric names (ResearchAndDevelopmentExpense) are split first."""
    text = _CAMEL.sub(" ", text or "")
    text = text.replace("&", " and ")
    return _TOKEN.findall(text.lower())


def unit_text(u: Dict[str, Any], concept_meta: Optional[Dict[str, Any]] = None) -> str:
    """Searchable text of one evidence unit: labels + metadata, never the number.

    `concept_meta` (aliases/description from the sector schema) enriches the
    text so a query for "R&D" reaches the row labelled "Research and development".
    """
    parts = [
        u.get("row_label"), u.get("col_label"), u.get("section_path"),
        u.get("metric"), u.get("concept"), u.get("ticker"),
        u.get("unit_type"), u.get("table_id"),
    ]
    if concept_meta:
        parts.append(concept_meta.get("canonical_name"))
        parts.extend(concept_meta.get("aliases") or [])
        parts.append(concept_meta.get("description"))
    return " ".join(str(p) for p in parts if p)


class BM25Index:
    def __init__(
        self,
        units: Sequence[Dict[str, Any]],
        texts: Optional[Sequence[str]] = None,
        k1: float = 1.5,
        b: float = 0.75,
    ):
        t0 = time.perf_counter()
        self.units = list(units)
        self.k1, self.b = k1, b
        texts = texts if texts is not None else [unit_text(u) for u in self.units]
        self.doc_tf: List[Counter] = [Counter(tokenize(t)) for t in texts]
        self.doc_len = [sum(tf.values()) for tf in self.doc_tf]
        self.avgdl = (sum(self.doc_len) / len(self.doc_len)) if self.doc_len else 0.0
        self.postings: Dict[str, List[int]] = defaultdict(list)
        for i, tf in enumerate(self.doc_tf):
            for term in tf:
                self.postings[term].append(i)
        n = len(self.units)
        self.idf = {
            t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5))
            for t, p in self.postings.items()
        }
        self.build_time = time.perf_counter() - t0

    @property
    def num_units(self) -> int:
        return len(self.units)

    @property
    def index_size(self) -> int:
        """Approximate in-memory footprint: postings entries + stored term freqs."""
        return sum(len(p) for p in self.postings.values()) * 8 + \
            sum(len(tf) for tf in self.doc_tf) * 16

    def score(self, query: str, allowed: Optional[Set[int]] = None) -> Dict[int, float]:
        q_terms = tokenize(query)
        scores: Dict[int, float] = defaultdict(float)
        for term in set(q_terms):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i in self.postings[term]:
                if allowed is not None and i not in allowed:
                    continue
                tf = self.doc_tf[i][term]
                denom = tf + self.k1 * (1 - self.b + self.b * self.doc_len[i] / (self.avgdl or 1))
                scores[i] += idf * tf * (self.k1 + 1) / denom
        return scores

    def search(
        self, query: str, k: int = 10, allowed: Optional[Set[int]] = None
    ) -> List[Tuple[int, float]]:
        ranked = sorted(self.score(query, allowed).items(), key=lambda x: (-x[1], x[0]))
        return ranked[:k]
