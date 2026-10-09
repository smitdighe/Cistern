import { HelpCircle } from 'lucide-react'

import { FadeIn } from '../../../components/motion'
import { QuestionInput } from './QuestionInput'

export interface ClarifyingQuestionCardProps {
  question: string
  onSubmit: (question: string) => void
  pending: boolean
}

/**
 * Deliberately not styled like ErrorCard. Nothing went wrong here — the
 * system is asking, so this reads as a conversation turn (accent border, left
 * rule, question mark icon) rather than a failure (red, alert icon).
 */
export function ClarifyingQuestionCard({
  question,
  onSubmit,
  pending,
}: ClarifyingQuestionCardProps) {
  return (
    <FadeIn>
      {/* Accent-framed rather than error-framed: nothing went wrong here, the
          system is asking. The left rule stays a real border — it is the
          strongest signal that this is a conversational turn, and a Tailwind
          `shadow-*` utility would have replaced the glass shadow stack whole
          rather than adding to it. */}
      <div className="glass glass-accent rounded-lg border-l-2 border-l-accent bg-accent/[0.07] p-5">
        <div className="flex items-start gap-3">
          <HelpCircle className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <h2 className="text-xs font-medium uppercase tracking-wide text-accent">
              Needs clarification
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-foreground/90">{question}</p>

            <div className="mt-4">
              {/* Reuses the same input, still bound to the store, so the user's
                  original wording is there to edit rather than retype. */}
              <QuestionInput
                onSubmit={onSubmit}
                pending={pending}
                placeholder="Answer, or rephrase your question…"
              />
            </div>
          </div>
        </div>
      </div>
    </FadeIn>
  )
}
