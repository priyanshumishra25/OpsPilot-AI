from __future__ import annotations

import json
from typing import Any


def parse_tool_error(text: str) -> dict[str, Any]:
    """Extract our structured `{"error": {...}}` body from an MCP tool error message.

    The SDK prefixes anticipated errors with "Error executing tool <name>: ". Argument validation failures
    carry pydantic text instead of JSON; those are reported as `invalid_argument`.
    """
    start = text.find("{")
    if start >= 0:
        try:
            body = json.loads(text[start:])
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                return dict(body["error"])
        except ValueError:
            pass
    code = "invalid_argument" if "validation error" in text else "tool_error"
    return {"code": code, "message": text[:2000], "details": {}}
