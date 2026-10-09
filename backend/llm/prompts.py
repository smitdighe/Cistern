"""Prompt construction. No provider client builds prompt text inline.

Each function returns a plain string. Schema dicts are rendered into a compact
DDL-ish form rather than raw JSON — it is shorter and models follow it more
reliably than nested objects.

The correction and judge prompts deliberately describe the SQL under review as
having an unknown author. Cerebras must not be told that the SQL came from
another model, or from the same model, because that framing biases it toward
agreement and destroys its value as an independent check.
"""

from typing import Any

_SQL_STYLE_RULES = """\
Rules:
- Emit PostgreSQL syntax only.
- Produce exactly one SELECT statement. Never emit INSERT, UPDATE, DELETE,
  DROP, ALTER, CREATE, GRANT, TRUNCATE or COPY.
- Never use semicolon-separated multiple statements.
- Reference only tables and columns that appear in the schema above.
- Prefer explicit JOIN ... ON over comma joins.
- Qualify ambiguous column names with their table alias."""


def render_schema(schema: dict[str, Any]) -> str:
    """Render an introspected schema dict as compact readable DDL."""
    if not schema:
        return "(no tables)"

    lines: list[str] = []
    for table_name in sorted(schema):
        table = schema[table_name] or {}
        columns = table.get("columns") or []
        primary_key = table.get("primary_key") or []
        foreign_keys = table.get("foreign_keys") or []

        lines.append(f"TABLE {table_name} (")
        for column in columns:
            parts = [f"  {column['name']} {column['type']}"]
            if not column.get("nullable", True):
                parts.append("NOT NULL")
            if column["name"] in primary_key:
                parts.append("PRIMARY KEY")
            lines.append(" ".join(parts))
        for fk in foreign_keys:
            lines.append(
                f"  FOREIGN KEY ({fk['column']}) REFERENCES {fk['ref_table']}({fk['ref_column']})"
            )
        lines.append(")")
    return "\n".join(lines)


def generation_prompt(question: str, schema: dict[str, Any]) -> str:
    """Prompt the generation tier for SQL plus an ambiguity assessment."""
    return f"""\
You translate natural language questions into PostgreSQL queries.

Database schema:
{render_schema(schema)}

{_SQL_STYLE_RULES}

Question:
{question}

Decide whether the question can be answered unambiguously from this schema. A
question is ambiguous if it names an entity or metric that maps to more than
one column, omits a filter the schema requires to be meaningful, or relies on
a term the schema does not define. Do not guess when the mapping is unclear —
report the ambiguity instead.

Respond with a single JSON object and nothing else:
{{
  "sql": "<the SELECT statement, or an empty string if ambiguous>",
  "is_ambiguous": <true or false>,
  "ambiguity_reason": "<what is unclear, or an empty string>",
  "confidence": <number between 0.0 and 1.0>
}}"""


def correction_prompt(question: str, schema: dict[str, Any], failed_sql: str, error: str) -> str:
    """Prompt the correction tier to repair a failing query.

    The prompt does not say who or what produced ``failed_sql``.
    """
    return f"""\
A PostgreSQL query failed. Repair it.

Database schema:
{render_schema(schema)}

The query was intended to answer this question:
{question}

Query that failed:
{failed_sql}

Error reported by PostgreSQL:
{error}

{_SQL_STYLE_RULES}

Diagnose the failure against the schema and produce a corrected query that
answers the question. If the original approach is unsalvageable, write a new
query rather than patching the broken one.

Respond with a single JSON object and nothing else:
{{
  "sql": "<the corrected SELECT statement>",
  "reasoning": "<one or two sentences on what was wrong>"
}}"""


def explanation_prompt(question: str, sql: str) -> str:
    """Prompt for a plain-language description of what a query does."""
    return f"""\
Explain what this PostgreSQL query does, in plain language, for someone who
does not read SQL.

Question the query is meant to answer:
{question}

Query:
{sql}

Write two to four sentences. Describe which records are selected, how they are
filtered, grouped or ordered, and what the result represents. Do not restate
the SQL line by line, do not use SQL keywords as the backbone of the sentences,
and do not comment on whether the query is correct.

Respond with the explanation text only. No JSON, no preamble, no code fences."""


def judge_prompt(
    question: str,
    gold_sql: str,
    generated_sql: str,
    gold_result_summary: str,
    generated_result_summary: str,
) -> str:
    """Prompt the judge tier to rule on semantic equivalence.

    Neither query is attributed. The judge is told only that it is comparing a
    reference query against a candidate.
    """
    return f"""\
You are evaluating whether a candidate PostgreSQL query answers a question as
well as a reference query does.

Question:
{question}

Reference query:
{gold_sql}

Result summary from the reference query:
{gold_result_summary}

Candidate query:
{generated_sql}

Result summary from the candidate query:
{generated_result_summary}

Judge semantic equivalence, not textual similarity. The candidate is correct if
it answers the question with the same meaning as the reference. Differences in
alias names, column order, join order, formatting, or equivalent predicate
phrasing do not make it wrong. Differences in filtering, grouping, aggregation,
row scope, or the set of values returned do make it wrong.

Respond with a single JSON object and nothing else:
{{
  "verdict": <true if the candidate is correct, false otherwise>,
  "reasoning": "<one or two sentences justifying the verdict>"
}}"""
