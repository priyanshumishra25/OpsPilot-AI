"""S3-compatible artifact storage (AWS S3 in the cloud, MinIO locally)."""

from __future__ import annotations

import asyncio
from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from opspilot.config import Settings
from opspilot.errors import NotFoundError, UpstreamUnavailableError


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def ping(self) -> None: ...


class S3ObjectStore:
    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._sse = settings.s3_server_side_encryption
        kwargs: dict[str, object] = {
            "region_name": settings.s3_region,
            "config": Config(retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=5, read_timeout=30),
        }
        if settings.s3_endpoint_url:
            kwargs["endpoint_url"] = settings.s3_endpoint_url
        if settings.s3_access_key_id and settings.s3_secret_access_key:
            kwargs["aws_access_key_id"] = settings.s3_access_key_id.get_secret_value()
            kwargs["aws_secret_access_key"] = settings.s3_secret_access_key.get_secret_value()
        self._client = boto3.client("s3", **kwargs)  # type: ignore[call-overload]

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        extra = {"ServerSideEncryption": self._sse} if self._sse else {}
        await self._call(
            self._client.put_object, Bucket=self._bucket, Key=key, Body=data, ContentType=content_type, **extra
        )

    async def get(self, key: str) -> bytes:
        try:
            resp = await asyncio.to_thread(self._client.get_object, Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                raise NotFoundError(f"Object {key} not found") from exc
            raise UpstreamUnavailableError(f"Object store error: {exc}") from exc
        except BotoCoreError as exc:
            raise UpstreamUnavailableError(f"Object store error: {exc}") from exc
        body: bytes = await asyncio.to_thread(resp["Body"].read)
        return body

    async def delete(self, key: str) -> None:
        await self._call(self._client.delete_object, Bucket=self._bucket, Key=key)

    async def ping(self) -> None:
        await self._call(self._client.head_bucket, Bucket=self._bucket)

    async def ensure_bucket(self) -> None:
        """Local/dev convenience; in AWS the bucket is created by Terraform."""
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)
        except ClientError:
            await self._call(self._client.create_bucket, Bucket=self._bucket)

    @staticmethod
    async def _call(fn: object, **kwargs: object) -> object:
        try:
            return await asyncio.to_thread(fn, **kwargs)  # type: ignore[arg-type]
        except (ClientError, BotoCoreError) as exc:
            raise UpstreamUnavailableError(f"Object store error: {exc}") from exc
