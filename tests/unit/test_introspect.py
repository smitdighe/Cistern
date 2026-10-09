"""Which tables introspection withholds from the SQL generator.

The exclusion set is the only thing standing between a natural-language
question and Cistern's own audit log, so it is asserted directly rather than
inferred from a live introspection run. No database is needed: the set is
built at import time.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://ro:pw@placeholder.invalid/db")
os.environ.setdefault("DATABASE_ADMIN_URL", "postgresql://admin:pw@placeholder.invalid/db")

from backend.db import introspect  # noqa: E402


def test_bookkeeping_tables_are_excluded():
    """Derived from Base.metadata — a new model is hidden without editing this."""
    assert "query_log" in introspect.EXCLUDED_TABLES
    assert "benchmark_results" in introspect.EXCLUDED_TABLES


def test_migration_tooling_table_is_excluded():
    """alembic owns this table, so Base.metadata cannot supply it."""
    assert "alembic_version" in introspect.EXCLUDED_TABLES


def test_application_tables_are_not_excluded():
    """Guards against an over-broad filter hiding the data users ask about."""
    for table in ("customers", "orders", "order_items", "products", "categories"):
        assert table not in introspect.EXCLUDED_TABLES
