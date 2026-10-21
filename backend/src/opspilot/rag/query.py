"""Deterministic query analysis: classification and lexical query construction.

This is a rule-based classifier, not a model. It is cheap, explainable, and evaluated in tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]*")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "documented",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "please",
        "should",
        "so",
        "tell",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "this",
        "to",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    ]
)

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "procedure",
        re.compile(r"\b(how (do|to|should)|procedure|steps?|runbook|playbook|recover|restore|roll ?back)\b", re.I),
    ),
    (
        "troubleshooting",
        re.compile(r"\b(why|error|fail(ed|ing|ure)?|500|timeout|latency|outage|incident|crash)\b", re.I),
    ),
    ("definition", re.compile(r"^\s*(what is|what are|define|explain)\b", re.I)),
    ("ownership", re.compile(r"\b(who owns|owner|on-?call|contact|escalat)", re.I)),
]


@dataclass(frozen=True)
class QueryAnalysis:
    original: str
    query_class: str
    keywords: list[str]

    @property
    def lexical_query(self) -> str:
        # websearch_to_tsquery syntax: OR the keywords so ranking (not strict AND) decides relevance.
        return " or ".join(self.keywords)


def analyse(query: str) -> QueryAnalysis:
    query_class = next((name for name, pat in _PATTERNS if pat.search(query)), "factual")
    seen: list[str] = []
    for tok in _WORD.findall(query.lower()):
        if tok not in _STOP and len(tok) > 1 and tok not in seen:
            seen.append(tok)
    return QueryAnalysis(original=query, query_class=query_class, keywords=seen[:24])
