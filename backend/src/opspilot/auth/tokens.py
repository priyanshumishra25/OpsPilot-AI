"""Signed access tokens. Tokens carry identity only; roles and scopes are loaded server-side per request."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from opspilot.errors import AuthenticationError

_ALGORITHM = "HS256"


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    token_id: str
    expires_at: datetime


class TokenService:
    def __init__(self, secret: str, *, issuer: str, audience: str, ttl_seconds: int) -> None:
        self._secret = secret
        self._issuer = issuer
        self._audience = audience
        self._ttl = timedelta(seconds=ttl_seconds)

    def issue(self, user_id: uuid.UUID, *, now: datetime | None = None) -> tuple[str, datetime]:
        now = now or datetime.now(UTC)
        expires = now + self._ttl
        token = jwt.encode(
            {
                "sub": str(user_id),
                "iss": self._issuer,
                "aud": self._audience,
                "iat": int(now.timestamp()),
                "nbf": int(now.timestamp()),
                "exp": int(expires.timestamp()),
                "jti": uuid.uuid4().hex,
            },
            self._secret,
            algorithm=_ALGORITHM,
        )
        return token, expires

    def verify(self, token: str) -> TokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[_ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub", "jti", "aud", "iss"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Access token expired") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("Invalid access token") from exc
        try:
            user_id = uuid.UUID(payload["sub"])
        except ValueError as exc:
            raise AuthenticationError("Invalid token subject") from exc
        return TokenClaims(
            user_id=user_id,
            token_id=payload["jti"],
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )
