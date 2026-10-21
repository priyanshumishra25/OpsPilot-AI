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
    WORKSPACE_ADMIN = "workspace:admin"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({Permission.DOCUMENTS_READ, Permission.QUERY}),
    Role.ENGINEER: frozenset({Permission.DOCUMENTS_READ, Permission.QUERY, Permission.DOCUMENTS_WRITE}),
    Role.OPERATOR: frozenset(
        {Permission.DOCUMENTS_READ, Permission.QUERY, Permission.DOCUMENTS_WRITE, Permission.AUDIT_READ}
    ),
    Role.ADMIN: frozenset(Permission),
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

    def require(self, permission: Permission) -> None:
        if permission not in self.permissions:
            raise PermissionDeniedError(f"Role '{self.role.value}' lacks permission '{permission.value}'")

    def can_read_scope(self, scope: str) -> bool:
        return scope in self.access_scopes

    def require_scope(self, scope: str) -> None:
        if not self.can_read_scope(scope):
            raise PermissionDeniedError(f"No access to scope '{scope}'")
