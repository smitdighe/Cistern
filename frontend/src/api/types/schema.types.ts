/**
 * Wire types for GET /schema. Mirrors backend/schemas/schema.py.
 *
 * `tables` is an array, not a name-keyed object. The backend converts the
 * introspected mapping into a list on purpose so consumers get a stable
 * ordering (alphabetical by table name) instead of relying on object key order.
 */

export interface ColumnInfo {
  name: string
  /** Postgres data type, e.g. "integer", "text". */
  type: string
  nullable: boolean
}

export interface ForeignKeyInfo {
  column: string
  ref_table: string
  ref_column: string
}

export interface TableInfo {
  name: string
  columns: ColumnInfo[]
  primary_key: string[]
  foreign_keys: ForeignKeyInfo[]
}

export interface SchemaResponse {
  tables: TableInfo[]
  table_count: number
}
