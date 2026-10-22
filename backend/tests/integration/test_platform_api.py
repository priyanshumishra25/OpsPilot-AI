import httpx
import pytest

from opspilot.db.models import EvaluationRun, Role
from tests.conftest import Env, login, make_member

pytestmark = pytest.mark.integration


async def test_evaluations_and_settings(env: Env, client: httpx.AsyncClient) -> None:
    await make_member(env.container.db, email="admin@acme.test", workspace_slug="acme", role=Role.ADMIN)
    await make_member(env.container.db, email="eng@acme.test", workspace_slug="acme")
    async with env.container.db.session() as s:
        s.add(
            EvaluationRun(
                kind="retrieval",
                run_id="20261022T000000Z",
                git_sha="abc1234",
                dataset={"questions": 35},
                settings={},
                retrieval={"dense": {"recall@5": 0.96}},
                abstention={},
                permission_checks={"passed": True},
                generation={"status": "not requested"},
                baseline_comparison={"compared": True, "regressions": []},
            )
        )
        await s.commit()
    eng = await login(client, "eng@acme.test")
    runs = (await client.get("/api/v1/evaluations", headers=eng)).json()
    assert runs[0]["retrieval"]["dense"]["recall@5"] == 0.96
    s_eng = (await client.get("/api/v1/settings", headers=eng)).json()
    assert "members" not in s_eng and s_eng["you"]["role"] == "engineer"
    assert s_eng["credentials"]["anthropic_api_key"] is False
    body = (await client.get("/api/v1/settings", headers=eng)).text
    assert env.container.settings.jwt_secret.get_secret_value() not in body
    admin = await login(client, "admin@acme.test")
    s_admin = (await client.get("/api/v1/settings", headers=admin)).json()
    assert {m["email"] for m in s_admin["members"]} == {"admin@acme.test", "eng@acme.test"}
