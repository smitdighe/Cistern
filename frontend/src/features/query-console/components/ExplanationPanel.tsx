import { Card } from '../../../components/ui'
import { cn } from '../../../lib/cn'

export interface ExplanationPanelProps {
  explanation: string
  className?: string
}

/**
 * Sits above the SQL by design: the user should read what the system believed
 * they asked for before reading the query or trusting the rows.
 *
 * No entrance animation of its own. The whole answer column fades in as one
 * unit from QueryConsolePage — three siblings each animating separately read as
 * a second sequence competing with the pipeline trace, which is the only thing
 * on the page that should look sequenced. No typewriter either: the text is
 * already written when it arrives, and animating it character by character
 * would fake a stream that does not exist.
 */
export function ExplanationPanel({ explanation, className }: ExplanationPanelProps) {
  if (!explanation) return null

  return (
    <Card className={cn(className)}>
      <h2 className="text-xs font-medium uppercase tracking-wide text-foreground/70">
        What this does
      </h2>
      <p className="mt-2 text-sm leading-relaxed text-foreground/85">{explanation}</p>
    </Card>
  )
}
