from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ModelPricing:
    """USD per million tokens. Values come from explicit configuration, never hard-coded guesses."""

    model: str
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float
    cache_write_per_mtok: float


@dataclass(frozen=True)
class UsageRecord:
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    estimated_cost_usd: float
    fallback_used: bool
    served_by: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def usage_from_message(message: Any, pricing: ModelPricing) -> UsageRecord:
    """Build a usage record from the provider-reported usage on a final Message."""
    u = message.usage
    inp = int(u.input_tokens or 0)
    out = int(u.output_tokens or 0)
    cr = int(getattr(u, "cache_read_input_tokens", 0) or 0)
    cw = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
    iterations = getattr(u, "iterations", None) or []
    fallback = any(getattr(it, "type", None) == "fallback_message" for it in iterations)
    cost = (
        inp * pricing.input_per_mtok
        + out * pricing.output_per_mtok
        + cr * pricing.cache_read_per_mtok
        + cw * pricing.cache_write_per_mtok
    ) / 1_000_000
    return UsageRecord(
        model=pricing.model,
        input_tokens=inp,
        output_tokens=out,
        cache_read_input_tokens=cr,
        cache_creation_input_tokens=cw,
        # If a fallback model served the request it bills at its own rates; flagged so the UI labels it.
        estimated_cost_usd=round(cost, 6),
        fallback_used=fallback,
        served_by=str(getattr(message, "model", pricing.model)),
    )
