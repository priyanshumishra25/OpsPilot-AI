from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import select

from opspilot.api.deps import ContainerDep, PrincipalDep, SessionDep, enforce_rate_limit
from opspilot.api.schemas import LoginRequest, MeOut, TokenResponse, WorkspaceOut
from opspilot.audit import record_audit
from opspilot.auth.service import authenticate, list_workspaces
from opspilot.db.models import User
from opspilot.errors import AuthenticationError

router = APIRouter(prefix="/api/v1", tags=["auth"])


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, container: ContainerDep, session: SessionDep) -> TokenResponse:
    client_ip = request.client.host if request.client else "unknown"
    await enforce_rate_limit(container, "login", client_ip, container.settings.rate_limit_login_per_minute)
    await enforce_rate_limit(
        container, "login-user", body.email.lower(), container.settings.rate_limit_login_per_minute
    )
    try:
        user = await authenticate(session, body.email, body.password)
    except AuthenticationError:
        record_audit(
            session,
            action="auth.login",
            resource_type="user",
            resource_id=None,
            workspace_id=None,
            actor_user_id=None,
            outcome="denied",
            details={"email": body.email.lower(), "ip": client_ip},
        )
        await session.commit()
        raise
    token, expires = container.tokens.issue(user.id)
    record_audit(
        session,
        action="auth.login",
        resource_type="user",
        resource_id=str(user.id),
        workspace_id=None,
        actor_user_id=user.id,
        details={"ip": client_ip},
    )
    await session.commit()
    return TokenResponse(access_token=token, expires_at=expires)


@router.get("/me", response_model=MeOut)
async def me(principal: PrincipalDep, session: SessionDep) -> MeOut:
    user = await session.scalar(select(User).where(User.id == principal.user_id))
    assert user is not None
    workspaces = await list_workspaces(session, principal.user_id)
    return MeOut(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        active_workspace_id=principal.workspace_id,
        role=principal.role.value,
        permissions=sorted(p.value for p in principal.permissions),
        access_scopes=sorted(principal.access_scopes),
        workspaces=[
            WorkspaceOut(id=w.id, slug=w.slug, name=w.name, role=m.role.value, access_scopes=sorted(m.access_scopes))
            for w, m in workspaces
        ],
    )
