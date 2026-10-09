import { create } from 'zustand'

/**
 * UI-only state for the console.
 *
 * Server responses deliberately do NOT live here — they stay in TanStack
 * Query's mutation cache. This store holds the draft question so a clarifying
 * round trip can prefill the input without the page owning two sources of
 * truth for the answer.
 */
interface QueryConsoleState {
  question: string
  setQuestion: (question: string) => void
  clearQuestion: () => void
}

export const useQueryConsoleStore = create<QueryConsoleState>((set) => ({
  question: '',
  setQuestion: (question) => set({ question }),
  clearQuestion: () => set({ question: '' }),
}))
