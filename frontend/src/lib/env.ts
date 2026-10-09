/**
 * Typed access to the `VITE_*` build-time environment.
 *
 * Vite inlines these at build time, so a missing variable is a silent
 * `undefined` rather than a crash at the call site. Validating here means a
 * misconfigured build fails immediately, with the variable name in the message,
 * instead of surfacing later as a request to `undefined/query`.
 */

function required(name: string, value: string | undefined): string {
  const trimmed = value?.trim()
  if (!trimmed) {
    throw new Error(
      `Missing required environment variable ${name}. ` +
        `Copy .env.example to .env and set it (e.g. ${name}=http://localhost:8000).`,
    )
  }
  return trimmed
}

/** Absent, empty, and any value other than "true" all read as false. */
function flag(value: string | undefined): boolean {
  return value?.trim() === 'true'
}

export const env = {
  apiBaseUrl: required('VITE_API_BASE_URL', import.meta.env.VITE_API_BASE_URL),
  /** Optional — the backend does not require a key today. */
  apiKey: import.meta.env.VITE_API_KEY?.trim() ?? '',
  features: {
    /**
     * Reserved, no consumer yet. There is nothing to gate: a history list needs
     * a backend endpoint over `query_log`, and the API exposes only /health,
     * /query and /schema. Kept because `.env.example` documents it as the
     * switch that turns the feature on when that endpoint lands — a build that
     * sets it to `true` today correctly changes nothing.
     */
    queryHistory: flag(import.meta.env.VITE_FEATURE_QUERY_HISTORY),
    /** Gates SQLPreview's editable mode. See the note there for what `true` buys today. */
    sqlPreviewSplit: flag(import.meta.env.VITE_FEATURE_SQL_PREVIEW_SPLIT),
  },
} as const

export type Env = typeof env
