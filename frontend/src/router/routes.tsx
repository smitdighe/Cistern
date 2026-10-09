import type { RouteObject } from 'react-router-dom'

import { AppShell } from '../components/layout'
import { QueryConsolePage } from '../features/query-console/components'
import { SchemaBrowserPage } from '../features/schema-browser/SchemaBrowserPage'

/** Route table. */
export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    children: [
      { index: true, element: <QueryConsolePage /> },
      { path: 'schema', element: <SchemaBrowserPage /> },
    ],
  },
]
