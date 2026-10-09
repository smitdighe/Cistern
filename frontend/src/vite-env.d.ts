/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
  readonly VITE_API_KEY?: string
  readonly VITE_FEATURE_QUERY_HISTORY?: string
  readonly VITE_FEATURE_SQL_PREVIEW_SPLIT?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
