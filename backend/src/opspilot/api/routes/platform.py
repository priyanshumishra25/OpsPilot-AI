"""Evaluation history and a read-only settings overview (configuration, credential presence, access control)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from opspilot.api.deps import ContainerDep, PrincipalDep, SessionDep
from opspilot.auth.principal import ROLE_PERMISSIONS, ROLE_TOOL_SCOPES, Permission
from opspilot.db.models import EvaluationRun, Membership, User
from opspilot.errors import NotFoundError

router = APIRouter(prefix="/api/v1", tags=["platform"])


class EvaluationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    run_id: str
    git_sha: str
    dataset: dict[str, Any]
    settings: dict[str, Any]
    retrieval: dict[str, Any]
    abstention: dict[str, Any]
    permission_checks: dict[str, Any]
    generation: dict[str, Any]
    baseline_comparison: dict[str, Any]
    environment: dict[str, Any]
    created_at: datetime


@router.get("/evaluations", response_model=list[EvaluationOut])
async def list_evaluations(
    principal: PrincipalDep, session: SessionDep, limit: Annotated[int, Query(ge=1, le=100)] = 30
) -> list[EvaluationRun]:
    principal.require(Permission.QUERY)
    return list(await session.scalars(select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(limit)))


@router.get("/evaluations/{evaluation_id}", response_model=EvaluationOut)
async def get_evaluation(evaluation_id: uuid.UUID, principal: PrincipalDep, session: SessionDep) -> EvaluationRun:
    principal.require(Permission.QUERY)
    run = await session.get(EvaluationRun, evaluation_id)
    if run is None:
        raise NotFoundError("Evaluation not found")
    return run


@router.get("/settings")
async def settings_overview(principal: PrincipalDep, container: ContainerDep, session: SessionDep) -> dict[str, Any]:
    s = container.settings
    out: dict[str, Any] = {
        "models": {
            "answer_model": s.generation_model,
            "answer_effort": s.generation_effort,
            "agent_model": s.agent_model,
            "agent_effort": s.agent_effort,
            "embedding_model": s.embedding_model,
            "reranker": s.reranker_model if s.reranker == "cross-encoder" else None,
            "pricing_usd_per_mtok": {
                "input": s.model_input_price_per_mtok,
                "output": s.model_output_price_per_mtok,
                "cache_read": s.model_cache_read_price_per_mtok,
            },
        },
        "retrieval": {
            "candidate_k": s.retrieval_candidate_k,
            "final_k": s.retrieval_final_k,
            "rerank_top_n": s.rerank_top_n,
            "rrf_k": s.rrf_k,
            "context_token_budget": s.context_token_budget,
            "abstain_min_rerank_score": s.abstain_min_rerank_score,
            "chunk_size_chars": s.chunk_size_chars,
            "chunk_overlap_chars": s.chunk_overlap_chars,
        },
        "agent_limits": {
            "max_turns": s.agent_max_turns,
            "max_tool_calls": s.agent_max_tool_calls,
            "time_budget_seconds": s.agent_time_budget_seconds,
            "max_budget_usd": s.agent_max_budget_usd,
        },
        "approvals": {
            "ttl_seconds": s.approval_ttl_seconds,
            "require_distinct_approver": s.approval_require_distinct_approver,
        },
        # Presence only; values are never returned.
        "credentials": {
            "anthropic_api_key": s.anthropic_api_key is not None,
            "github_token": s.github_token is not None,
            "mcp_signing_key": s.mcp_signing_private_key is not None,
            "control_plane_admin_token": s.control_plane_admin_token is not None,
        },
        "you": {
            "role": principal.role.value,
            "permissions": sorted(p.value for p in principal.permissions),
            "tool_scopes": sorted(t.value for t in principal.tool_scopes),
            "access_scopes": sorted(principal.access_scopes),
        },
        "roles": {
            r.value: {
                "permissions": sorted(p.value for p in ROLE_PERMISSIONS[r]),
                "tool_scopes": sorted(t.value for t in ROLE_TOOL_SCOPES[r]),
            }
            for r in ROLE_PERMISSIONS
        },
    }
    if Permission.WORKSPACE_ADMIN in principal.permissions:
        rows = await session.execute(
            select(User.email, User.display_name, Membership.role, Membership.access_scopes)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.workspace_id == principal.workspace_id)
            .order_by(User.email)
        )
        out["members"] = [{"email": e, "name": n, "role": r.value, "access_scopes": sc} for e, n, r, sc in rows]
    return out
