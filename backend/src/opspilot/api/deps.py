from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from opspilot.auth.principal import Principal
from opspilot.auth.service import load_principal
from opspilot.container import Container
from opspilot.errors import AuthenticationError, RateLimitedError, ValidationFailedError

_bearer = HTTPBearer(auto_error=False)


def get_container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


ContainerDep = Annotated[Container, Depends(get_container)]


async def get_session(container: ContainerDep) -> AsyncIterator[AsyncSession]:
    async with container.db.session() as s:
        yield s


SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_principal(
    container: ContainerDep,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    x_workspace_id: Annotated[str | None, Header()] = None,
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Missing bearer token")
    claims = container.tokens.verify(credentials.credentials)
    workspace_id = None
    if x_workspace_id:
        try:
            workspace_id = uuid.UUID(x_workspace_id)
        except ValueError as exc:
            raise ValidationFailedError("Invalid X-Workspace-Id header") from exc
    return await load_principal(session, claims.user_id, workspace_id)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


async def enforce_rate_limit(container: Container, bucket: str, identity: str, limit: int) -> None:
    """Fixed-window limiter in Redis. Fails closed: a Redis error surfaces as a 5xx rather than bypassing the limit."""
    import time

    window = int(time.time() // 60)
    key = f"ratelimit:{bucket}:{identity}:{window}"
    pipe = container.redis.pipeline()
    pipe.incr(key)
    pipe.expire(key, 70)
    count, _ = await pipe.execute()
    if int(count) > limit:
        raise RateLimitedError(
            "Too many requests; try again shortly", details={"retry_after_seconds": 60 - int(time.time()) % 60}
        )
