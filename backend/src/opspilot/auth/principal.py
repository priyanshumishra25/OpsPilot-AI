"""The authenticated caller and the permission model.

Permissions are derived from the membership row loaded server-side. Nothing in a request body,
model output, or tool argument can widen them.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass

from opspilot.db.models import Role
from opspilot.errors import PermissionDeniedError


class Permission(enum.StrEnum):
    DOCUMENTS_READ = "documents:read"
    DOCUMENTS_WRITE = "documents:write"
    DOCUMENTS_DELETE = "documents:delete"
    QUERY = "query"
    AUDIT_READ = "audit:read"
    APPROVALS_DECIDE = "approvals:decide"
    WORKSPACE_ADMIN = "workspace:admin"


class ToolScope(enum.StrEnum):
    """Scopes carried in MCP tool tokens. Each MCP server requires its own read scope."""

    KNOWLEDGE_READ = "knowledge:read"
    GITHUB_READ = "github:read"
    OBSERVABILITY_READ = "observability:read"
    OPERATIONS_READ = "operations:read"
    OPERATIONS_REQUEST = "operations:request"  # create incident reports / request (not perform) actions
    OPERATIONS_EXECUTE = "operations:execute"  # execute an action that a human has approved


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({Permission.DOCUMENTS_READ, Permission.QUERY}),
    Role.ENGINEER: frozenset({Permission.DOCUMENTS_READ, Permission.QUERY, Permission.DOCUMENTS_WRITE}),
    Role.OPERATOR: frozenset(
        {
            Permission.DOCUMENTS_READ,
            Permission.QUERY,
            Permission.DOCUMENTS_WRITE,
            Permission.AUDIT_READ,
            Permission.APPROVALS_DECIDE,
        }
    ),
    Role.ADMIN: frozenset(Permission),
}


_READ_TOOLS = frozenset(
    {ToolScope.KNOWLEDGE_READ, ToolScope.GITHUB_READ, ToolScope.OBSERVABILITY_READ, ToolScope.OPERATIONS_READ}
)
ROLE_TOOL_SCOPES: dict[Role, frozenset[ToolScope]] = {
    Role.VIEWER: frozenset({ToolScope.KNOWLEDGE_READ}),
    Role.ENGINEER: _READ_TOOLS | {ToolScope.OPERATIONS_REQUEST, ToolScope.OPERATIONS_EXECUTE},
    Role.OPERATOR: _READ_TOOLS | {ToolScope.OPERATIONS_REQUEST, ToolScope.OPERATIONS_EXECUTE},
    Role.ADMIN: frozenset(ToolScope),
}


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    email: str
    workspace_id: uuid.UUID
    role: Role
    access_scopes: frozenset[str]

    @property
    def permissions(self) -> frozenset[Permission]:
        return ROLE_PERMISSIONS[self.role]

    @property
    def tool_scopes(self) -> frozenset[ToolScope]:
        return ROLE_TOOL_SCOPES[self.role]

    def require(self, permission: Permission) -> None:
        if permission not in self.permissions:
            raise PermissionDeniedError(f"Role '{self.role.value}' lacks permission '{permission.value}'")

    def can_read_scope(self, scope: str) -> bool:
        return scope in self.access_scopes

    def require_scope(self, scope: str) -> None:
        if not self.can_read_scope(scope):
            raise PermissionDeniedError(f"No access to scope '{scope}'")
