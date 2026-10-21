"""TEST DOUBLES ONLY. These are never wired into the running application.

They replace infrastructure whose behaviour is not under test (object storage, queue transport) and
provide a deterministic embedding function for tests that do not measure semantic quality. Retrieval
quality is measured separately by `evals/` using the real embedding model.
"""

from __future__ import annotations

import hashlib
import re

import numpy as np
from langchain_core.embeddings import Embeddings

from opspilot.errors import NotFoundError, UpstreamUnavailableError
from opspilot.queue.base import QueueMessage


class InMemoryObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_gets = 0  # number of upcoming get() calls that raise a transient error

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = data

    async def get(self, key: str) -> bytes:
        if self.fail_gets > 0:
            self.fail_gets -= 1
            raise UpstreamUnavailableError("simulated object store outage")
        if key not in self.objects:
            raise NotFoundError(key)
        return self.objects[key]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def ping(self) -> None:
        return None


class InMemoryQueue:
    def __init__(self) -> None:
        self.published: list[str] = []
        self.dead_letters: list[tuple[str, str]] = []
        self.acked: list[str] = []

    async def publish(self, job_id: str) -> None:
        self.published.append(job_id)

    async def receive(self, *, max_messages: int, wait_seconds: float) -> list[QueueMessage]:
        out = [QueueMessage(job_id=j, receipt=j) for j in self.published[:max_messages]]
        del self.published[:max_messages]
        return out

    async def ack(self, message: QueueMessage) -> None:
        self.acked.append(message.receipt)

    async def publish_dead_letter(self, job_id: str, reason: str) -> None:
        self.dead_letters.append((job_id, reason))

    async def ping(self) -> None:
        return None

    async def close(self) -> None:
        return None


class HashingEmbeddings(Embeddings):
    """Deterministic feature-hashing bag-of-words vectors (L2-normalised, 384-d)."""

    dim = 384

    def __init__(self) -> None:
        self.calls = 0
        self.texts_embedded = 0

    def _vec(self, text: str) -> list[float]:
        v = np.zeros(self.dim, dtype=np.float32)
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 9) & 1 else -1.0
        n = float(np.linalg.norm(v))
        if n == 0:
            v[0] = 1.0
            n = 1.0
        return (v / n).tolist()  # type: ignore[no-any-return]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        self.texts_embedded += len(texts)
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


class FailingEmbeddings(HashingEmbeddings):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.failures > 0:
            self.failures -= 1
            raise UpstreamUnavailableError("simulated embedding outage")
        return super().embed_documents(texts)


def make_pdf(pages: list[str]) -> bytes:
    """Build a minimal valid PDF with one Helvetica text line per page (for extraction tests)."""
    objs: list[bytes] = []
    kids = []
    n_pages = len(pages)
    font_id = 3 + 2 * n_pages
    for i, text in enumerate(pages):
        page_id, content_id = 3 + 2 * i, 4 + 2 * i
        kids.append(f"{page_id} 0 R")
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode()
        objs.append(
            f"{page_id} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_id} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >> endobj\n".encode()
        )
        objs.append(
            f"{content_id} 0 obj << /Length {len(stream)} >> stream\n".encode() + stream + b"\nendstream endobj\n"
        )
    header = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        f"2 0 obj << /Type /Pages /Kids [{' '.join(kids)}] /Count {n_pages} >> endobj\n".encode(),
    ]
    font = f"{font_id} 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n".encode()
    body = b"%PDF-1.4\n"
    offsets = []
    for o in [*header, *objs, font]:
        offsets.append(len(body))
        body += o
    xref = len(body)
    body += f"xref\n0 {len(offsets) + 1}\n0000000000 65535 f \n".encode()
    body += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    body += f"trailer << /Size {len(offsets) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return body


class FakeAnthropicStream:
    """TEST DOUBLE for `AsyncAnthropic().beta.messages.stream(...)`. Replays scripted events whose
    shapes mirror the SDK's TextEvent / CitationEvent and final Message. Not used outside tests."""

    def __init__(self, events: list[object], final: object) -> None:
        self._events, self._final = events, final

    async def __aenter__(self) -> FakeAnthropicStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def __aiter__(self):  # type: ignore[no-untyped-def]
        async def gen():  # type: ignore[no-untyped-def]
            for e in self._events:
                yield e

        return gen()

    async def get_final_message(self) -> object:
        return self._final


class FakeAnthropicClient:
    def __init__(self, script) -> None:  # type: ignore[no-untyped-def]
        """`script(request_kwargs) -> (events, final_message)`; requests are recorded for assertions."""
        from types import SimpleNamespace

        self.requests: list[dict[str, object]] = []
        outer = self

        class _Messages:
            def stream(self, **kwargs: object) -> FakeAnthropicStream:
                outer.requests.append(kwargs)
                return FakeAnthropicStream(*script(kwargs))

        self.beta = SimpleNamespace(messages=_Messages())
