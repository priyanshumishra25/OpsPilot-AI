from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    # Exact-match lookup only; deliberately not EmailStr, which rejects internal domains such as .local.
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime


class WorkspaceOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    role: str
    access_scopes: list[str]


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    active_workspace_id: uuid.UUID
    role: str
    permissions: list[str]
    access_scopes: list[str]
    workspaces: list[WorkspaceOut]


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    status: str
    stage: str
    attempts: int
    max_attempts: int
    error: str | None
    error_class: str | None
    timings_ms: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version_number: int
    content_hash: str
    mime_type: str
    original_filename: str
    size_bytes: int
    status: str
    chunk_count: int
    error: str | None
    created_at: datetime
    indexed_at: datetime | None


class DocumentOut(BaseModel):
    id: uuid.UUID
    title: str
    source_type: str
    source_uri: str
    access_scope: str
    status: str
    is_demo_seed: bool
    current_version: VersionOut | None
    latest_job: JobOut | None
    created_at: datetime
    updated_at: datetime


class DocumentDetailOut(DocumentOut):
    versions: list[VersionOut]
    jobs: list[JobOut]


class UploadOut(BaseModel):
    document_id: uuid.UUID
    version_id: uuid.UUID
    job_id: uuid.UUID | None
    deduplicated: bool


class ChunkOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    version_id: uuid.UUID
    ordinal: int
    section_path: list[str]
    content: str
    page_number: int | None
    char_start: int
    char_end: int
    source_uri: str


class FiltersIn(BaseModel):
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    source_types: list[Literal["upload", "github"]] = Field(default_factory=list)
    access_scopes: list[str] = Field(default_factory=list, max_length=20)


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    strategy: Literal["dense", "lexical", "hybrid"] = "hybrid"
    rerank: bool = True
    k: int = Field(default=8, ge=1, le=30)
    filters: FiltersIn = Field(default_factory=FiltersIn)


class QueryRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    filters: FiltersIn = Field(default_factory=FiltersIn)


class RagQueryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    query: str
    status: str
    answer: str | None
    abstained: bool
    citations: list[dict[str, Any]]
    diagnostics: dict[str, Any]
    usage: dict[str, Any]
    trace_id: str | None
    latency_ms: int | None
    error: str | None
    created_at: datetime


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    actor_user_id: uuid.UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    outcome: str
    details: dict[str, Any]
    trace_id: str | None
    created_at: datetime
