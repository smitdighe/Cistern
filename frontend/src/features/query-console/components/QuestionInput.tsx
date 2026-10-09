import { type FormEvent } from 'react'
import { CornerDownLeft } from 'lucide-react'

import { Button } from '../../../components/ui'
import { cn } from '../../../lib/cn'
import { useQueryConsoleStore } from '../store/queryConsoleStore'

export interface QuestionInputProps {
  onSubmit: (question: string) => void
  pending: boolean
  autoFocus?: boolean
  placeholder?: string
}

export function QuestionInput({
  onSubmit,
  pending,
  autoFocus,
  placeholder = 'Ask a question about the data…',
}: QuestionInputProps) {
  const question = useQueryConsoleStore((state) => state.question)
  const setQuestion = useQueryConsoleStore((state) => state.setQuestion)

  const trimmed = question.trim()
  const canSubmit = trimmed.length > 0 && !pending

  // A plain form, so Enter submits natively and the button gets keyboard and
  // screen-reader behaviour for free.
  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canSubmit) return
    onSubmit(trimmed)
  }

  return (
    <form onSubmit={handleSubmit} className="flex items-center gap-2">
      <label htmlFor="question" className="sr-only">
        Question
      </label>
      <input
        id="question"
        type="text"
        value={question}
        autoFocus={autoFocus}
        disabled={pending}
        placeholder={placeholder}
        onChange={(event) => setQuestion(event.target.value)}
        className={cn(
          'min-w-0 flex-1 rounded-md border border-border bg-surface/60 px-3.5 py-2',
          'text-sm text-foreground backdrop-blur placeholder:text-foreground/60',
          'transition-[border-color,background-color,box-shadow] duration-150',
          'hover:border-border/80 hover:bg-surface/70',
          'outline-none focus-visible:border-accent/60 focus-visible:ring-2',
          'focus-visible:ring-accent/50 focus-visible:ring-offset-2',
          'focus-visible:ring-offset-background',
          'disabled:opacity-60',
        )}
      />
      <Button type="submit" disabled={!canSubmit}>
        Ask
        <CornerDownLeft className="size-3.5" aria-hidden="true" />
      </Button>
    </form>
  )
}
