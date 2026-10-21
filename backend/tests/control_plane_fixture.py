"""Runs the real demo control plane (FastAPI + SQLite + demo git repos) in-process for tests."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
TOKENS = {
    "read": "test-read-token-0123456789",
    "deploy": "test-deploy-token-0123456789",
    "admin": "test-admin-token-0123456789",
}


@pytest.fixture
def control_plane_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    repos = tmp_path / "repos"
    subprocess.run(
        [sys.executable, str(ROOT / "demo/repos/build_repos.py"), str(repos)], check=True, capture_output=True
    )
    monkeypatch.setenv("CONTROL_PLANE_DB", str(tmp_path / "cp.db"))
    monkeypatch.setenv("DEMO_REPOS_DIR", str(repos))
    for k, v in TOKENS.items():
        monkeypatch.setenv(f"CONTROL_PLANE_{k.upper()}_TOKEN", v)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import demo.control_plane.app as cp

    cp = importlib.reload(cp)
    cp.init_db()
    return cp


def transport(cp: ModuleType) -> httpx.ASGITransport:
    return httpx.ASGITransport(app=cp.app)
