"""Client for the demo environment's control plane. Each caller is given only the token it needs."""

from __future__ import annotations

from typing import Any

import httpx

from opspilot.errors import ConflictError, NotFoundError, UpstreamUnavailableError, ValidationFailedError


class ControlPlaneClient:
    def __init__(
        self, base_url: str, token: str, *, timeout: float = 5.0, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url, timeout=timeout, headers={"Authorization": f"Bearer {token}"}, transport=transport
        )

    async def _request(self, method: str, path: str, **kw: Any) -> Any:
        try:
            r = await self._http.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise UpstreamUnavailableError(f"Control plane unreachable: {exc.__class__.__name__}") from exc
        if r.status_code == 404:
            raise NotFoundError(_detail(r, "Not found"))
        if r.status_code == 409:
            detail = r.json().get("detail", {})
            raise ConflictError(detail.get("message", "Conflict"), details=detail if isinstance(detail, dict) else {})
        if r.status_code == 422:
            raise ValidationFailedError(_detail(r, "Rejected by control plane"))
        if r.status_code >= 400:
            raise UpstreamUnavailableError(f"Control plane error {r.status_code}")
        return r.json()

    async def services(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/services")  # type: ignore[no-any-return]

    async def status(self, service: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/services/{service}/status")  # type: ignore[no-any-return]

    async def deployments(self, service: str, limit: int = 20) -> list[dict[str, Any]]:
        return await self._request("GET", f"/api/services/{service}/deployments", params={"limit": limit})  # type: ignore[no-any-return]

    async def releases(self, service: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/api/services/{service}/releases")  # type: ignore[no-any-return]

    async def deploy(
        self, service: str, *, version: str, expected_state_version: int, idempotency_key: str, actor: str, reason: str
    ) -> dict[str, Any]:
        body = {
            "version": version,
            "expected_state_version": expected_state_version,
            "idempotency_key": idempotency_key,
            "actor": actor,
            "reason": reason,
        }
        return await self._request("POST", f"/api/services/{service}/deploy", json=body)  # type: ignore[no-any-return]

    async def scenarios(self) -> list[dict[str, str]]:
        return await self._request("GET", "/api/scenarios")  # type: ignore[no-any-return]

    async def trigger(self, scenario: str) -> dict[str, Any]:
        return await self._request("POST", f"/api/scenarios/{scenario}/trigger")  # type: ignore[no-any-return]

    async def reset(self) -> dict[str, Any]:
        return await self._request("POST", "/api/scenarios/reset")  # type: ignore[no-any-return]

    async def aclose(self) -> None:
        await self._http.aclose()


def _detail(r: httpx.Response, default: str) -> str:
    try:
        d = r.json().get("detail")
        return d if isinstance(d, str) else default
    except ValueError:
        return default
