/**
 * Wire types for GET /health. Mirrors backend/routes/health.py.
 *
 * The response is nested — each dependency reports its own status, latency and
 * detail string — rather than a flat name-to-"up"/"down" map. `unconfigured`
 * is a third state, returned when a provider API key is not set; it is not a
 * failure and must not be rendered as one.
 */

export type DependencyName = 'groq' | 'cerebras' | 'neon_execution' | 'neon_admin'

export type DependencyStatus = 'up' | 'down' | 'unconfigured'

/** "ok" only when all four checks are up; "degraded" otherwise. */
export type HealthVerdict = 'ok' | 'degraded'

export interface DependencyCheck {
  name: string
  status: DependencyStatus
  latency_ms: number
  detail: string | null
}

export interface HealthResponse {
  status: HealthVerdict
  env: string
  checks: Record<DependencyName, DependencyCheck>
}
