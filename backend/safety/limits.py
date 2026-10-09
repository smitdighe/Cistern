"""Row-count and time bounds applied to queries before execution.

LIMIT injection happens on the sqlglot expression tree. Appending " LIMIT n"
to SQL text would land inside the wrong statement on a compound query, break
on a trailing comment, and silently corrupt anything ending in a set operation
or a locking clause. The AST cannot be fooled that way.
"""

from __future__ import annotations

from sqlglot import exp

from backend.config import settings


def default_query_limit() -> int:
    """Row cap injected into unbounded SELECTs."""
    return int(settings.DEFAULT_QUERY_LIMIT)


def query_timeout_seconds() -> int:
    """Statement timeout for query execution.

    Consumed by ``backend/db/executor.py`` in a later phase — exposed here so
    the value has a single home alongside the row cap.
    """
    return int(settings.QUERY_TIMEOUT_SECONDS)


# What apply_limit did to the statement, surfaced so the validator can flag it.
LIMIT_INJECTED = "injected"
LIMIT_CLAMPED = "clamped"


def has_limit(statement: exp.Expression) -> bool:
    """True when the statement already carries its own LIMIT."""
    return statement.args.get("limit") is not None


def _existing_limit_value(statement: exp.Expression) -> int | None:
    """The literal value of an existing LIMIT, or None when non-literal."""
    limit = statement.args.get("limit")
    if limit is None:
        return None
    value = limit.expression
    if not isinstance(value, exp.Literal) or value.is_string:
        return None
    try:
        return int(value.name)
    except (TypeError, ValueError):
        return None


def apply_limit(
    statement: exp.Expression, limit: int | None = None
) -> tuple[exp.Expression, str | None]:
    """Return ``(statement, action)`` with a bounded LIMIT guaranteed on SELECTs.

    ``action`` is ``LIMIT_INJECTED`` when no LIMIT existed, ``LIMIT_CLAMPED``
    when an existing LIMIT exceeded the cap (or was not a plain integer
    literal) and was replaced by it, and ``None`` when the statement's own
    LIMIT was within bounds and kept as written.

    Clamping is deliberate and loud, not silent: the adversarial suite
    (phase 9) treats ``LIMIT 10000000`` as an unbounded scan — a caller who
    can name any ceiling faces no ceiling — and the validator surfaces every
    clamp as a ``limit_clamped:N`` safety flag so the caller can see the
    result set was truncated.
    """
    if not isinstance(statement, exp.Select):
        return statement, None

    cap = default_query_limit() if limit is None else int(limit)

    if has_limit(statement):
        existing = _existing_limit_value(statement)
        if existing is not None and existing <= cap:
            return statement, None
        return statement.limit(cap), LIMIT_CLAMPED

    return statement.limit(cap), LIMIT_INJECTED
