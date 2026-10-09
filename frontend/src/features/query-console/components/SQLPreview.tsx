import { useMemo } from 'react'
import CodeMirror from '@uiw/react-codemirror'
import { sql, PostgreSQL } from '@codemirror/lang-sql'
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { EditorView } from '@codemirror/view'
import { tags } from '@lezer/highlight'

import { env } from '../../../lib/env'

/**
 * CodeMirror themes compile to real stylesheet rules, so the app's CSS custom
 * properties resolve normally here. That keeps the editor on the same tokens
 * as everything else instead of duplicating hex values that drift.
 */
const editorTheme = EditorView.theme(
  {
    '&': {
      backgroundColor: 'transparent',
      color: 'hsl(var(--foreground))',
      fontSize: '13px',
    },
    '.cm-content': { padding: '12px 0', fontFamily: 'ui-monospace, SFMono-Regular, monospace' },
    '.cm-gutters': {
      backgroundColor: 'transparent',
      color: 'hsl(var(--foreground) / 0.3)',
      border: 'none',
    },
    '.cm-activeLine': { backgroundColor: 'hsl(var(--foreground) / 0.04)' },
    '.cm-activeLineGutter': { backgroundColor: 'transparent' },
    '&.cm-focused': { outline: 'none' },
    '.cm-selectionBackground, ::selection': { backgroundColor: 'hsl(var(--accent) / 0.25)' },
    '.cm-cursor': { borderLeftColor: 'hsl(var(--accent))' },
  },
  { dark: true },
)

const highlightStyle = HighlightStyle.define([
  { tag: tags.keyword, color: 'hsl(var(--accent))' },
  { tag: [tags.string, tags.special(tags.string)], color: 'hsl(var(--success))' },
  { tag: tags.number, color: 'hsl(var(--warning))' },
  { tag: [tags.comment, tags.lineComment, tags.blockComment], color: 'hsl(var(--foreground) / 0.4)' },
  { tag: [tags.function(tags.variableName), tags.standard(tags.variableName)], color: 'hsl(var(--foreground))' },
  { tag: tags.operator, color: 'hsl(var(--foreground) / 0.7)' },
])

export interface SQLPreviewProps {
  value: string
  className?: string
}

export function SQLPreview({ value, className }: SQLPreviewProps) {
  // When the split lands, the editor becomes editable; until then it is a
  // viewer. Read-only is derived from the flag rather than hardcoded so
  // flipping the env var is the only change needed.
  const editable = env.features.sqlPreviewSplit
  // Stated in both modes. Flipping the flag on used to *hide* the caveat, which
  // is exactly backwards: an editor that takes keystrokes it cannot run is the
  // state that most needs explaining. /query generates and executes in one
  // call, so there is no way to submit an edited statement.
  const note = editable
    ? 'Editable scratchpad — edits stay local until the backend splits generate and execute'
    : 'Preview only — live editing available once backend supports two-call split'

  const extensions = useMemo(
    () => [
      sql({ dialect: PostgreSQL }),
      syntaxHighlighting(highlightStyle),
      editorTheme,
      // CodeMirror puts role="textbox" on .cm-content, which needs its own
      // accessible name — a label on any ancestor does not satisfy it. This is
      // the only way to reach that element, since React never renders it.
      EditorView.contentAttributes.of({
        'aria-label': editable ? 'Generated SQL, editable' : 'Generated SQL, read only',
        'aria-readonly': editable ? 'false' : 'true',
      }),
    ],
    [editable],
  )

  return (
    <div className={className}>
      <div className="glass overflow-hidden rounded-lg">
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-border/70 bg-foreground/[0.04] px-4 py-2">
          <span className="text-xs font-medium uppercase tracking-wide text-foreground/70">SQL</span>
          <span className="text-xs text-foreground/60">{note}</span>
        </div>
        {/* CodeMirror keeps .cm-content focusable at tabindex 0 even when
            read-only, so the query is reachable by keyboard and Ctrl/Cmd+A then
            copy works. The label is what a screen reader announces on entry. */}
        <div className="px-4">
          <CodeMirror
            value={value}
            // `editable` and `readOnly` are different things and the difference
            // matters here. `editable={false}` sets contenteditable="false",
            // which drops the editor out of the tab order entirely — a keyboard
            // user then cannot focus the SQL, select it, or copy it. Staying
            // editable keeps it focusable and selectable; `readOnly` is what
            // actually rejects input.
            editable
            readOnly={!editable}
            // @uiw/react-codemirror injects its own light theme by default,
            // which paints .cm-editor white and beats the token colours below.
            // "none" leaves the styling entirely to `editorTheme`.
            theme="none"
            extensions={extensions}
            basicSetup={{
              lineNumbers: false,
              foldGutter: false,
              highlightActiveLine: editable,
              highlightActiveLineGutter: false,
              autocompletion: false,
            }}
          />
        </div>
      </div>
    </div>
  )
}
