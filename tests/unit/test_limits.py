"""LIMIT injection and bound configuration."""

import sqlglot
from sqlglot import exp

from backend.safety import limits


def _parse(sql: str) -> exp.Expression:
    return sqlglot.parse_one(sql, dialect="postgres")


def test_limit_injected_when_absent():
    statement, action = limits.apply_limit(_parse("SELECT id FROM orders"))

    assert action == limits.LIMIT_INJECTED
    assert statement.sql(dialect="postgres") == "SELECT id FROM orders LIMIT 500"


def test_existing_limit_within_cap_left_untouched():
    statement, action = limits.apply_limit(_parse("SELECT id FROM orders LIMIT 7"))

    assert action is None
    assert statement.sql(dialect="postgres") == "SELECT id FROM orders LIMIT 7"


def test_existing_limit_above_cap_is_clamped():
    """A caller who can name any ceiling faces no ceiling (adversarial suite,
    phase 9). Oversized LIMITs are clamped to the cap and flagged, not kept."""
    statement, action = limits.apply_limit(_parse("SELECT id FROM orders LIMIT 10000"))

    assert action == limits.LIMIT_CLAMPED
    assert statement.sql(dialect="postgres").endswith("LIMIT 500")


def test_non_literal_limit_is_clamped():
    """A LIMIT that is not a plain integer literal cannot be verified as
    bounded, so it is replaced by the cap."""
    statement, action = limits.apply_limit(
        _parse("SELECT id FROM orders LIMIT (SELECT COUNT(*) FROM orders)")
    )

    assert action == limits.LIMIT_CLAMPED
    assert statement.sql(dialect="postgres").endswith("LIMIT 500")


def test_limit_injection_survives_order_by():
    statement, action = limits.apply_limit(_parse("SELECT id FROM orders ORDER BY total DESC"))

    assert action == limits.LIMIT_INJECTED
    assert statement.sql(dialect="postgres").endswith("ORDER BY total DESC LIMIT 500")


def test_limit_injection_targets_outer_query_not_cte():
    statement, action = limits.apply_limit(
        _parse("WITH r AS (SELECT id FROM orders) SELECT id FROM r")
    )

    rendered = statement.sql(dialect="postgres")
    assert action == limits.LIMIT_INJECTED
    assert rendered.endswith("SELECT id FROM r LIMIT 500")
    assert "SELECT id FROM orders LIMIT" not in rendered


def test_non_select_is_not_given_a_limit():
    statement, action = limits.apply_limit(_parse("DELETE FROM orders WHERE id = 1"))

    assert action is None
    assert "LIMIT" not in statement.sql(dialect="postgres").upper()


def test_explicit_limit_argument_overrides_config():
    statement, action = limits.apply_limit(_parse("SELECT id FROM orders"), limit=25)

    assert action == limits.LIMIT_INJECTED
    assert statement.sql(dialect="postgres").endswith("LIMIT 25")


def test_has_limit_detection():
    assert limits.has_limit(_parse("SELECT 1 FROM t LIMIT 3")) is True
    assert limits.has_limit(_parse("SELECT 1 FROM t")) is False


def test_configured_bounds_exposed():
    assert limits.default_query_limit() == 500
    assert limits.query_timeout_seconds() == 10
