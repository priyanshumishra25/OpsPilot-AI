"""Structured output of an agent turn (delivered through the Agent SDK's StructuredOutput tool)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from opspilot.incidents.schema import IncidentReport


class TurnOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(
        max_length=12000,
        description="Markdown answer for the user. Refer to evidence by its id (E1, E2, ...) and say plainly what "
        "is observed, what is hypothesised and what is concluded.",
    )
    report: IncidentReport | None = Field(
        default=None, description="Structured incident report when the user asked for an investigation; else null."
    )


def inline_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema with $defs inlined (tool input schemas are safest without $ref)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]])
            return {k: resolve(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)  # type: ignore[no-any-return]
