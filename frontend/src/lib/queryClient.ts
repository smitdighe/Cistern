import { QueryClient } from '@tanstack/react-query'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Health polling wants every tick to be a real request, so nothing is
      // fresh by default. Long-lived data (the schema) opts out per-hook with
      // its own staleTime rather than the default carving out an exception.
      staleTime: 0,
      // The default, "online", pauses a query the moment a fetch fails with a
      // network error and only resumes on a browser `online` event. Since
      // navigator.onLine never goes false when it is merely *our* backend that
      // died, that event never arrives: polling stops permanently and the
      // health strip freezes showing its last good state — the exact failure
      // it exists to report. "always" treats an unreachable backend as a
      // normal error, so the query keeps polling and recovers on its own.
      networkMode: 'always',
      // Refocusing the window is not a reason to re-ask the database.
      refetchOnWindowFocus: false,
      retry: 1,
    },
    mutations: {
      // A failed /query is a pipeline verdict, not a flaky request. Retrying
      // silently would burn LLM calls and hide the failure from the user.
      retry: 0,
      // Same reasoning as above: a paused mutation would leave the console
      // stuck on its loading state with no error and no way out.
      networkMode: 'always',
    },
  },
})
