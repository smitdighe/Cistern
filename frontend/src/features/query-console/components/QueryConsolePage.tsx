import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'

import { FadeIn } from '../../../components/motion'
import { Skeleton } from '../../../components/ui'
import { PipelineTrace } from '../../pipeline-trace/components'
import { useQueryResponseParser } from '../hooks/useQueryResponseParser'
import { useSubmitQuery } from '../hooks/useSubmitQuery'
import { ClarifyingQuestionCard } from './ClarifyingQuestionCard'
import { ErrorCard } from './ErrorCard'
import { ExplanationPanel } from './ExplanationPanel'
import { QuestionInput } from './QuestionInput'
import { ResultTable } from './ResultTable'
import { SQLPreview } from './SQLPreview'

/**
 * Cosmetic only. /query is one request with no streaming, so the backend
 * cannot tell us which stage it is in. These labels describe the pipeline's
 * shape to fill dead air — they are never claimed to be live progress, and the
 * cycle is time-based, not event-based. Real per-attempt state belongs to the
 * post-response replay in PipelineTrace, which has actual data behind it.
 */
const PHASE_LABELS = [
  'Generating SQL…',
  'Validating safety…',
  'Executing…',
  'Checking result…',
] as const

const PHASE_INTERVAL_MS = 1600

function PendingState() {
  const [index, setIndex] = useState(0)

  useEffect(() => {
    const id = setInterval(
      () => setIndex((current) => (current + 1) % PHASE_LABELS.length),
      PHASE_INTERVAL_MS,
    )
    return () => clearInterval(id)
  }, [])

  return (
    <div className="glass rounded-lg p-5" aria-busy="true">
      <div className="flex items-center gap-3">
        <span
          aria-hidden="true"
          className="size-2 animate-pulse rounded-full bg-accent motion-reduce:animate-none"
        />
        {/* Polite, not assertive: these labels are cosmetic filler, so they
            must not interrupt anything the user is already hearing. */}
        <span className="text-sm text-foreground/70" aria-live="polite">
          {PHASE_LABELS[index]}
        </span>
      </div>
      <div className="mt-4 space-y-2">
        <Skeleton className="h-3 w-2/3" />
        <Skeleton className="h-3 w-1/2" />
        <Skeleton className="h-3 w-3/4" />
      </div>
    </div>
  )
}

export function QueryConsolePage() {
  const submit = useSubmitQuery()
  const classified = useQueryResponseParser(submit.data)

  function handleSubmit(question: string) {
    submit.mutate({ question })
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-foreground sm:text-xl">
          Query console
        </h1>
        <p className="mt-1 text-sm text-foreground/70">
          Ask in plain English. Cistern writes the SQL, checks it, and runs it read-only.
        </p>
      </div>

      {/* Hidden while a clarifying question owns the input, so there are never
          two question boxes on screen competing for the same store value. */}
      {classified?.type !== 'clarifying' && (
        <QuestionInput onSubmit={handleSubmit} pending={submit.isPending} autoFocus />
      )}

      {submit.isPending && <PendingState />}

      {/* Transport failure — distinct from a pipeline verdict, which arrives as
          a normal 200 and is handled by the classifier below. */}
      {submit.isError && !submit.isPending && (
        <FadeIn>
          <div className="glass glass-error flex items-start gap-3 rounded-lg bg-error/[0.07] p-5">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-error" aria-hidden="true" />
            <div>
              <h2 className="text-xs font-medium uppercase tracking-wide text-error">
                Could not reach the backend
              </h2>
              <p className="mt-2 font-mono text-xs text-foreground/60">{submit.error.message}</p>
            </div>
          </div>
        </FadeIn>
      )}

      {/* Two columns on wide screens so the trace is visible without pushing
          the answer below the fold; stacks trace-first on narrow ones. */}
      {!submit.isPending && classified?.type === 'success' && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem] lg:items-start">
          {/* min-w-0: a grid child defaults to min-width:auto, so a wide result
              table would stretch this column and push the whole page into
              horizontal scroll instead of scrolling inside the table. */}
          {/* One fade for the whole answer, not one per panel. The trace beside
              it is the only element allowed to look sequenced. */}
          <FadeIn className="order-2 min-w-0 space-y-4 lg:order-1">
            <ExplanationPanel explanation={classified.explanation} />
            <SQLPreview value={classified.sql} />
            <ResultTable rows={classified.result} />
          </FadeIn>
          {/* top-20 clears the sticky header; see the note in SchemaBrowserPage. */}
          <PipelineTrace classified={classified} className="order-1 lg:sticky lg:top-20 lg:order-2" />
        </div>
      )}

      {!submit.isPending && classified?.type === 'clarifying' && (
        <ClarifyingQuestionCard
          question={classified.question}
          onSubmit={handleSubmit}
          pending={submit.isPending}
        />
      )}

      {!submit.isPending &&
        (classified?.type === 'safety_blocked' || classified?.type === 'correction_exhausted') && (
          <div className="space-y-4">
            <ErrorCard variant={classified} />
            <PipelineTrace classified={classified} />
          </div>
        )}
    </div>
  )
}
