import { apiClient } from '../client'
import type { HealthResponse } from '../types/health.types'

/** Per-dependency reachability. Always 200 — read `status` and `checks`, not the HTTP code. */
export async function getHealth(): Promise<HealthResponse> {
  const { data } = await apiClient.get<HealthResponse>('/health')
  return data
}
