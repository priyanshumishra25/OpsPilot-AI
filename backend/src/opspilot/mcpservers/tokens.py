"""Short-lived, per-server tool tokens.

The API (MCP client) mints an EdDSA-signed JWT for each MCP session, carrying the *authenticated* caller's
identity, workspace, role, readable document scopes and tool scopes. The audience is the target server, so a
token minted for the knowledge server is useless against the operations server. MCP servers hold only the
public key. Nothing a model emits can change these claims: tools read identity from the verified token only.
"""

from __future__ import annotations

import base64
import time
import uuid
from dataclasses import dataclass

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from mcp.server.auth.provider import AccessToken

from opspilot.auth.principal import Principal

ISSUER = "opspilot-api"
SERVER_AUDIENCES = {
    "knowledge": "opspilot-mcp-knowledge",
    "github": "opspilot-mcp-github",
    "observability": "opspilot-mcp-observability",
    "operations": "opspilot-mcp-operations",
}


def load_private_key(b64: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(b64))


def load_public_key(b64: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(b64))


class ToolTokenIssuer:
    def __init__(self, private_key_b64: str, *, ttl_seconds: int = 120) -> None:
        self._key = load_private_key(private_key_b64)
        self._ttl = ttl_seconds

    def issue(self, principal: Principal, server: str, *, session_id: str | None = None) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": SERVER_AUDIENCES[server],
            "sub": str(principal.user_id),
            "ws": str(principal.workspace_id),
            "role": principal.role.value,
            "doc_scopes": sorted(principal.access_scopes),
            "scope": " ".join(sorted(s.value for s in principal.tool_scopes)),
            "sid": session_id,
            "iat": now,
            "nbf": now,
            "exp": now + self._ttl,
            "jti": uuid.uuid4().hex,
        }
        return jwt.encode(claims, self._key, algorithm="EdDSA")


@dataclass(frozen=True)
class CallerContext:
    """Trusted identity of the user on whose behalf a tool runs (from the verified token only)."""

    user_id: uuid.UUID
    workspace_id: uuid.UUID
    role: str
    doc_scopes: frozenset[str]
    tool_scopes: frozenset[str]
    session_id: str | None
    token_id: str


class ToolTokenVerifier:
    """`mcp.server.auth.provider.TokenVerifier` implementation for one MCP server."""

    def __init__(self, public_key_b64: str, server: str) -> None:
        self._key = load_public_key(public_key_b64)
        self._audience = SERVER_AUDIENCES[server]

    def decode(self, token: str) -> CallerContext | None:
        try:
            c = jwt.decode(
                token,
                self._key,
                algorithms=["EdDSA"],
                audience=self._audience,
                issuer=ISSUER,
                options={"require": ["exp", "iat", "sub", "aud", "iss", "jti", "ws"]},
            )
            return CallerContext(
                user_id=uuid.UUID(c["sub"]),
                workspace_id=uuid.UUID(c["ws"]),
                role=str(c.get("role", "")),
                doc_scopes=frozenset(c.get("doc_scopes", [])),
                tool_scopes=frozenset(str(c.get("scope", "")).split()),
                session_id=c.get("sid"),
                token_id=c["jti"],
            )
        except (jwt.InvalidTokenError, ValueError, KeyError):
            return None

    async def verify_token(self, token: str) -> AccessToken | None:
        caller = self.decode(token)
        if caller is None:
            return None
        claims = jwt.decode(token, options={"verify_signature": False})  # already verified above
        return AccessToken(
            token=token,
            client_id=ISSUER,
            scopes=sorted(caller.tool_scopes),
            expires_at=claims["exp"],
            resource=self._audience,
            subject=str(caller.user_id),
            claims=claims,
        )
