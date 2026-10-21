"""Build the demo service git repositories deterministically.

Commits use fixed author/committer identities and timestamps, so every machine produces identical commit
SHAs. The control plane and the GitHub MCP server's local-git provider both read these repositories, so the
release -> commit mapping used during an investigation refers to real git objects.

Usage: python build_repos.py <output_dir>
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

AUTHORS = {
    "maya": ("Maya Chen", "maya.chen@example.com"),
    "omar": ("Omar Haddad", "omar.haddad@example.com"),
    "lena": ("Lena Fischer", "lena.fischer@example.com"),
}

ORDERS_BASE = '''"""Order creation for orders-api (demo service source)."""

from orders.db import pool
from orders.inventory import InventoryClient

inventory = InventoryClient(timeout_seconds=2.0)


async def create_order(cart):
    reservation = await inventory.reserve(cart.items)
    async with pool.connection() as conn:
        async with conn.transaction():
            order_id = await conn.insert_order(cart, reservation.id)
    return order_id
'''

ORDERS_V214_BAD = '''"""Order creation for orders-api (demo service source)."""

from orders.db import pool
from orders.inventory import InventoryClient

inventory = InventoryClient(timeout_seconds=2.0)


async def create_order(cart):
    async with pool.connection() as conn:
        async with conn.transaction():
            order_id = await conn.insert_order(cart, reservation_id=None)
            # Reserve stock inside the transaction so a failed reservation rolls back the order.
            reservation = await inventory.reserve(cart.items)
            await conn.attach_reservation(order_id, reservation.id)
    return order_id
'''

ORDERS_V2141 = ORDERS_BASE.replace(
    "    return order_id\n",
    "    # Never hold a transaction open across a network call (postmortem 2026-03-12).\n    return order_id\n",
)

ORDERS_V215 = ORDERS_V2141.replace(
    "async def create_order(cart):",
    "async def create_order(cart, *, notes: str | None = None):",
).replace("insert_order(cart, reservation.id)", "insert_order(cart, reservation.id, notes=notes)")

ORDERS_V216_BAD = '''"""Order creation for orders-api (demo service source)."""

from orders.db import pool
from orders.inventory import InventoryClient

inventory = InventoryClient(timeout_seconds=2.0)


async def create_order(cart, *, notes: str | None = None):
    async with pool.connection() as conn:
        async with conn.transaction():
            order_id = await conn.insert_order(cart, reservation_id=None, notes=notes)
            # Batch reservation: reserve all line items in one inventory call and attach the
            # reservation in the same transaction to keep order and stock consistent.
            reservation = await inventory.reserve_batch([item.sku for item in cart.items])
            await conn.attach_reservation(order_id, reservation.id)
    return order_id
'''

DB_PY = '''"""Database pool configuration for orders-api (demo service source)."""

import os

POOL_SIZE = int(os.environ.get("ORDERS_DB_POOL_SIZE", "20"))
POOL_TIMEOUT_MS = int(os.environ.get("ORDERS_DB_POOL_TIMEOUT_MS", "3000"))
'''

CHECKOUT_BASE = '''"""Checkout flow for checkout-api (demo service source)."""

from checkout.payments import PaymentClient
from checkout.orders import OrdersClient

payments = PaymentClient(timeout_seconds=10.0, retries=2)
orders = OrdersClient(timeout_seconds=5.0)


async def checkout(cart):
    charge = await payments.charge(cart.total, idempotency_key=cart.id)
    return await orders.create(cart, charge_id=charge.id)
'''

CHECKOUT_V39 = (
    CHECKOUT_BASE.replace(
        "orders = OrdersClient(timeout_seconds=5.0)\n",
        "orders = OrdersClient(timeout_seconds=5.0)\nfraud = FraudClient()  # no timeout configured\n",
    )
    .replace(
        "from checkout.orders import OrdersClient\n",
        "from checkout.orders import OrdersClient\nfrom checkout.fraud import FraudClient\n",
    )
    .replace(
        "    charge = await payments.charge",
        "    await fraud.score(cart)  # synchronous risk check before charging\n    charge = await payments.charge",
    )
)

# (repo, version, author, iso timestamp, message, {path: content})
HISTORY = [
    (
        "orders-api",
        "v2.13.2",
        "maya",
        "2026-02-20T10:15:00+00:00",
        "Retry inventory reservation on 503",
        {"orders/service.py": ORDERS_BASE, "orders/db.py": DB_PY, "VERSION": "2.13.2\n"},
    ),
    (
        "orders-api",
        "v2.14.0",
        "omar",
        "2026-03-12T13:40:00+00:00",
        "Reserve inventory inside the order transaction for consistency",
        {"orders/service.py": ORDERS_V214_BAD, "VERSION": "2.14.0\n"},
    ),
    (
        "orders-api",
        "v2.14.1",
        "maya",
        "2026-03-13T09:05:00+00:00",
        "Revert: do not hold a transaction across the inventory call",
        {"orders/service.py": ORDERS_V2141, "VERSION": "2.14.1\n"},
    ),
    (
        "orders-api",
        "v2.15.0",
        "lena",
        "2026-09-29T11:30:00+00:00",
        "Support free-text order notes",
        {"orders/service.py": ORDERS_V215, "VERSION": "2.15.0\n"},
    ),
    (
        "orders-api",
        "v2.16.0",
        "omar",
        "2026-10-20T14:02:00+00:00",
        "Batch inventory reservation for multi-item orders",
        {"orders/service.py": ORDERS_V216_BAD, "VERSION": "2.16.0\n"},
    ),
    (
        "checkout-api",
        "v3.8.0",
        "lena",
        "2026-08-03T10:00:00+00:00",
        "Idempotent payment charges",
        {"checkout/flow.py": CHECKOUT_BASE, "VERSION": "3.8.0\n"},
    ),
    (
        "checkout-api",
        "v3.8.1",
        "maya",
        "2026-09-15T15:20:00+00:00",
        "Raise payment timeout logging level",
        {"checkout/flow.py": CHECKOUT_BASE + "\n# payment timeouts are logged at WARNING\n", "VERSION": "3.8.1\n"},
    ),
    (
        "checkout-api",
        "v3.9.0",
        "omar",
        "2026-10-20T16:45:00+00:00",
        "Add fraud scoring before charging",
        {"checkout/flow.py": CHECKOUT_V39, "VERSION": "3.9.0\n"},
    ),
]


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, env={**os.environ, **(env or {})}
    ).stdout.strip()


def build(out: Path) -> dict[str, dict[str, str]]:
    if out.exists():
        shutil.rmtree(out)
    shas: dict[str, dict[str, str]] = {}
    for repo_name, version, who, ts, message, files in HISTORY:
        repo = out / repo_name
        if not repo.exists():
            repo.mkdir(parents=True)
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "commit.gpgsign", "false")
        for path, content in files.items():
            p = repo / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        git(repo, "add", "-A")
        name, email = AUTHORS[who]
        env = {
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_AUTHOR_DATE": ts,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
            "GIT_COMMITTER_DATE": ts,
        }
        git(repo, "commit", "-q", "-m", message, env=env)
        git(repo, "tag", version, env=env)
        shas.setdefault(repo_name, {})[version] = git(repo, "rev-parse", "HEAD")
    return shas


if __name__ == "__main__":
    result = build(Path(sys.argv[1]))
    for repo, versions in result.items():
        for v, sha in versions.items():
            print(f"{repo} {v} {sha}")
