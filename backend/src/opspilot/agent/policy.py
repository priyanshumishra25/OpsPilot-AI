"""Which MCP tools the agent may use, per tool scope. Tools not listed are removed from the model's context."""

from __future__ import annotations

from opspilot.auth.principal import Principal

# server -> tool -> required tool scope
TOOL_POLICY: dict[str, dict[str, str]] = {
    "knowledge": {
        "search_knowledge": "knowledge:read",
        "get_document": "knowledge:read",
        "get_document_section": "knowledge:read",
        "list_available_sources": "knowledge:read",
    },
    "github": {
        "list_repositories": "github:read",
        "search_repository_code": "github:read",
        "get_repository_file": "github:read",
        "get_recent_commits": "github:read",
        "get_pull_request": "github:read",
        "get_commit_diff": "github:read",
    },
    "observability": {
        "query_service_metrics": "observability:read",
        "search_service_logs": "observability:read",
        "get_service_health": "observability:read",
        "get_error_rate": "observability:read",
        "get_deployment_timeline": "observability:read",
    },
    "operations": {
        "get_deployment_status": "operations:read",
        "get_available_rollback_versions": "operations:read",
        "request_deployment_rollback": "operations:request",
        "execute_approved_rollback": "operations:execute",
        # Incident reports are persisted by the runner from validated structured output, not by the model.
        "create_incident_report": "__never__",
    },
}

SERVER_READ_SCOPE = {
    "knowledge": "knowledge:read",
    "github": "github:read",
    "observability": "observability:read",
    "operations": "operations:read",
}


def mcp_name(server: str, tool: str) -> str:
    return f"mcp__{server}__{tool}"


def split_tool_name(name: str) -> tuple[str, str] | None:
    parts = name.split("__", 2)
    if len(parts) == 3 and parts[0] == "mcp":
        return parts[1], parts[2]
    return None


def tool_access(principal: Principal) -> tuple[list[str], list[str], list[str]]:
    """Returns (servers to connect, allowed tool names, disallowed tool names) for this principal."""
    scopes = {s.value for s in principal.tool_scopes}
    servers = [srv for srv, scope in SERVER_READ_SCOPE.items() if scope in scopes]
    allowed: list[str] = []
    disallowed: list[str] = []
    for srv, tools in TOOL_POLICY.items():
        for tool, scope in tools.items():
            (allowed if srv in servers and scope in scopes else disallowed).append(mcp_name(srv, tool))
    return servers, allowed, disallowed
