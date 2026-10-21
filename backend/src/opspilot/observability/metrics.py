from __future__ import annotations

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter("opspilot_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("opspilot_http_request_seconds", "HTTP request latency", ["method", "route"])
INGESTION_JOBS = Counter("opspilot_ingestion_jobs_total", "Ingestion job outcomes", ["status"])
INGESTION_STAGE_SECONDS = Histogram("opspilot_ingestion_stage_seconds", "Ingestion stage duration", ["stage"])
RETRIEVAL_SECONDS = Histogram("opspilot_retrieval_seconds", "Retrieval latency", ["strategy"])
LLM_TOKENS = Counter("opspilot_llm_tokens_total", "Provider-reported LLM tokens", ["model", "kind"])
LLM_COST_USD = Counter("opspilot_llm_estimated_cost_usd_total", "Estimated LLM cost from configured pricing", ["model"])
