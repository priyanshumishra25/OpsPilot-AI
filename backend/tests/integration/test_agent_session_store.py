import uuid

import pytest
from claude_agent_sdk.testing import run_session_store_conformance

from opspilot.agent.session_store import PostgresSessionStore
from opspilot.db.base import Database

pytestmark = pytest.mark.integration


async def test_postgres_session_store_passes_sdk_conformance(db: Database) -> None:
    await run_session_store_conformance(lambda: PostgresSessionStore(db, namespace=f"t-{uuid.uuid4().hex[:8]}/"))
