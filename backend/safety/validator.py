"""AST-based SQL safety validation.

Every decision in this module is made by inspecting the sqlglot expression
tree. Nothing here regex-matches, substring-searches or otherwise inspects raw
SQL text. Text matching is defeated by comments, string literals, unicode
escapes, nested quoting and whitespace tricks; a parser is not.

Order of checks matters and is deliberate:

1. parse — unparseable SQL is rejected before anything else looks at it
2. single statement — a second statement is rejected outright
3. forbidden statement type — DROP/TRUNCATE die here, before any other logic
4. WHERE floor — DELETE/UPDATE without WHERE is rejected
5. identifier grounding — every table and column must exist in the schema
6. limits — LIMIT injected on unbounded SELECTs
7. policy — the confirmation requirement is attached last

A passing result is not permission to execute. ``requires_confirmation`` must
be honoured; use ``may_auto_execute`` rather than reading ``is_safe`` alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import build_scope

from backend.safety import limits
from backend.safety.allowlist import Disposition, classify

DIALECT = "postgres"

# Statement types where a missing WHERE means "every row in the table".
_WHERE_REQUIRED_TYPES: tuple[type[exp.Expression], ...] = (exp.Delete, exp.Update)


@dataclass(slots=True)
class ValidationResult:
    """Outcome of validating one statement.

    ``is_safe`` means the statement passed every check. It does **not** mean
    the statement may run — a DELETE with a WHERE clause is safe and still
    requires a human decision. Callers should gate execution on
    ``may_auto_execute``.
    """

    is_safe: bool
    final_sql: str | None
    flags: list[str] = field(default_factory=list)
    requires_confirmation: bool = False
    rejection_reason: str | None = None

    @property
    def may_auto_execute(self) -> bool:
        """True only when the statement is safe *and* needs no confirmation."""
        return self.is_safe and not self.requires_confirmation and self.final_sql is not None


def _reject(reason: str, flags: list[str]) -> ValidationResult:
    return ValidationResult(
        is_safe=False,
        final_sql=None,
        flags=flags,
        requires_confirmation=False,
        rejection_reason=reason,
    )


def _normalise(name: str) -> str:
    """Fold an identifier for comparison.

    Postgres downcases unquoted identifiers; sqlglot preserves whatever the
    model wrote. Compare on a common footing.
    """
    return name.strip().lower()


def _schema_index(schema: dict[str, Any]) -> dict[str, set[str]]:
    """Map normalised table name to its set of normalised column names."""
    index: dict[str, set[str]] = {}
    for table_name, table in (schema or {}).items():
        columns = (table or {}).get("columns") or []
        index[_normalise(table_name)] = {_normalise(c["name"]) for c in columns}
    return index


@dataclass(slots=True)
class _NameScope:
    """The names visible inside one query scope."""

    # alias-or-name -> real table name, for physical tables only
    physical: dict[str, str] = field(default_factory=dict)
    # alias -> output column names, for CTEs and derived tables
    virtual: dict[str, set[str]] = field(default_factory=dict)
    # names usable unqualified: projection aliases of this scope
    local_aliases: set[str] = field(default_factory=set)

    def visible_columns(self, index: dict[str, set[str]]) -> set[str]:
        """Every column name resolvable without a qualifier in this scope."""
        names = set(self.local_aliases)
        for real_table in self.physical.values():
            names |= index.get(real_table, set())
        for outputs in self.virtual.values():
            names |= outputs
        return names


def _output_columns(source: Any) -> set[str]:
    """Projection names of a CTE or derived-table scope."""
    expression = getattr(source, "expression", None)
    try:
        return {_normalise(n) for n in expression.named_selects if n}
    except (AttributeError, TypeError):
        return set()


def _local_aliases(scope_expression: exp.Expression) -> set[str]:
    """Projection aliases declared directly by this scope."""
    aliases: set[str] = set()
    for projection in getattr(scope_expression, "expressions", []) or []:
        if isinstance(projection, exp.Alias):
            alias = _normalise(projection.alias or "")
            if alias:
                aliases.add(alias)
    return aliases


def _check_scope(
    name_scope: _NameScope,
    columns: list[exp.Column],
    index: dict[str, set[str]],
) -> str | None:
    """Ground every table and column visible in one scope."""
    for real_table in sorted(set(name_scope.physical.values())):
        if real_table not in index:
            known = ", ".join(sorted(index)) or "(schema is empty)"
            return (
                f"unknown table '{real_table}' — not present in the database schema. "
                f"Known tables: {known}"
            )

    unqualified = name_scope.visible_columns(index)

    for column in columns:
        # `SELECT *` and `SELECT t.*` carry a Star, not a real column name.
        if isinstance(column.this, exp.Star):
            continue

        name = _normalise(column.name)
        if not name:
            continue

        qualifier = _normalise(column.table or "")

        if not qualifier:
            if name not in unqualified:
                known = ", ".join(sorted(unqualified)) or "(none)"
                return (
                    f"unknown column '{name}' — not present in any referenced table. "
                    f"Known columns: {known}"
                )
            continue

        if qualifier in name_scope.virtual:
            outputs = name_scope.virtual[qualifier]
            if outputs and name not in outputs:
                known = ", ".join(sorted(outputs)) or "(none)"
                return (
                    f"unknown column '{name}' — '{qualifier}' does not produce a "
                    f"column named '{name}'. Known columns: {known}"
                )
            continue

        real_table = name_scope.physical.get(qualifier)
        if real_table is None:
            return (
                f"unknown table alias '{qualifier}' in reference "
                f"'{qualifier}.{name}' — no such table or alias in this query"
            )

        table_columns = index.get(real_table, set())
        if name not in table_columns:
            known = ", ".join(sorted(table_columns)) or "(none)"
            return (
                f"unknown column '{name}' on table '{real_table}' "
                f"(referenced as '{qualifier}.{name}'). Known columns: {known}"
            )

    return None


def _check_identifiers(statement: exp.Expression, index: dict[str, set[str]]) -> str | None:
    """Ground every identifier, one query scope at a time.

    Scope-by-scope matters: a CTE's projection names are visible to the outer
    query but must not be visible inside the CTE's own body. Validating the
    whole tree against one flat name pool lets
    ``WITH c AS (SELECT hallucinated FROM t) SELECT ...`` launder an invented
    column into legitimacy, because the CTE's output list vouches for it.
    """
    root = build_scope(statement)

    if root is None:
        # DML (DELETE/UPDATE/INSERT) has no scope tree. These are flat by
        # construction — a single target table, no CTE laundering possible.
        name_scope = _NameScope()
        for table in statement.find_all(exp.Table):
            table_name = _normalise(table.name)
            if not table_name:
                continue
            name_scope.physical[table_name] = table_name
            alias = _normalise(table.alias or "")
            if alias:
                name_scope.physical[alias] = table_name
        return _check_scope(name_scope, list(statement.find_all(exp.Column)), index)

    for scope in root.traverse():
        name_scope = _NameScope(local_aliases=_local_aliases(scope.expression))

        for source_name, source in scope.sources.items():
            key = _normalise(source_name)
            if isinstance(source, exp.Table):
                name_scope.physical[key] = _normalise(source.name)
            else:
                name_scope.virtual[key] = _output_columns(source)

        error = _check_scope(name_scope, list(scope.columns), index)
        if error is not None:
            return error

    return None


def validate_and_prepare(sql: str, schema: dict[str, Any]) -> ValidationResult:
    """Validate ``sql`` against ``schema`` and return the execution-ready form.

    ``schema`` is the dict produced by ``backend.db.introspect.get_schema()``.
    """
    flags: list[str] = []

    if not sql or not sql.strip():
        return _reject("empty SQL string", flags)

    # 1. Parse. Anything sqlglot cannot read, we will not run.
    try:
        statements = sqlglot.parse(sql, dialect=DIALECT)
    except ParseError as exc:
        return _reject(f"SQL could not be parsed: {exc}", flags)

    statements = [s for s in statements if s is not None]
    if not statements:
        return _reject("no statement found in input", flags)

    # 2. One statement only. A trailing statement is how stacked-query
    #    injection arrives, and nothing legitimate needs it here.
    if len(statements) > 1:
        flags.append("multiple_statements")
        return _reject(
            f"expected a single statement, found {len(statements)} — "
            "multiple statements are never executed",
            flags,
        )

    statement = statements[0]

    # 3. Statement-type policy. Forbidden types stop here.
    decision = classify(statement)
    flags.append(f"statement:{decision.statement_type.lower()}")

    if decision.is_forbidden:
        flags.append("forbidden_statement")
        return _reject(decision.reason or f"{decision.statement_type} is forbidden", flags)

    # 4. WHERE floor for row-destroying statements. This is a floor, not a
    #    guarantee — `WHERE 1=1` passes it. Confirmation still applies.
    if isinstance(statement, _WHERE_REQUIRED_TYPES) and statement.args.get("where") is None:
        flags.append("missing_where")
        return _reject(
            f"{decision.statement_type} without a WHERE clause would affect every row",
            flags,
        )

    # 5. Ground every identifier in the real schema.
    index = _schema_index(schema)
    identifier_error = _check_identifiers(statement, index)
    if identifier_error is not None:
        flags.append(
            "unknown_table" if identifier_error.startswith("unknown table") else "unknown_column"
        )
        return _reject(identifier_error, flags)

    # 6. Bound the result set.
    statement, limit_action = limits.apply_limit(statement)
    if limit_action == limits.LIMIT_INJECTED:
        flags.append(f"limit_injected:{limits.default_query_limit()}")
    elif limit_action == limits.LIMIT_CLAMPED:
        flags.append(f"limit_clamped:{limits.default_query_limit()}")
    elif isinstance(statement, exp.Select):
        flags.append("limit_present")

    # 7. Attach the confirmation requirement.
    requires_confirmation = decision.disposition is Disposition.REQUIRES_CONFIRMATION
    if requires_confirmation:
        flags.append("requires_confirmation")

    return ValidationResult(
        is_safe=True,
        final_sql=statement.sql(dialect=DIALECT),
        flags=flags,
        requires_confirmation=requires_confirmation,
        rejection_reason=None,
    )
