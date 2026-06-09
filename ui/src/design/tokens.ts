/**
 * StackOS Console Design System — TypeScript token mirror.
 *
 * Source of truth for values is `colors_and_type.css` (CSS custom properties)
 * plus `tailwind.config.js` (utility aliases). This module mirrors them for
 * documentation and any future programmatic use (charts, canvas, exports).
 * Update all three together.
 */

/* ------------------------------------------------------------------ */
/* Color                                                               */
/* ------------------------------------------------------------------ */

export const lightTheme = {
  bg: {
    app: '#f7f7f8',
    surface: '#ffffff',
    surfaceAlt: '#fafafa',
    sunken: '#f1f1f3',
    inverse: '#18181b',
    overlay: 'rgba(24, 24, 27, 0.48)',
  },
  fg: {
    default: '#27272a',
    strong: '#09090b',
    muted: '#52525b',
    subtle: '#71717a',
    disabled: '#a1a1aa',
    inverse: '#fafafa',
    link: '#4f46e5',
    onAccent: '#ffffff',
  },
  border: {
    default: '#e4e4e7',
    strong: '#d4d4d8',
    subtle: '#efeff1',
    focus: '#6366f1',
    inverse: '#3f3f46',
  },
  accent: {
    primary: '#4f46e5',
    hover: '#4338ca',
    active: '#3730a3',
    subtle: '#eef2ff',
    fg: '#4f46e5',
  },
  success: { default: '#059669', subtle: '#ecfdf5', fg: '#047857', border: '#a7f3d0' },
  warning: { default: '#d97706', subtle: '#fffbeb', fg: '#b45309', border: '#fde68a' },
  danger: { default: '#dc2626', subtle: '#fef2f2', fg: '#b91c1c', border: '#fecaca' },
  info: { default: '#0284c7', subtle: '#f0f9ff', fg: '#0369a1', border: '#bae6fd' },
  neutral: { default: '#52525b', subtle: '#f4f4f5', fg: '#3f3f46', border: '#e4e4e7' },
  sidebar: {
    bg: '#16161c',
    bgHover: 'rgba(255, 255, 255, 0.06)',
    bgActive: 'rgba(255, 255, 255, 0.1)',
    fg: '#d4d4d8',
    fgStrong: '#fafafa',
    fgMuted: '#8e8e98',
    border: 'rgba(255, 255, 255, 0.08)',
    accent: '#a5b4fc',
    ring: '#818cf8',
  },
} as const

export const darkTheme = {
  bg: {
    app: '#0d0d10',
    surface: '#16161a',
    surfaceAlt: '#1c1c21',
    sunken: '#09090b',
    inverse: '#fafafa',
    overlay: 'rgba(0, 0, 0, 0.6)',
  },
  fg: {
    default: '#d8d8dc',
    strong: '#fafafa',
    muted: '#9c9ca5',
    subtle: '#70707a',
    disabled: '#4b4b53',
    inverse: '#18181b',
    link: '#a5b4fc',
    onAccent: '#ffffff',
  },
  border: {
    default: '#26262c',
    strong: '#36363e',
    subtle: '#1e1e24',
    focus: '#818cf8',
    inverse: '#d4d4d8',
  },
  accent: {
    primary: '#6366f1',
    hover: '#4f46e5',
    active: '#4338ca',
    subtle: 'rgba(99, 102, 241, 0.16)',
    fg: '#a5b4fc',
  },
  success: { default: '#10b981', subtle: 'rgba(16, 185, 129, 0.14)', fg: '#34d399', border: 'rgba(16, 185, 129, 0.35)' },
  warning: { default: '#f59e0b', subtle: 'rgba(245, 158, 11, 0.14)', fg: '#fbbf24', border: 'rgba(245, 158, 11, 0.35)' },
  danger: { default: '#ef4444', subtle: 'rgba(239, 68, 68, 0.14)', fg: '#f87171', border: 'rgba(239, 68, 68, 0.35)' },
  info: { default: '#0ea5e9', subtle: 'rgba(14, 165, 233, 0.14)', fg: '#38bdf8', border: 'rgba(14, 165, 233, 0.35)' },
  neutral: { default: '#9c9ca5', subtle: 'rgba(148, 148, 160, 0.12)', fg: '#c2c2ca', border: '#36363e' },
  sidebar: {
    bg: '#101014',
    bgHover: 'rgba(255, 255, 255, 0.05)',
    bgActive: 'rgba(255, 255, 255, 0.09)',
    fg: '#c8c8cf',
    fgStrong: '#fafafa',
    fgMuted: '#80808a',
    border: 'rgba(255, 255, 255, 0.07)',
    accent: '#a5b4fc',
    ring: '#818cf8',
  },
} as const

/* ------------------------------------------------------------------ */
/* Foundation                                                          */
/* ------------------------------------------------------------------ */

export const spacing = {
  px: '1px',
  0.5: '2px',
  1: '4px',
  1.5: '6px',
  2: '8px',
  3: '12px',
  4: '16px',
  5: '20px',
  6: '24px',
  8: '32px',
  10: '40px',
  12: '48px',
  16: '64px',
} as const

export const radius = {
  none: '0px',
  xs: '4px',
  sm: '6px',
  md: '8px',
  lg: '10px',
  xl: '14px',
  full: '9999px',
} as const

export const typography = {
  fontSans:
    '"Inter Variable", "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
  fontMono:
    '"JetBrains Mono Variable", "JetBrains Mono", "SF Mono", Menlo, Consolas, "Liberation Mono", monospace',
  scale: {
    '2xs': { size: '11px', lineHeight: '16px' },
    xs: { size: '12px', lineHeight: '18px' },
    sm: { size: '13px', lineHeight: '20px' }, // body
    base: { size: '14px', lineHeight: '22px' },
    lg: { size: '16px', lineHeight: '24px' },
    xl: { size: '19px', lineHeight: '28px' },
    '2xl': { size: '24px', lineHeight: '32px' },
    '3xl': { size: '30px', lineHeight: '38px' },
  },
  weight: { regular: 400, medium: 500, semibold: 600, bold: 700 },
  tracking: { tight: '-0.011em', wide: '0.05em' },
} as const

export const motion = {
  duration: { fast: '120ms', base: '160ms', slow: '240ms' },
  easing: {
    standard: 'cubic-bezier(0.2, 0, 0, 1)',
    enter: 'cubic-bezier(0, 0, 0.2, 1)',
    exit: 'cubic-bezier(0.4, 0, 1, 1)',
  },
} as const

export const layout = {
  contentMax: '1280px',
  contentNarrow: '720px',
  contentWide: '1480px',
  sidebar: '248px',
  panel: '360px',
  header: '56px',
  rowSm: '32px',
  rowMd: '36px',
  rowLg: '44px',
  gutter: '24px',
  gutterSm: '16px',
} as const

export const zIndex = {
  sticky: 100,
  dropdown: 1000,
  overlay: 1100,
  modal: 1200,
  popover: 1300,
  tooltip: 1400,
  toast: 1500,
} as const
