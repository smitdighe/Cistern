"""Response models for GET /schema.

Mirrors the dict shape produced by ``backend.db.introspect.get_schema()``:

    {table_name: {"columns": [...], "primary_key": [...], "foreign_keys": [...]}}

The wire format turns that mapping into a list of tables carrying their own
names, so consumers get a stable ordered array instead of an object with
arbitrary keys.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ColumnInfo(BaseModel):
    """One column, as reported by information_schema."""

    name: str
    type: str = Field(description="Postgres data type, e.g. 'integer', 'text'.")
    nullable: bool


class ForeignKeyInfo(BaseModel):
    """One foreign key edge, per referencing column."""

    column: str
    ref_table: str
    ref_column: str


class TableInfo(BaseModel):
    """One base table and its structure."""

    name: str
    columns: list[ColumnInfo] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = Field(default_factory=list)


class SchemaResponse(BaseModel):
    """The introspected schema the generator is given."""

    tables: list[TableInfo] = Field(default_factory=list)
    table_count: int = 0

    @classmethod
    def from_introspection(cls, schema: dict[str, Any]) -> SchemaResponse:
        """Build the wire shape from ``introspect.get_schema()`` output."""
        tables = [
            TableInfo(
                name=table_name,
                columns=[ColumnInfo(**column) for column in (table or {}).get("columns") or []],
                primary_key=list((table or {}).get("primary_key") or []),
                foreign_keys=[
                    ForeignKeyInfo(**fk) for fk in (table or {}).get("foreign_keys") or []
                ],
            )
            for table_name, table in sorted((schema or {}).items())
        ]
        return cls(tables=tables, table_count=len(tables))
