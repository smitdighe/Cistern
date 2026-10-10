/**
 * Postgres's own short names for the types whose SQL-standard spelling is long.
 *
 * Keyed on what the backend actually sends: `information_schema.columns
 * .data_type`, which is lowercase and carries no modifiers — never
 * `varchar(255)`, and arrays arrive as the bare word `ARRAY` — so an exact
 * lookup is enough. Every value is a name Postgres itself accepts, so the
 * short form is still a type someone could paste into a query.
 */
const SHORT_TYPE_NAMES: Readonly<Record<string, string>> = {
  'timestamp with time zone': 'timestamptz',
  'timestamp without time zone': 'timestamp',
  'time with time zone': 'timetz',
  'time without time zone': 'time',
  'character varying': 'varchar',
  character: 'char',
  'double precision': 'float8',
  'bit varying': 'varbit',
}

/**
 * The type as it fits on a diagram row. Unknown types come back unchanged
 * rather than guessed at.
 *
 * Lives beside the node rather than inside it so the component file exports
 * only a component — a module that mixes the two breaks fast refresh.
 */
export function shortColumnType(type: string): string {
  return SHORT_TYPE_NAMES[type] ?? type
}
