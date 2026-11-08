/**
 * Mantine, pointed at the tokens.
 *
 * The values live in `tokens.css`. This file maps them onto Mantine's own variables
 * so a component styled with `<Button color="brand">` and one styled with
 * `className="u-card"` resolve to the same colour - otherwise the design system
 * exists twice and the two drift the first time someone changes a shade.
 *
 * Four colour roles, deliberately: brand, neutral, positive, critical. A fifth is
 * where a palette starts to rot - two blues that are nearly the same get used
 * interchangeably, and after a month nobody can say which means what.
 */

import { createTheme, type CSSVariablesResolver, type MantineColorsTuple } from '@mantine/core'

/** Indigo. Primary actions, active nav, focus rings. Nothing else. */
const brand: MantineColorsTuple = [
  '#eef2ff',
  '#e0e7ff',
  '#c7d2fe',
  '#a5b4fc',
  '#818cf8',
  '#6366f1',
  '#4f46e5',
  '#4338ca',
  '#3730a3',
  '#312e81',
]

/** Warm-neutral. A blue-grey beside indigo reads as a second, muddier accent. */
const neutral: MantineColorsTuple = [
  '#f8f9fb',
  '#f1f3f5',
  '#e9ecef',
  '#dee2e6',
  '#ced4da',
  '#9ca3af',
  '#6b7280',
  '#495057',
  '#343a40',
  '#212529',
]

/** Created, succeeded, active. */
const positive: MantineColorsTuple = [
  '#f0fdfa',
  '#ccfbf1',
  '#99f6e4',
  '#5eead4',
  '#2dd4bf',
  '#14b8a6',
  '#0d9488',
  '#0f766e',
  '#115e59',
  '#134e4a',
]

/** Failed, destructive. */
const critical: MantineColorsTuple = [
  '#fef2f2',
  '#fee2e2',
  '#fecaca',
  '#fca5a5',
  '#f87171',
  '#ef4444',
  '#dc2626',
  '#b91c1c',
  '#991b1b',
  '#7f1d1d',
]

export const theme = createTheme({
  primaryColor: 'brand',
  primaryShade: 6,
  colors: { brand, neutral, positive, critical },

  fontFamily: 'var(--font-sans)',
  fontFamilyMonospace: 'var(--font-mono)',
  fontSizes: {
    xs: 'var(--text-xs)',
    sm: 'var(--text-sm)',
    md: 'var(--text-md)',
    lg: 'var(--text-lg)',
    xl: 'var(--text-xl)',
  },
  spacing: {
    xs: 'var(--space-2)',
    sm: 'var(--space-3)',
    md: 'var(--space-4)',
    lg: 'var(--space-5)',
    xl: 'var(--space-6)',
  },
  radius: {
    xs: 'var(--radius-sm)',
    sm: 'var(--radius-sm)',
    md: 'var(--radius-md)',
    lg: 'var(--radius-lg)',
    xl: 'var(--radius-lg)',
  },
  shadows: {
    xs: 'var(--shadow-sm)',
    sm: 'var(--shadow-sm)',
    md: 'var(--shadow-md)',
    lg: 'var(--shadow-lg)',
    xl: 'var(--shadow-lg)',
  },
  headings: {
    fontWeight: 'var(--weight-strong)',
    sizes: {
      h1: { fontSize: 'var(--text-2xl)', lineHeight: 'var(--leading-tight)' },
      h2: { fontSize: 'var(--text-xl)', lineHeight: 'var(--leading-tight)' },
      h3: { fontSize: 'var(--text-lg)', lineHeight: 'var(--leading-tight)' },
    },
  },

  defaultRadius: 'md',

  components: {
    Card: { defaultProps: { withBorder: true, shadow: 'none', radius: 'md', padding: 'lg' } },
    Paper: { defaultProps: { withBorder: true, shadow: 'none', radius: 'md' } },
    Button: { defaultProps: { radius: 'md', size: 'sm' } },
    Table: {
      defaultProps: { verticalSpacing: 'sm', horizontalSpacing: 'md', highlightOnHover: true },
    },
    Modal: { defaultProps: { radius: 'lg', centered: true, overlayProps: { blur: 2 } } },
    Badge: { defaultProps: { variant: 'light', radius: 'sm' } },
    Tooltip: { defaultProps: { withArrow: true, openDelay: 400 } },
  },
})

/**
 * Mantine's own body and text variables, pointed back at the tokens.
 *
 * Without this, `<Text>` would take its colour from Mantine's defaults while
 * `.u-muted` took it from ours, and the two would be a shade apart on every screen.
 */
export const cssVariablesResolver: CSSVariablesResolver = () => ({
  variables: {
    '--mantine-color-body': 'var(--c-surface)',
    '--mantine-color-text': 'var(--c-text)',
    '--mantine-color-dimmed': 'var(--c-text-muted)',
    '--mantine-color-default-border': 'var(--c-border)',
  },
  light: {},
  dark: {},
})

/**
 * Status -> colour, named by meaning.
 *
 * `unchanged` is grey on purpose. The API distinguishes "no change" from "updated",
 * and a green success badge after a re-import that changed nothing is the interface
 * telling a comfortable lie.
 */
export const statusColor = {
  created: 'positive',
  updated: 'brand',
  unchanged: 'neutral',
  failed: 'critical',
} as const
