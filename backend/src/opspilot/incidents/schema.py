"""Structured incident investigation report.

Confidence is qualitative (evidence strength plus a written rationale). There is no numeric confidence field: no
calibration method exists for one, so the schema does not allow it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")  # unknown fields (e.g. a numeric "confidence_score") are rejected


EvidenceKind = Literal["document", "metric", "log", "deployment", "commit", "pull_request", "health"]


class EvidenceReference(_Strict):
    id: str = Field(max_length=40, description="Short label used to refer to this evidence, e.g. E1")
    kind: EvidenceKind
    source: str = Field(max_length=500, description="Tool name and identifiers (chunk id, commit sha, query, ...)")
    observation: str = Field(max_length=1000, description="What the evidence shows, stated factually")


class TimelineEvent(_Strict):
    time: str = Field(max_length=64)
    event: str = Field(max_length=500)
    evidence: list[str] = Field(default_factory=list, max_length=10)


class CandidateCause(_Strict):
    cause: str = Field(max_length=500)
    assessment: Literal["likely", "possible", "unlikely", "ruled_out"]
    supporting_evidence: list[str] = Field(default_factory=list, max_length=20)
    contradicting_evidence: list[str] = Field(default_factory=list, max_length=20)


class RecommendedAction(_Strict):
    action: str = Field(max_length=500)
    rationale: str = Field(max_length=1000)
    risk: Literal["low", "medium", "high"]
    requires_approval: bool
    evidence: list[str] = Field(default_factory=list, max_length=10)


class Confidence(_Strict):
    evidence_strength: Literal["strong", "moderate", "weak"]
    explanation: str = Field(max_length=1500)


class IncidentReport(_Strict):
    incident_summary: str = Field(max_length=2000)
    observed_symptoms: list[str] = Field(max_length=20)
    relevant_services: list[str] = Field(max_length=10)
    evidence: list[EvidenceReference] = Field(max_length=50)
    timeline: list[TimelineEvent] = Field(max_length=50)
    candidate_causes: list[CandidateCause] = Field(max_length=10)
    most_likely_cause: str | None = Field(default=None, max_length=1000)
    confidence: Confidence
    missing_information: list[str] = Field(default_factory=list, max_length=20)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list, max_length=10)
    risk_assessment: str = Field(max_length=2000)
