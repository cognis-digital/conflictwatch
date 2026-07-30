"""lessonsindex — an inverted index + ranked search over the 'what's working' lessons KB.

`lessons.query` does substring filtering: fast, but it can't *rank*, doesn't understand
that a two-word query should prefer documents containing both words, and gives no notion of
"which lessons are most about this". This module adds real retrieval over the same KB
(and any list of dict "documents"):

  * an **inverted index** term -> postings (which lessons, how often)
  * **BM25** ranking (the standard Okapi BM25, k1/b tunable) so multi-term queries surface
    the lessons that are genuinely most relevant, length-normalized
  * **snippets** — a highlighted context window around the best-matching term
  * **related lessons** — nearest neighbours by shared-term cosine similarity
  * **keywords** — the most distinctive terms of a lesson by tf-idf

Pure standard library, deterministic, offline. Descriptive retrieval over open,
defensive lessons — awareness tooling, nothing operational.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict

from conflictwatch import lessons as lessons_kb

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-]*")
_STOP = frozenset((
    "the", "a", "an", "of", "in", "on", "at", "to", "and", "or", "for", "with", "by",
    "from", "is", "are", "be", "as", "that", "this", "it", "its", "their", "they",
    "these", "those", "such", "can", "will", "must", "should", "may", "than", "then",
    "into", "over", "under", "not", "no", "but", "which", "who", "when", "where",
    "have", "has", "had", "was", "were", "been", "being", "also", "more", "most",
))

# lesson text fields folded into the searchable document (weighted by repetition)
_FIELDS = ("title", "insight", "category", "indicators", "countermeasures", "tags")


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric/hyphen tokens, stopwords and single chars dropped (order kept)."""
    return [t for t in _TOKEN_RE.findall((text or "").lower())
            if len(t) > 1 and t not in _STOP]


def _doc_text(doc: dict) -> str:
    parts = []
    for f in _FIELDS:
        v = doc.get(f)
        if isinstance(v, (list, tuple)):
            parts.extend(str(x) for x in v)
        elif v:
            parts.append(str(v))
    # title carries a bit more weight — repeat it
    if doc.get("title"):
        parts.append(str(doc["title"]))
    return " ".join(parts)


class LessonIndex:
    """A BM25 inverted index over a list of lesson/document dicts.

    Build once, then :meth:`search`, :meth:`related`, and :meth:`keywords`. Documents
    are addressed by their positional index; the original dicts are kept for result
    payloads. Deterministic: ties break by ascending document index.
    """

    def __init__(self, docs: list[dict], *, k1: float = 1.5, b: float = 0.75):
        self.docs = list(docs)
        self.k1 = k1
        self.b = b
        self._tokens: list[list[str]] = [tokenize(_doc_text(d)) for d in self.docs]
        self._tf: list[Counter] = [Counter(toks) for toks in self._tokens]
        self._len: list[int] = [len(toks) for toks in self._tokens]
        self.avg_len: float = (sum(self._len) / len(self._len)) if self._len else 0.0
        self.postings: dict[str, list[int]] = defaultdict(list)
        for i, tf in enumerate(self._tf):
            for term in tf:
                self.postings[term].append(i)
        self.N = len(self.docs)

    # ------------------------------------------------------------------ #
    def idf(self, term: str) -> float:
        """Okapi BM25 idf (always >= 0) for a term over the corpus."""
        df = len(self.postings.get(term, ()))
        if df == 0:
            return 0.0
        return math.log(1 + (self.N - df + 0.5) / (df + 0.5))

    def _score(self, i: int, q_terms: list[str]) -> float:
        if not self.avg_len:
            return 0.0
        tf = self._tf[i]
        dl = self._len[i]
        s = 0.0
        for t in q_terms:
            f = tf.get(t, 0)
            if not f:
                continue
            denom = f + self.k1 * (1 - self.b + self.b * dl / self.avg_len)
            s += self.idf(t) * (f * (self.k1 + 1)) / denom
        return s

    # ------------------------------------------------------------------ #
    def search(self, query: str, *, k: int = 5, category: str | None = None) -> list[dict]:
        """Rank lessons by BM25 relevance to ``query`` (optionally within a category).

        Returns up to ``k`` hits, each ``{rank, score, index, category, title, snippet,
        matched, lesson}`` sorted by descending score then ascending index. Documents
        with zero score are excluded. A blank query yields no hits.
        """
        q_terms = tokenize(query)
        if not q_terms:
            return []
        q_set = set(q_terms)
        candidates = set()
        for t in q_set:
            candidates.update(self.postings.get(t, ()))
        scored = []
        for i in sorted(candidates):
            if category and self.docs[i].get("category") != category:
                continue
            sc = self._score(i, q_terms)
            if sc > 0:
                scored.append((sc, i))
        scored.sort(key=lambda si: (-si[0], si[1]))
        out = []
        for rank, (sc, i) in enumerate(scored[:k], 1):
            doc = self.docs[i]
            matched = sorted(q_set & set(self._tokens[i]))
            out.append({
                "rank": rank,
                "score": round(sc, 4),
                "index": i,
                "category": doc.get("category", ""),
                "title": doc.get("title", ""),
                "snippet": snippet(_doc_text(doc), matched),
                "matched": matched,
                "lesson": doc,
            })
        return out

    # ------------------------------------------------------------------ #
    def _vector(self, i: int) -> dict:
        """tf-idf vector of document ``i`` (term -> weight)."""
        vec = {}
        for term, f in self._tf[i].items():
            vec[term] = f * self.idf(term)
        return vec

    def related(self, index: int, *, k: int = 3) -> list[dict]:
        """The ``k`` lessons most similar to lesson ``index`` by tf-idf cosine similarity.

        Returns ``{index, title, category, similarity, lesson}`` items, most similar
        first, excluding the query lesson itself. Deterministic (index tiebreak).
        """
        if not (0 <= index < self.N):
            raise IndexError(f"lesson index {index} out of range 0..{self.N - 1}")
        base = self._vector(index)
        bnorm = math.sqrt(sum(v * v for v in base.values())) or 1.0
        sims = []
        for j in range(self.N):
            if j == index:
                continue
            other = self._vector(j)
            if not other:
                continue
            dot = sum(base.get(t, 0.0) * w for t, w in other.items())
            onorm = math.sqrt(sum(v * v for v in other.values())) or 1.0
            sim = dot / (bnorm * onorm)
            if sim > 0:
                sims.append((sim, j))
        sims.sort(key=lambda sj: (-sj[0], sj[1]))
        out = []
        for sim, j in sims[:k]:
            d = self.docs[j]
            out.append({"index": j, "title": d.get("title", ""),
                        "category": d.get("category", ""),
                        "similarity": round(sim, 4), "lesson": d})
        return out

    def keywords(self, index: int, *, n: int = 6) -> list[str]:
        """The ``n`` most distinctive terms of lesson ``index`` by tf-idf weight."""
        vec = self._vector(index)
        return [t for t, _ in sorted(vec.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]

    def vocabulary(self) -> list[str]:
        """Sorted list of every indexed term (the corpus vocabulary)."""
        return sorted(self.postings)


# --------------------------------------------------------------------------- #
# snippet highlighting
# --------------------------------------------------------------------------- #
def snippet(text: str, terms, *, width: int = 160, mark: str = "**") -> str:
    """A context window around the first matched term, with matches ``**highlighted**``.

    Falls back to the head of the text when nothing matches. Whitespace is collapsed.
    """
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return ""
    terms = [t for t in (terms or []) if t]
    lower = text.lower()
    pos = -1
    for t in terms:
        p = lower.find(t.lower())
        if p != -1 and (pos == -1 or p < pos):
            pos = p
    if pos == -1:
        out = text[:width]
        return out + ("…" if len(text) > width else "")
    start = max(0, pos - width // 3)
    end = min(len(text), start + width)
    frag = text[start:end]
    for t in sorted(set(terms), key=len, reverse=True):
        frag = re.sub(r"(?i)\b(" + re.escape(t) + r")\b", mark + r"\1" + mark, frag)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + frag + suffix


# --------------------------------------------------------------------------- #
# module-level convenience over the bundled lessons KB
# --------------------------------------------------------------------------- #
def build_index(lessons: list[dict] | None = None, **kwargs) -> LessonIndex:
    """Build a :class:`LessonIndex` over the bundled lessons KB (or a supplied list)."""
    return LessonIndex(lessons if lessons is not None else lessons_kb.load(), **kwargs)


def search(query: str, *, k: int = 5, category: str | None = None,
           lessons: list[dict] | None = None) -> list[dict]:
    """One-shot ranked search over the lessons KB (builds a fresh index)."""
    return build_index(lessons).search(query, k=k, category=category)
