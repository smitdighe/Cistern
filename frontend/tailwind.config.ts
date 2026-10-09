import type { Config } from 'tailwindcss'

/**
 * Dark-first: the token values in `:root` (see src/index.css) are the dark
 * palette. `darkMode: 'class'` is kept so a future `.light` opt-out can be
 * driven from the same config without a rewrite.
 *
 * The palette is deliberately small — one accent hue plus three status colors.
 * Anything needing a lighter or dimmer shade uses an opacity modifier
 * (`text-foreground/60`) rather than a new token.
 */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        background: 'hsl(var(--background) / <alpha-value>)',
        surface: 'hsl(var(--surface) / <alpha-value>)',
        border: 'hsl(var(--border) / <alpha-value>)',
        foreground: 'hsl(var(--foreground) / <alpha-value>)',
        accent: 'hsl(var(--accent) / <alpha-value>)',
        success: 'hsl(var(--success) / <alpha-value>)',
        warning: 'hsl(var(--warning) / <alpha-value>)',
        error: 'hsl(var(--error) / <alpha-value>)',
      },
    },
  },
  plugins: [],
} satisfies Config
