import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from opspilot.auth.passwords import hash_password, verify_password
from opspilot.auth.principal import Permission, Principal
from opspilot.auth.tokens import TokenService
from opspilot.db.models import Role
from opspilot.errors import AuthenticationError, PermissionDeniedError
from opspilot.ingestion.worker import backoff_seconds
from opspilot.logging import redact
from opspilot.rag.fusion import reciprocal_rank_fusion
from opspilot.rag.generation import CitationResolver, build_search_results, chunk_source_id
from opspilot.rag.query import analyse
from opspilot.rag.types import RetrievedChunk

SECRET = "unit-test-secret-0123456789abcdefghij"


def test_rrf_rewards_agreement() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    scores = reciprocal_rank_fusion([[a, b], [b, c]], k=60)
    assert scores[b] == pytest.approx(1 / 62 + 1 / 61)
    assert max(scores, key=scores.__getitem__) == b


def test_query_analysis() -> None:
    q = analyse("What is the documented procedure for recovering the checkout service after a DB failure?")
    assert q.query_class == "procedure"
    assert "checkout" in q.keywords and "the" not in q.keywords
    assert q.lexical_query.count(" or ") == len(q.keywords) - 1
    assert analyse("Why is orders-api returning 500 errors?").query_class == "troubleshooting"
    assert analyse("Who owns checkout-api?").query_class == "ownership"
    # Injection-shaped input becomes plain keywords; websearch_to_tsquery never sees raw syntax.
    assert all(t.replace("-", "").replace("_", "").isalnum() for t in analyse("x'); DROP TABLE chunks;--").keywords)


def test_tokens_roundtrip_and_rejections() -> None:
    svc = TokenService(SECRET, issuer="opspilot", audience="opspilot-api", ttl_seconds=60)
    uid = uuid.uuid4()
    token, _ = svc.issue(uid)
    assert svc.verify(token).user_id == uid
    expired, _ = svc.issue(uid, now=datetime.now(UTC) - timedelta(hours=1))
    with pytest.raises(AuthenticationError, match="expired"):
        svc.verify(expired)
    other = TokenService("x" * 40, issuer="opspilot", audience="opspilot-api", ttl_seconds=60)
    with pytest.raises(AuthenticationError):
        svc.verify(other.issue(uid)[0])
    wrong_aud = TokenService(SECRET, issuer="opspilot", audience="other", ttl_seconds=60)
    with pytest.raises(AuthenticationError):
        svc.verify(wrong_aud.issue(uid)[0])
    with pytest.raises(AuthenticationError):
        svc.verify(token[:-4] + "AAAA")


def test_alg_none_token_rejected() -> None:
    import jwt

    svc = TokenService(SECRET, issuer="opspilot", audience="opspilot-api", ttl_seconds=60)
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "iss": "opspilot", "aud": "opspilot-api", "iat": 0, "exp": 9999999999, "jti": "x"},
        key=None,
        algorithm="none",
    )
    with pytest.raises(AuthenticationError):
        svc.verify(forged)


def test_password_hashing() -> None:
    h = hash_password("a-long-password-123")
    assert verify_password(h, "a-long-password-123")
    assert not verify_password(h, "wrong")
    assert not verify_password(None, "anything")


def test_role_permissions() -> None:
    viewer = Principal(uuid.uuid4(), "v@x", uuid.uuid4(), Role.VIEWER, frozenset({"general"}))
    viewer.require(Permission.QUERY)
    with pytest.raises(PermissionDeniedError):
        viewer.require(Permission.DOCUMENTS_WRITE)
    with pytest.raises(PermissionDeniedError):
        viewer.require_scope("sre-restricted")


def test_backoff_is_bounded_exponential_with_jitter() -> None:
    rng = random.Random(7)
    for attempt in range(1, 10):
        assert 0 <= backoff_seconds(attempt, 2.0, 30.0, rng) <= min(30.0, 2.0 * 2 ** (attempt - 1))


def test_redaction() -> None:
    out = redact({"password": "x", "nested": {"api_key": "y", "note": "key sk-ant-abcdefghijkl123 here"}, "ok": 1})
    assert out == {
        "password": "[REDACTED]",
        "nested": {"api_key": "[REDACTED]", "note": "key [REDACTED] here"},
        "ok": 1,
    }


def _chunk(content: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        document_title="Runbook",
        source_type="upload",
        source_uri="upload://r.md",
        section_path=["Recovery"],
        content=content,
        content_hash="h",
        page_number=None,
        char_start=0,
        char_end=len(content),
        token_estimate=10,
        access_scope="general",
    )


@dataclass
class FakeCitation:
    """Shape of anthropic CitationsSearchResultLocation (test double)."""

    search_result_index: int
    source: str
    cited_text: str
    type: str = "search_result_location"
    start_block_index: int = 0
    end_block_index: int = 1


def test_citation_resolver_accepts_only_verifiable_citations() -> None:
    chunks = [_chunk("Restart pods one at a time."), _chunk("Set degraded mode to true.")]
    r = CitationResolver(chunks)
    ok = r.resolve(FakeCitation(1, chunk_source_id(chunks[1]), "Set degraded mode to true."))
    assert ok is not None and ok.chunk_id == str(chunks[1].chunk_id) and ok.index == 1
    # Same chunk cited twice keeps one marker.
    assert r.resolve(FakeCitation(1, chunk_source_id(chunks[1]), "Set degraded mode")) is ok
    # Out-of-range index, mismatched source, fabricated text, and other citation types are rejected.
    assert r.resolve(FakeCitation(5, chunk_source_id(chunks[0]), "x")) is None
    assert r.resolve(FakeCitation(0, chunk_source_id(chunks[1]), "Restart pods one at a time.")) is None
    assert r.resolve(FakeCitation(0, chunk_source_id(chunks[0]), "Delete the database.")) is None
    assert r.resolve(FakeCitation(0, chunk_source_id(chunks[0]), "x", type="char_location")) is None
    assert r.rejected == 4
    assert [c.index for c in r.citations] == [1]


def test_search_result_blocks_shape() -> None:
    blocks = build_search_results([_chunk("text")])
    assert blocks[0]["type"] == "search_result"
    assert blocks[0]["citations"] == {"enabled": True}
    assert blocks[0]["title"] == "Runbook — Recovery"
    assert blocks[0]["source"].startswith("opspilot://documents/")


def test_empty_optional_secrets_mean_unset() -> None:
    from opspilot.config import Settings

    s = Settings(
        _env_file=None, anthropic_api_key="", github_token="unset", mcp_signing_private_key="", s3_endpoint_url=""
    )  # type: ignore[call-arg]
    assert s.anthropic_api_key is None and s.github_token is None and s.mcp_signing_private_key is None
    assert Settings(_env_file=None, anthropic_api_key="UNSET").anthropic_api_key is None  # type: ignore[call-arg]
    assert s.s3_endpoint_url is None


def test_password_hash_uses_explicit_owasp_parameters() -> None:
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-c", "from opspilot.auth.passwords import hash_password; print(hash_password('x'))"],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin", "PYTHONPATH": "src"},
    ).stdout
    assert out.startswith("$argon2id$v=19$m=19456,t=2,p=1$")


def test_database_and_redis_urls_from_components() -> None:
    from opspilot.config import Settings

    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_host="db.internal",
        database_user="app",
        database_password="p@ss/w:rd%",
        database_name="opspilot",
        redis_host="cache.internal",
        redis_password="tok/en",
        redis_tls=True,
    )
    assert s.database_url == "postgresql+asyncpg://app:p%40ss%2Fw%3Ard%25@db.internal:5432/opspilot"
    assert s.redis_url == "rediss://:tok%2Fen@cache.internal:6379/0"
