from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from opspilot.auth.passwords import ahash_password, averify_password, needs_rehash
from opspilot.auth.principal import Principal
from opspilot.db.models import Membership, User, Workspace
from opspilot.errors import AuthenticationError, PermissionDeniedError


async def authenticate(session: AsyncSession, email: str, password: str) -> User:
    user = await session.scalar(select(User).where(User.email == email.lower().strip()))
    ok = await averify_password(user.password_hash if user else None, password)
    if not ok or user is None or not user.is_active:
        raise AuthenticationError("Invalid email or password")
    if needs_rehash(user.password_hash):
        # Upgrade hashes created with older cost parameters; the caller's transaction commits it with the login audit.
        user.password_hash = await ahash_password(password)
    return user


async def load_principal(session: AsyncSession, user_id: uuid.UUID, workspace_id: uuid.UUID | None) -> Principal:
    """Resolve the caller's membership. If no workspace is given, use their only/first workspace."""
    stmt = (
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(User.id == user_id, User.is_active.is_(True))
        .order_by(Membership.created_at)
    )
    if workspace_id is not None:
        stmt = stmt.where(Membership.workspace_id == workspace_id)
    row = (await session.execute(stmt.limit(1))).first()
    if row is None:
        if workspace_id is not None:
            raise PermissionDeniedError("Not a member of this workspace")
        raise AuthenticationError("User has no workspace membership")
    user, membership = row
    return Principal(
        user_id=user.id,
        email=user.email,
        workspace_id=membership.workspace_id,
        role=membership.role,
        access_scopes=frozenset(membership.access_scopes),
    )


async def list_workspaces(session: AsyncSession, user_id: uuid.UUID) -> list[tuple[Workspace, Membership]]:
    rows = await session.execute(
        select(Workspace, Membership)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user_id)
        .order_by(Workspace.name)
    )
    return [(w, m) for w, m in rows.all()]
