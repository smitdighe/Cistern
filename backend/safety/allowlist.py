"""Statement-type policy.

Three tiers, decided on the parsed AST node type — never on the SQL text:

* **allowed** — SELECT, and read-only utility statements. Executes directly.
* **requires confirmation** — INSERT, UPDATE, DELETE, MERGE and friends. These
  are not rejected and are not executed; the caller must obtain an explicit
  human decision first.
* **forbidden** — DROP and TRUNCATE. There is no confirmation path to these.
  A user cannot approve them, an LLM cannot argue its way into them, and no
  config change in this module should ever move them out of this set.

Adding a statement type to ``CONFIRMATION_REQUIRED`` widens what a compromised
or hallucinating model can reach with one human click. Adding one to
``ALLOWED`` widens what it reaches with none.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlglot import exp


class Disposition(StrEnum):
    """What the policy says should happen to a statement."""

    ALLOWED = "allowed"
    REQUIRES_CONFIRMATION = "requires_confirmation"
    FORBIDDEN = "forbidden"


# Never permitted. Not overridable, not confirmable.
FORBIDDEN_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Drop,
    exp.TruncateTable,
)

# Permitted without human input.
ALLOWED_TYPES: tuple[type[exp.Expression], ...] = (exp.Select,)

# Permitted only after an explicit human decision. Never auto-executed.
CONFIRMATION_REQUIRED_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
)


@dataclass(slots=True)
class PolicyDecision:
    """The allowlist's ruling on one statement."""

    disposition: Disposition
    statement_type: str
    reason: str | None = None

    @property
    def is_forbidden(self) -> bool:
        return self.disposition is Disposition.FORBIDDEN

    @property
    def requires_confirmation(self) -> bool:
        return self.disposition is Disposition.REQUIRES_CONFIRMATION


def classify(statement: exp.Expression) -> PolicyDecision:
    """Rule on a parsed statement by its AST node type."""
    statement_type = type(statement).__name__.upper()

    # Forbidden is checked first so no later branch can override it.
    if isinstance(statement, FORBIDDEN_TYPES):
        return PolicyDecision(
            disposition=Disposition.FORBIDDEN,
            statement_type=statement_type,
            reason=(f"{statement_type} is permanently forbidden and has no confirmation path"),
        )

    if isinstance(statement, ALLOWED_TYPES):
        return PolicyDecision(Disposition.ALLOWED, statement_type)

    if isinstance(statement, CONFIRMATION_REQUIRED_TYPES):
        return PolicyDecision(
            disposition=Disposition.REQUIRES_CONFIRMATION,
            statement_type=statement_type,
            reason=f"{statement_type} modifies data and needs explicit confirmation",
        )

    # Anything unrecognised — DDL, GRANT, COPY, vendor commands, or a node type
    # a future sqlglot release introduces — defaults to confirmation rather
    # than execution. Unknown must never mean allowed.
    return PolicyDecision(
        disposition=Disposition.REQUIRES_CONFIRMATION,
        statement_type=statement_type,
        reason=f"{statement_type} is not on the allowlist and needs explicit confirmation",
    )
