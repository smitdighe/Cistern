import axios from 'axios'

import { env } from '../lib/env'

/**
 * A single /query can run several LLM calls plus correction retries, so the
 * ceiling is generous — the pipeline's own timeouts should decide when a
 * request has failed, not the HTTP client cutting it off mid-flight.
 */
const REQUEST_TIMEOUT_MS = 45_000

export const apiClient = axios.create({
  baseURL: env.apiBaseUrl,
  timeout: REQUEST_TIMEOUT_MS,
  headers: {
    'Content-Type': 'application/json',
    // Placeholder: the backend has no auth today. Sending an empty header
    // would be worse than sending none, so the key is only attached when set.
    ...(env.apiKey ? { 'X-API-Key': env.apiKey } : {}),
  },
})
