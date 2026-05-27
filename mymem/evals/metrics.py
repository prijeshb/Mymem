"""
Pure metric functions — stdlib only, no I/O, no LLM calls.

Implements: TF-IDF cosine similarity, BM25, ROUGE-1 F1, duplicate rate.
"""
from __future__ import annotations

import math
import re
from collections import Counter


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, strip punctuation."""
    return re.findall(r"\b\w+\b", text.lower())


def tfidf_cosine(a: str, b: str) -> float:
    """TF-IDF cosine similarity between two strings. Range [0, 1]."""
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    cta, ctb = Counter(ta), Counter(tb)
    vocab = set(cta) | set(ctb)
    dot = sum(cta[t] * ctb[t] for t in vocab)
    mag_a = math.sqrt(sum(v * v for v in cta.values()))
    mag_b = math.sqrt(sum(v * v for v in ctb.values()))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def bm25_score(query: str, document: str, k1: float = 1.5, b: float = 0.75) -> float:
    """BM25 relevance of document given query. Higher = more relevant."""
    q_tokens = tokenize(query)
    d_tokens = tokenize(document)
    if not q_tokens or not d_tokens:
        return 0.0
    doc_len = len(d_tokens)
    doc_tf = Counter(d_tokens)
    score = 0.0
    for term in q_tokens:
        tf = doc_tf.get(term, 0)
        if tf == 0:
            continue
        idf = math.log(1.0 + 1.0 / (1.0 + tf))
        score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / max(doc_len, 1)))
    return score


def rouge1_f1(reference: str, hypothesis: str) -> float:
    """ROUGE-1 F1 (unigram overlap). Range [0, 1]."""
    ref = set(tokenize(reference))
    hyp = set(tokenize(hypothesis))
    if not ref or not hyp:
        return 0.0
    overlap = len(ref & hyp)
    p = overlap / len(hyp)
    r = overlap / len(ref)
    return (2 * p * r / (p + r)) if (p + r) else 0.0


def duplicate_rate(texts: list[str], threshold: float = 0.7) -> float:
    """
    Fraction of texts that are near-duplicates of at least one other text.
    Uses TF-IDF cosine similarity.
    """
    if len(texts) < 2:
        return 0.0
    flagged = set()
    for i, a in enumerate(texts):
        for j, b in enumerate(texts):
            if j <= i:
                continue
            if tfidf_cosine(a, b) >= threshold:
                flagged.add(i)
                flagged.add(j)
    return len(flagged) / len(texts)
