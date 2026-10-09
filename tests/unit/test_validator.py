"""Safety validator behaviour.

The schema below stands in for ``introspect.get_schema()`` output.
"""

import pytest

from backend.safety.validator import validate_and_prepare

SCHEMA = {
    "orders": {
        "columns": [
            {"name": "id", "type": "integer", "nullable": False},
            {"name": "customer_id", "type": "integer", "nullable": False},
            {"name": "total", "type": "numeric", "nullable": True},
            {"name": "placed_at", "type": "timestamp", "nullable": False},
        ],
        "primary_key": ["id"],
        "foreign_keys": [{"column": "customer_id", "ref_table": "customers", "ref_column": "id"}],
    },
    "customers": {
        "columns": [
            {"name": "id", "type": "integer", "nullable": False},
            {"name": "name", "type": "text", "nullable": False},
            {"name": "country", "type": "text", "nullable": True},
        ],
        "primary_key": ["id"],
        "foreign_keys": [],
    },
}


# --- forbidden statements ---------------------------------------------------


def test_drop_is_rejected():
    result = validate_and_prepare("DROP TABLE orders", SCHEMA)

    assert result.is_safe is False
    assert result.requires_confirmation is False, "DROP must have no confirmation path"
    assert result.final_sql is None
    assert "forbidden_statement" in result.flags
    assert "DROP" in result.rejection_reason


def test_truncate_is_rejected():
    result = validate_and_prepare("TRUNCATE TABLE orders", SCHEMA)

    assert result.is_safe is False
    assert result.requires_confirmation is False, "TRUNCATE must have no confirmation path"
    assert result.final_sql is None
    assert "forbidden_statement" in result.flags
    assert "TRUNCATETABLE" in result.rejection_reason.upper().replace(" ", "")


def test_drop_hidden_behind_a_comment_is_still_rejected():
    """Text matching would be fooled here; the parser is not."""
    result = validate_and_prepare("/* SELECT */ DROP TABLE orders", SCHEMA)

    assert result.is_safe is False
    assert "forbidden_statement" in result.flags


# --- multi-statement --------------------------------------------------------


def test_stacked_statement_is_rejected():
    result = validate_and_prepare("SELECT id FROM orders; DROP TABLE orders", SCHEMA)

    assert result.is_safe is False
    assert "multiple_statements" in result.flags
    assert result.final_sql is None


def test_drop_inside_a_string_literal_is_not_a_drop():
    """A literal containing 'DROP TABLE' is data, not a statement."""
    result = validate_and_prepare(
        "SELECT id FROM customers WHERE name = 'DROP TABLE orders'", SCHEMA
    )

    assert result.is_safe is True
    assert "forbidden_statement" not in result.flags


# --- WHERE floor ------------------------------------------------------------


def test_delete_without_where_is_rejected():
    result = validate_and_prepare("DELETE FROM orders", SCHEMA)

    assert result.is_safe is False
    assert "missing_where" in result.flags
    assert result.final_sql is None
    assert "every row" in result.rejection_reason


def test_update_without_where_is_rejected():
    result = validate_and_prepare("UPDATE orders SET total = 0", SCHEMA)

    assert result.is_safe is False
    assert "missing_where" in result.flags


def test_delete_with_where_requires_confirmation_and_is_not_auto_executable():
    result = validate_and_prepare("DELETE FROM orders WHERE id = 42", SCHEMA)

    assert result.is_safe is True
    assert result.requires_confirmation is True
    assert result.may_auto_execute is False, "a DELETE must never auto-execute"
    assert "requires_confirmation" in result.flags
    assert result.final_sql is not None


def test_update_with_where_requires_confirmation():
    result = validate_and_prepare("UPDATE orders SET total = 0 WHERE id = 42", SCHEMA)

    assert result.is_safe is True
    assert result.requires_confirmation is True
    assert result.may_auto_execute is False


def test_insert_requires_confirmation():
    result = validate_and_prepare(
        "INSERT INTO orders (id, customer_id, total, placed_at) " "VALUES (1, 1, 5, now())",
        SCHEMA,
    )

    assert result.requires_confirmation is True
    assert result.may_auto_execute is False


# --- identifier grounding ---------------------------------------------------


def test_hallucinated_column_names_the_bad_identifier():
    result = validate_and_prepare("SELECT revenue FROM orders", SCHEMA)

    assert result.is_safe is False
    assert "unknown_column" in result.flags
    assert "revenue" in result.rejection_reason


def test_hallucinated_qualified_column_names_table_and_column():
    result = validate_and_prepare("SELECT o.revenue FROM orders o", SCHEMA)

    assert result.is_safe is False
    assert "unknown_column" in result.flags
    assert "revenue" in result.rejection_reason
    assert "orders" in result.rejection_reason


def test_hallucinated_table_names_the_bad_identifier():
    result = validate_and_prepare("SELECT id FROM invoices", SCHEMA)

    assert result.is_safe is False
    assert "unknown_table" in result.flags
    assert "invoices" in result.rejection_reason


def test_unknown_alias_is_rejected():
    result = validate_and_prepare("SELECT x.id FROM orders o", SCHEMA)

    assert result.is_safe is False
    assert "x" in result.rejection_reason


def test_column_from_wrong_table_is_rejected():
    """`country` exists, but on customers — not on orders."""
    result = validate_and_prepare("SELECT o.country FROM orders o", SCHEMA)

    assert result.is_safe is False
    assert "country" in result.rejection_reason
    assert "orders" in result.rejection_reason


def test_identifier_matching_is_case_insensitive():
    result = validate_and_prepare("SELECT ID, Total FROM ORDERS", SCHEMA)

    assert result.is_safe is True, result.rejection_reason


# --- valid queries ----------------------------------------------------------


def test_plain_select_passes_and_gets_a_limit():
    result = validate_and_prepare("SELECT id, total FROM orders", SCHEMA)

    assert result.is_safe is True
    assert result.requires_confirmation is False
    assert result.may_auto_execute is True
    assert result.rejection_reason is None
    assert result.final_sql == "SELECT id, total FROM orders LIMIT 500"
    assert "limit_injected:500" in result.flags


def test_existing_limit_is_preserved_not_replaced():
    result = validate_and_prepare("SELECT id FROM orders LIMIT 3", SCHEMA)

    assert result.is_safe is True
    assert result.final_sql == "SELECT id FROM orders LIMIT 3"
    assert "limit_present" in result.flags
    assert not any(f.startswith("limit_injected") for f in result.flags)


def test_join_with_aliases_passes():
    result = validate_and_prepare(
        "SELECT c.name, SUM(o.total) AS spend "
        "FROM orders o JOIN customers c ON c.id = o.customer_id "
        "GROUP BY c.name ORDER BY spend DESC",
        SCHEMA,
    )

    assert result.is_safe is True, result.rejection_reason
    assert result.final_sql.endswith("LIMIT 500")


def test_select_star_passes():
    result = validate_and_prepare("SELECT * FROM orders", SCHEMA)

    assert result.is_safe is True, result.rejection_reason


def test_cte_query_passes():
    result = validate_and_prepare(
        "WITH recent AS (SELECT id, total FROM orders WHERE total > 10) " "SELECT id FROM recent",
        SCHEMA,
    )

    assert result.is_safe is True, result.rejection_reason


def test_cte_does_not_launder_a_hallucinated_column():
    result = validate_and_prepare(
        "WITH recent AS (SELECT id, revenue FROM orders) SELECT id FROM recent",
        SCHEMA,
    )

    assert result.is_safe is False
    assert "revenue" in result.rejection_reason


# --- malformed input --------------------------------------------------------


@pytest.mark.parametrize("sql", ["", "   ", "not sql at all ((("])
def test_unusable_input_is_rejected(sql):
    result = validate_and_prepare(sql, SCHEMA)

    assert result.is_safe is False
    assert result.final_sql is None
    assert result.rejection_reason
