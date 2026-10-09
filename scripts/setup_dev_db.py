"""One-off dev database setup for the Cistern Neon instance.

Creates everything phase 10 integration testing needs, idempotently:

1. Cistern's own bookkeeping tables (``query_log``, ``benchmark_results``)
   via alembic — the same migration CI runs.
2. The e-commerce schema the benchmark declares in
   ``backend/eval/benchmark_subset.json`` (``schema_assumptions``), seeded
   with a small deterministic dataset.
3. SELECT-only grants for ``readonly_user`` on the e-commerce tables, plus
   the write-capable role's own tables staying admin-only.

Run as:  python scripts/setup_dev_db.py
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = ROOT / "backend" / "alembic.ini"
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from backend.db.connection import AdminSessionLocal, dispose_engines  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS customers (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    email      TEXT NOT NULL UNIQUE,
    country    TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS categories (
    id   SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS products (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    category_id INTEGER REFERENCES categories(id),
    price       NUMERIC(10, 2) NOT NULL,
    stock       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS orders (
    id          SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    status      TEXT NOT NULL,
    total       NUMERIC(10, 2) NOT NULL,
    placed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS order_items (
    id         SERIAL PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity   INTEGER NOT NULL,
    unit_price NUMERIC(10, 2) NOT NULL
);
"""

SEED = """
INSERT INTO customers (id, name, email, country, created_at) VALUES
  (1, 'Alice Meyer',   'alice@example.com',  'Germany', now() - interval '400 days'),
  (2, 'Bob Tanaka',    'bob@example.com',    'Japan',   now() - interval '200 days'),
  (3, 'Carla Rossi',   'carla@example.com',  'Italy',   now() - interval '100 days'),
  (4, 'Dev Patel',     'dev@example.com',    'India',   now() - interval '50 days'),
  (5, 'Erik Larsson',  'erik@example.com',   'Sweden',  now() - interval '20 days'),
  (6, 'Franz Huber',   'franz@example.com',  'Germany', now() - interval '10 days'),
  (7, 'Grete Nansen',  'grete@example.com',  NULL,      now() - interval '5 days')
ON CONFLICT (id) DO NOTHING;

INSERT INTO categories (id, name) VALUES
  (1, 'Electronics'), (2, 'Books'), (3, 'Kitchen')
ON CONFLICT (id) DO NOTHING;

INSERT INTO products (id, name, category_id, price, stock) VALUES
  (1, 'Laptop',          1, 1200.00, 10),
  (2, 'Headphones',      1,  150.00, 40),
  (3, 'Novel',           2,   20.00, 100),
  (4, 'Cookbook',        2,   35.00, 25),
  (5, 'Blender',         3,   90.00, 15),
  (6, 'Discontinued Fax', 1,  60.00, 0)
ON CONFLICT (id) DO NOTHING;

INSERT INTO orders (id, customer_id, status, total, placed_at) VALUES
  (1, 1, 'delivered', 1350.00, now() - interval '90 days'),
  (2, 1, 'delivered',   20.00, now() - interval '45 days'),
  (3, 2, 'shipped',    240.00, now() - interval '20 days'),
  (4, 3, 'pending',     90.00, now() - interval '10 days'),
  (5, 4, 'delivered',   55.00, now() - interval '8 days'),
  (6, 5, 'cancelled',  150.00, now() - interval '3 days'),
  (7, 6, 'pending',   1200.00, now() - interval '1 days')
ON CONFLICT (id) DO NOTHING;

INSERT INTO order_items (id, order_id, product_id, quantity, unit_price) VALUES
  (1, 1, 1, 1, 1200.00),
  (2, 1, 2, 1,  150.00),
  (3, 2, 3, 1,   20.00),
  (4, 3, 2, 1,  150.00),
  (5, 3, 5, 1,   90.00),
  (6, 4, 5, 1,   90.00),
  (7, 5, 3, 1,   20.00),
  (8, 5, 4, 1,   35.00),
  (9, 6, 2, 1,  150.00),
  (10, 7, 1, 1, 1200.00)
ON CONFLICT (id) DO NOTHING;

SELECT setval('customers_id_seq',   (SELECT MAX(id) FROM customers));
SELECT setval('categories_id_seq',  (SELECT MAX(id) FROM categories));
SELECT setval('products_id_seq',    (SELECT MAX(id) FROM products));
SELECT setval('orders_id_seq',      (SELECT MAX(id) FROM orders));
SELECT setval('order_items_id_seq', (SELECT MAX(id) FROM order_items));
"""

GRANTS = """
GRANT USAGE ON SCHEMA public TO readonly_user;
GRANT SELECT ON customers, categories, products, orders, order_items TO readonly_user;
"""


async def seed() -> None:
    async with AdminSessionLocal() as session:
        for block in (DDL, SEED, GRANTS):
            for statement in block.split(";"):
                if statement.strip():
                    await session.execute(text(statement))
        await session.commit()
    await dispose_engines()
    print("e-commerce schema created, seeded, and granted")


def migrate() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    print(
        result.stdout[-500:] if result.stdout else "", result.stderr[-500:] if result.stderr else ""
    )
    if result.returncode != 0:
        raise SystemExit("alembic upgrade failed")
    print("alembic migrations applied (query_log, benchmark_results)")


if __name__ == "__main__":
    migrate()
    asyncio.run(seed())
