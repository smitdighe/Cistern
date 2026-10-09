import { apiClient } from '../client'
import type { SchemaResponse } from '../types/schema.types'

/** Fetch the introspected schema. Responds 503 if the admin connection is down. */
export async function getSchema(): Promise<SchemaResponse> {
  const { data } = await apiClient.get<SchemaResponse>('/schema')
  return data
}
