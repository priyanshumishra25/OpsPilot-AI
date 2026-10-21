"""Amazon SQS queue adapter used in AWS deployments."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import boto3

from opspilot.queue.base import QueueMessage


class SqsQueue:
    def __init__(self, queue_url: str, dlq_url: str | None, *, region: str, client: Any | None = None) -> None:
        self._url = queue_url
        self._dlq_url = dlq_url
        self._client: Any = client or boto3.client("sqs", region_name=region)

    async def publish(self, job_id: str) -> None:
        await asyncio.to_thread(
            self._client.send_message, QueueUrl=self._url, MessageBody=json.dumps({"job_id": job_id})
        )

    async def receive(self, *, max_messages: int, wait_seconds: float) -> list[QueueMessage]:
        resp = await asyncio.to_thread(
            self._client.receive_message,
            QueueUrl=self._url,
            MaxNumberOfMessages=min(max_messages, 10),
            WaitTimeSeconds=min(int(wait_seconds), 20),
        )
        out = []
        for m in resp.get("Messages", []):
            try:
                job_id = str(json.loads(m["Body"])["job_id"])
            except (ValueError, KeyError, TypeError):
                await asyncio.to_thread(
                    self._client.delete_message, QueueUrl=self._url, ReceiptHandle=m["ReceiptHandle"]
                )
                continue
            out.append(QueueMessage(job_id=job_id, receipt=m["ReceiptHandle"]))
        return out

    async def ack(self, message: QueueMessage) -> None:
        await asyncio.to_thread(self._client.delete_message, QueueUrl=self._url, ReceiptHandle=message.receipt)

    async def publish_dead_letter(self, job_id: str, reason: str) -> None:
        if self._dlq_url:
            body = json.dumps({"job_id": job_id, "reason": reason[:2000]})
            await asyncio.to_thread(self._client.send_message, QueueUrl=self._dlq_url, MessageBody=body)

    async def ping(self) -> None:
        await asyncio.to_thread(self._client.get_queue_attributes, QueueUrl=self._url, AttributeNames=["QueueArn"])

    async def close(self) -> None:
        return None
