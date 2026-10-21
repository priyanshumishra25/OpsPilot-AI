"""Demo control plane: release catalogue, deployment history, deploys and failure scenarios.

This simulates the *deployment system* of an isolated demo environment. It is the only thing the OpsPilot
operations MCP server can mutate, and only through `POST /api/services/{service}/deploy` with the deploy
token. Deploys are compare-and-swap on `state_version` and idempotent on `idempotency_key`.
"""

from __future__ import annotations

import hmac
import json
import os
import sqlite3
import subprocess
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

DB_PATH = os.environ.get("CONTROL_PLANE_DB", "/data/control-plane.db")
REPOS_DIR = Path(os.environ.get("DEMO_REPOS_DIR", "/repos"))
READ_TOKEN = os.environ.get("CONTROL_PLANE_READ_TOKEN", "")
DEPLOY_TOKEN = os.environ.get("CONTROL_PLANE_DEPLOY_TOKEN", "")
ADMIN_TOKEN = os.environ.get("CONTROL_PLANE_ADMIN_TOKEN", "")
SERVICES = {"orders-api": "orders-api", "checkout-api": "checkout-api"}  # service -> repo name

# Seeded production history (deterministic). (service, version, deployed_at, actor, status, note)
SEED_HISTORY = [
    ("orders-api", "v2.13.2", "2026-02-20T11:00:00+00:00", "deploy-bot", "succeeded", None),
    ("orders-api", "v2.14.0", "2026-03-12T14:02:00+00:00", "deploy-bot", "rolled_back", "SEV-2: pool exhaustion"),
    ("orders-api", "v2.13.2", "2026-03-12T14:31:00+00:00", "oncall:maya.chen", "succeeded", "rollback from v2.14.0"),
    ("orders-api", "v2.14.1", "2026-03-13T10:00:00+00:00", "deploy-bot", "succeeded", None),
    ("orders-api", "v2.15.0", "2026-09-29T12:00:00+00:00", "deploy-bot", "succeeded", None),
    ("checkout-api", "v3.8.0", "2026-08-03T11:00:00+00:00", "deploy-bot", "succeeded", None),
    ("checkout-api", "v3.8.1", "2026-09-15T16:00:00+00:00", "deploy-bot", "succeeded", None),
]

# Version-level behaviour (what the code at that commit does under load).
VERSION_BEHAVIOUR: dict[tuple[str, str], dict[str, Any]] = {
    ("orders-api", "v2.14.0"): {"holds_connection_across_inventory": True},
    ("orders-api", "v2.16.0"): {"holds_connection_across_inventory": True},
    ("checkout-api", "v3.9.0"): {"extra_latency_ms": 1400},
}

SCENARIOS: dict[str, dict[str, Any]] = {
    "orders-bad-deploy": {
        "description": "orders-api v2.16.0 is deployed; it holds a DB transaction across the inventory call, "
        "exhausting the connection pool (HTTP 500 PoolTimeoutError).",
        "deploy": ("orders-api", "v2.16.0"),
    },
    "checkout-latency-regression": {
        "description": "checkout-api v3.9.0 adds a fraud-scoring call with no timeout; p95 latency regresses.",
        "deploy": ("checkout-api", "v3.9.0"),
    },
    "checkout-db-pool-exhaustion": {
        "description": "checkout-db connection pool is exhausted (HTTP 503 CHECKOUT_DB_UNAVAILABLE).",
        "fault": ("checkout-api", {"db_pool_exhausted": True}),
    },
    "inventory-timeout": {
        "description": "The inventory dependency times out; orders-api returns HTTP 504 for some requests.",
        "fault": ("orders-api", {"inventory_timeout_rate": 0.25}),
    },
    "failed-deployment": {
        "description": "A deploy of orders-api v2.16.0 fails its health checks and is aborted by the pipeline; "
        "the previous version keeps serving.",
        "failed_deploy": ("orders-api", "v2.16.0"),
    },
}

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, isolation_level=None)
    c.row_factory = sqlite3.Row
    return c


def _now() -> datetime:
    return datetime.now(UTC)


def _release_catalogue() -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {}
    for service, repo in SERVICES.items():
        path = REPOS_DIR / repo
        raw = subprocess.run(
            [
                "git",
                "for-each-ref",
                "refs/tags",
                "--format=%(refname:short)|%(objectname)|%(creatordate:iso-strict)|%(subject)",
            ],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        rows = []
        for line in raw.strip().splitlines():
            tag, sha, date, subject = line.split("|", 3)
            rows.append({"version": tag, "commit_sha": sha, "released_at": date, "subject": subject})
        out[service] = sorted(rows, key=lambda r: r["released_at"])
    return out


def init_db() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _lock, _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS releases (service TEXT, version TEXT, commit_sha TEXT, released_at TEXT,
                subject TEXT, PRIMARY KEY (service, version));
            CREATE TABLE IF NOT EXISTS deployments (id INTEGER PRIMARY KEY AUTOINCREMENT, service TEXT, version TEXT,
                previous_version TEXT, commit_sha TEXT, deployed_at TEXT, actor TEXT, status TEXT, note TEXT,
                idempotency_key TEXT UNIQUE, seeded INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS service_state (service TEXT PRIMARY KEY, current_version TEXT,
                state_version INTEGER, faults TEXT, scenario TEXT, updated_at TEXT);
            """
        )
        c.execute("DELETE FROM releases")
        for service, rows in _release_catalogue().items():
            for r in rows:
                c.execute(
                    "INSERT INTO releases VALUES (?,?,?,?,?)",
                    (service, r["version"], r["commit_sha"], r["released_at"], r["subject"]),
                )
        if c.execute("SELECT count(*) FROM deployments").fetchone()[0] == 0:
            _seed(c)


def _seed(c: sqlite3.Connection) -> None:
    c.execute("DELETE FROM deployments")
    c.execute("DELETE FROM service_state")
    previous: dict[str, str | None] = {}
    for service, version, at, actor, status, note in SEED_HISTORY:
        sha = c.execute("SELECT commit_sha FROM releases WHERE service=? AND version=?", (service, version)).fetchone()[
            0
        ]
        c.execute(
            "INSERT INTO deployments (service, version, previous_version, commit_sha, deployed_at, actor, status, "
            "note, seeded)"
            " VALUES (?,?,?,?,?,?,?,?,1)",
            (service, version, previous.get(service), sha, at, actor, status, note),
        )
        previous[service] = version
    for service, version in previous.items():
        c.execute(
            "INSERT INTO service_state VALUES (?,?,?,?,?,?)", (service, version, 1, "{}", None, _now().isoformat())
        )


def _state(c: sqlite3.Connection, service: str) -> sqlite3.Row:
    row = c.execute("SELECT * FROM service_state WHERE service=?", (service,)).fetchone()
    if row is None:
        raise HTTPException(404, f"Unknown service {service}")
    return row


def _deploy(
    c: sqlite3.Connection,
    service: str,
    version: str,
    actor: str,
    note: str | None,
    key: str | None,
    status: str = "succeeded",
) -> dict[str, Any]:
    st = _state(c, service)
    rel = c.execute("SELECT commit_sha FROM releases WHERE service=? AND version=?", (service, version)).fetchone()
    if rel is None:
        raise HTTPException(422, f"No release {version} for {service}")
    c.execute(
        "INSERT INTO deployments (service, version, previous_version, commit_sha, deployed_at, actor, status, note, "
        "idempotency_key)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (service, version, st["current_version"], rel["commit_sha"], _now().isoformat(), actor, status, note, key),
    )
    if status == "succeeded":
        cur = c.execute(
            "SELECT released_at FROM releases WHERE service=? AND version=?", (service, st["current_version"])
        ).fetchone()
        new = c.execute("SELECT released_at FROM releases WHERE service=? AND version=?", (service, version)).fetchone()
        if cur is not None and new["released_at"] < cur["released_at"]:
            # Deploying an older release is a rollback: mark the replaced deployment accordingly.
            c.execute(
                "UPDATE deployments SET status='rolled_back' WHERE id=(SELECT max(id) FROM deployments "
                "WHERE service=? AND version=? AND status='succeeded')",
                (service, st["current_version"]),
            )
        c.execute(
            "UPDATE service_state SET current_version=?, state_version=state_version+1, updated_at=? WHERE service=?",
            (version, _now().isoformat(), service),
        )
    dep = c.execute("SELECT * FROM deployments WHERE id=last_insert_rowid()").fetchone()
    return dict(dep)


# ---- auth ---------------------------------------------------------------------------------------


def _token_dep(*allowed: str):  # type: ignore[no-untyped-def]
    def check(authorization: Annotated[str | None, Header()] = None) -> str:
        token = (authorization or "").removeprefix("Bearer ").strip()
        for name, expected in zip(("read", "deploy", "admin"), (READ_TOKEN, DEPLOY_TOKEN, ADMIN_TOKEN), strict=True):
            if name in allowed and expected and hmac.compare_digest(token, expected):
                return name
        raise HTTPException(401, "invalid control-plane token")

    return check


ReadAuth = Depends(_token_dep("read", "deploy", "admin"))
DeployAuth = Depends(_token_dep("deploy"))
AdminAuth = Depends(_token_dep("admin"))


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    for name, tok in (("READ", READ_TOKEN), ("DEPLOY", DEPLOY_TOKEN), ("ADMIN", ADMIN_TOKEN)):
        if len(tok) < 16:
            raise RuntimeError(f"CONTROL_PLANE_{name}_TOKEN must be set (>= 16 chars)")
    init_db()
    yield


app = FastAPI(title="OpsPilot demo control plane (demo environment only)", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "environment": "demo"}


@app.get("/api/services", dependencies=[ReadAuth])
def list_services() -> list[dict[str, Any]]:
    with _conn() as c:
        return [_status(c, s) for s in SERVICES]


def _status(c: sqlite3.Connection, service: str) -> dict[str, Any]:
    st = _state(c, service)
    rel = c.execute("SELECT * FROM releases WHERE service=? AND version=?", (service, st["current_version"])).fetchone()
    last = c.execute(
        "SELECT * FROM deployments WHERE service=? AND status='succeeded' ORDER BY id DESC LIMIT 1", (service,)
    ).fetchone()
    return {
        "service": service,
        "environment": "demo",
        "current_version": st["current_version"],
        "commit_sha": rel["commit_sha"],
        "state_version": st["state_version"],
        "deployed_at": last["deployed_at"],
        "deployed_by": last["actor"],
        "active_faults": json.loads(st["faults"]),
        "scenario": st["scenario"],
    }


@app.get("/api/services/{service}/status", dependencies=[ReadAuth])
def service_status(service: str) -> dict[str, Any]:  # also used directly by tests
    with _conn() as c:
        return _status(c, service)


@app.get("/api/services/{service}/behavior")
def behaviour(service: str) -> dict[str, Any]:
    """Unauthenticated, read-only: consumed by the demo services themselves on the internal network."""
    with _conn() as c:
        st = _state(c, service)
        return {
            "version": st["current_version"],
            **VERSION_BEHAVIOUR.get((service, st["current_version"]), {}),
            **json.loads(st["faults"]),
        }


@app.get("/api/services/{service}/deployments", dependencies=[ReadAuth])
def deployments(service: str, limit: int = 20) -> list[dict[str, Any]]:
    with _conn() as c:
        _state(c, service)
        rows = c.execute(
            "SELECT * FROM deployments WHERE service=? ORDER BY id DESC LIMIT ?", (service, min(limit, 100))
        )
        return [{k: r[k] for k in r.keys() if k not in ("idempotency_key", "seeded")} for r in rows]


@app.get("/api/services/{service}/releases", dependencies=[ReadAuth])
def releases(service: str) -> list[dict[str, Any]]:
    """Releases with the total time each version served production (from deployment history)."""
    with _conn() as c:
        st = _state(c, service)
        deps = c.execute(
            "SELECT version, deployed_at, status FROM deployments "
            "WHERE service=? AND status IN ('succeeded','rolled_back') "
            "ORDER BY id",
            (service,),
        ).fetchall()
        served: dict[str, float] = {}
        rolled_back: set[str] = set()
        for i, d in enumerate(deps):
            start = datetime.fromisoformat(d["deployed_at"])
            end = datetime.fromisoformat(deps[i + 1]["deployed_at"]) if i + 1 < len(deps) else _now()
            served[d["version"]] = served.get(d["version"], 0.0) + max(0.0, (end - start).total_seconds() / 3600)
            if d["status"] == "rolled_back":
                rolled_back.add(d["version"])
        rows = c.execute("SELECT * FROM releases WHERE service=? ORDER BY released_at DESC", (service,)).fetchall()
        return [
            {
                **dict(r),
                "hours_in_production": round(served.get(r["version"], 0.0), 1),
                "previously_rolled_back": r["version"] in rolled_back,
                "is_current": r["version"] == st["current_version"],
            }
            for r in rows
        ]


class DeployRequest(BaseModel):
    version: str = Field(pattern=r"^v\d+\.\d+\.\d+$")
    expected_state_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128)
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)


@app.post("/api/services/{service}/deploy", dependencies=[DeployAuth])
def deploy(service: str, body: DeployRequest) -> dict[str, Any]:
    with _lock, _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            prior = c.execute("SELECT * FROM deployments WHERE idempotency_key=?", (body.idempotency_key,)).fetchone()
            if prior is not None:
                c.execute("COMMIT")
                return {"deployment": dict(prior), "replayed": True, "status": _status(c, service)}
            st = _state(c, service)
            if st["state_version"] != body.expected_state_version:
                raise HTTPException(
                    409,
                    {
                        "code": "state_conflict",
                        "message": "Service state changed since the deploy was planned",
                        "expected_state_version": body.expected_state_version,
                        "actual_state_version": st["state_version"],
                        "current_version": st["current_version"],
                    },
                )
            dep = _deploy(c, service, body.version, body.actor, body.reason, body.idempotency_key)
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        return {"deployment": dep, "replayed": False, "status": _status(c, service)}


@app.get("/api/scenarios", dependencies=[ReadAuth])
def list_scenarios() -> list[dict[str, str]]:
    return [{"name": k, "description": v["description"]} for k, v in SCENARIOS.items()]


@app.post("/api/scenarios/{name}/trigger", dependencies=[AdminAuth])
def trigger(name: str) -> dict[str, Any]:
    spec = SCENARIOS.get(name)
    if spec is None:
        raise HTTPException(404, f"Unknown scenario {name}")
    with _lock, _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        if "deploy" in spec:
            service, version = spec["deploy"]
            _deploy(c, service, version, "deploy-bot", f"scenario:{name}", None)
        if "failed_deploy" in spec:
            service, version = spec["failed_deploy"]
            _deploy(
                c,
                service,
                version,
                "deploy-bot",
                f"scenario:{name}: health checks failed, deploy aborted",
                None,
                status="failed",
            )
        if "fault" in spec:
            service, fault = spec["fault"]
            st = _state(c, service)
            faults = {**json.loads(st["faults"]), **fault}
            c.execute("UPDATE service_state SET faults=? WHERE service=?", (json.dumps(faults), service))
        target = (spec.get("deploy") or spec.get("fault") or spec.get("failed_deploy"))[0]
        c.execute(
            "UPDATE service_state SET scenario=?, updated_at=? WHERE service=?", (name, _now().isoformat(), target)
        )
        c.execute("COMMIT")
        return {"scenario": name, "services": [_status(c, s) for s in SERVICES]}


@app.post("/api/scenarios/reset", dependencies=[AdminAuth])
def reset() -> dict[str, Any]:
    with _lock, _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        _seed(c)
        c.execute("COMMIT")
        return {"reset": True, "services": [_status(c, s) for s in SERVICES]}
