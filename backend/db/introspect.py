"""Database schema introspection, run on the admin engine.

Produces a JSON-serialisable description of the target schema:

    {table_name: {"columns": [{"name", "type", "nullable"}],
                  "primary_key": [col, ...],
                  "foreign_keys": [{"column", "ref_table", "ref_column"}]}}

The result is cached in a module-level dict. ``get_schema()`` populates it on
first call; ``refresh_schema()`` forces a re-query.
"""

import asyncio
from typing import Any

from sqlalchemy import text

from backend.db.connection import AdminSessionLocal
from backend.db.models import Base

DEFAULT_SCHEMA = "public"

# Cistern's own bookkeeping tables (query_log, benchmark_results). They live
# in the same schema as the user's data but are not part of it: exposing them
# to the generator would let any natural-language question read the audit log.
# Derived from the models rather than listed, so a bookkeeping table added in a
# later migration is hidden automatically instead of silently appearing.
_INTERNAL_TABLES = frozenset(Base.metadata.tables.keys())

# Tables owned by migration tooling rather than by this application. These
# cannot come from ``Base.metadata`` — alembic creates and owns its version
# table itself — so they are the one thing that has to be named explicitly.
# "alembic_version" is alembic's default ``version_table``, which
# backend/alembic/env.py does not override.
_MIGRATION_TABLES = frozenset({"alembic_version"})

# Everything withheld from the schema the SQL generator is shown.
EXCLUDED_TABLES = _INTERNAL_TABLES | _MIGRATION_TABLES

_COLUMNS_SQL = text("""
    SELECT table_name, column_name, data_type, is_nullable, ordinal_position
    FROM information_schema.columns
    WHERE table_schema = :schema
    ORDER BY table_name, ordinal_position
    """)

# Restricted to BASE TABLE so views do not appear as writable relations.
_TABLES_SQL = text("""
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = :schema AND table_type = 'BASE TABLE'
    ORDER BY table_name
    """)

_PRIMARY_KEYS_SQL = text("""
    SELECT kcu.table_name, kcu.column_name
    FROM information_schema.table_constraints AS tc
    JOIN information_schema.key_column_usage AS kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.constraint_schema = kcu.constraint_schema
    WHERE tc.constraint_type = 'PRIMARY KEY'
      AND tc.table_schema = :schema
    ORDER BY kcu.table_name, kcu.ordinal_position
    """)

# Referenced columns are reached through referential_constraints and a second
# key_column_usage join, matched on position_in_unique_constraint. Joining
# constraint_column_usage directly would produce a cross product on composite
# foreign keys and mispair the columns.
_FOREIGN_KEYS_SQL = text("""
    SELECT
        kcu.table_name    AS table_name,
        kcu.column_name   AS column_name,
        ref.table_name    AS ref_table,
        ref.column_name   AS ref_column
    FROM information_schema.table_constraints AS tc
    JOIN information_schema.key_column_usage AS kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.constraint_schema = kcu.constraint_schema
    JOIN information_schema.referential_constraints AS rc
      ON tc.constraint_name = rc.constraint_name
     AND tc.constraint_schema = rc.constraint_schema
    JOIN information_schema.key_column_usage AS ref
      ON rc.unique_constraint_name = ref.constraint_name
     AND rc.unique_constraint_schema = ref.constraint_schema
     AND kcu.position_in_unique_constraint = ref.ordinal_position
    WHERE tc.constraint_type = 'FOREIGN KEY'
      AND tc.table_schema = :schema
    ORDER BY kcu.table_name, kcu.ordinal_position
    """)

_cache: dict[str, dict[str, Any]] = {}
_lock = asyncio.Lock()


async def _query_schema(schema: str = DEFAULT_SCHEMA) -> dict[str, dict[str, Any]]:
    """Read the live schema from the admin connection."""
    params = {"schema": schema}

    async with AdminSessionLocal() as session:
        tables = (await session.execute(_TABLES_SQL, params)).all()
        columns = (await session.execute(_COLUMNS_SQL, params)).all()
        primary_keys = (await session.execute(_PRIMARY_KEYS_SQL, params)).all()
        foreign_keys = (await session.execute(_FOREIGN_KEYS_SQL, params)).all()

    result: dict[str, dict[str, Any]] = {
        row.table_name: {"columns": [], "primary_key": [], "foreign_keys": []}
        for row in tables
        if row.table_name not in EXCLUDED_TABLES
    }

    for row in columns:
        table = result.get(row.table_name)
        if table is None:  # a view or a table we filtered out
            continue
        table["columns"].append(
            {
                "name": row.column_name,
                "type": row.data_type,
                "nullable": row.is_nullable == "YES",
            }
        )

    for row in primary_keys:
        table = result.get(row.table_name)
        if table is not None:
            table["primary_key"].append(row.column_name)

    for row in foreign_keys:
        table = result.get(row.table_name)
        if table is not None:
            table["foreign_keys"].append(
                {
                    "column": row.column_name,
                    "ref_table": row.ref_table,
                    "ref_column": row.ref_column,
                }
            )

    return result


async def get_schema(schema: str = DEFAULT_SCHEMA) -> dict[str, dict[str, Any]]:
    """Return the cached schema, querying the database on first call."""
    if _cache:
        return _cache
    return await refresh_schema(schema)


async def refresh_schema(schema: str = DEFAULT_SCHEMA) -> dict[str, dict[str, Any]]:
    """Force a re-query and replace the cache."""
    async with _lock:
        fresh = await _query_schema(schema)
        _cache.clear()
        _cache.update(fresh)
    return _cache
