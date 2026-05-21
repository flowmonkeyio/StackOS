/**
 * Design Tokens — Operations Console Design System
 *
 * Semantic tokens are the contract. Raw color/space values are the implementation.
 * Components MUST consume semantic tokens (e.g. `color.fg.default`) — never raw
 * scale values. Theme switching (light/dark) flips the semantic layer only.
 *
 * The same tokens are also exposed as CSS custom properties via
 * `colors_and_type.css` for use from Tailwind utility classes and plain CSS.
 */

// ---------------------------------------------------------------------------
// Raw scales (private — do not import directly from app code)
// ---------------------------------------------------------------------------

export const palette = {
  // Neutral — slate-tuned. Backbone of the UI.
  slate: {
    0:   '#ffffff',
    25:  '#fbfcfd',
    50:  '#f6f8fa',
    100: '#eef1f4',
    200: '#dde2e8',
    300: '#c4ccd5',
    400: '#9aa5b1',
    500: '#6b7785',
    600: '#4a5563',
    700: '#374151',
    800: '#1f2937',
    900: '#111826',
    950: '#0a0e17',
    1000:'#05080d',
  },
  // Primary — restrained blue.
  blue: {
    50:  '#eff6ff',
    100: '#dbeafe',
    200: '#bfdbfe',
    300: '#93c5fd',
    400: '#60a5fa',
    500: '#3b82f6',
    600: '#2563eb',
    700: '#1d4ed8',
    800: '#1e40af',
    900: '#1e3a8a',
  },
  // Success.
  emerald: {
    50:  '#ecfdf5',
    100: '#d1fae5',
    200: '#a7f3d0',
    300: '#6ee7b7',
    400: '#34d399',
    500: '#10b981',
    600: '#059669',
    700: '#047857',
    800: '#065f46',
    900: '#064e3b',
  },
  // Warning / attention.
  amber: {
    50:  '#fffbeb',
    100: '#fef3c7',
    200: '#fde68a',
    300: '#fcd34d',
    400: '#fbbf24',
    500: '#f59e0b',
    600: '#d97706',
    700: '#b45309',
    800: '#92400e',
    900: '#78350f',
  },
  // Destructive / error.
  red: {
    50:  '#fef2f2',
    100: '#fee2e2',
    200: '#fecaca',
    300: '#fca5a5',
    400: '#f87171',
    500: '#ef4444',
    600: '#dc2626',
    700: '#b91c1c',
    800: '#991b1b',
    900: '#7f1d1d',
  },
  violet: {
    50:  '#f5f3ff',
    100: '#ede9fe',
    200: '#ddd6fe',
    300: '#c4b5fd',
    400: '#a78bfa',
    500: '#8b5cf6',
    600: '#7c3aed',
    700: '#6d28d9',
    800: '#5b21b6',
    900: '#4c1d95',
  },
} as const;

// ---------------------------------------------------------------------------
// Semantic colors — light theme
// ---------------------------------------------------------------------------

export const lightTheme = {
  bg: {
    app:        palette.slate[50],   // page background
    surface:    palette.slate[0],    // cards, panels, dialogs
    surfaceAlt: palette.slate[25],   // alt rows, hover, subtle fill
    sunken:     palette.slate[100],  // input fill, code block
    inverse:    palette.slate[900],
    overlay:    'rgba(15, 23, 42, 0.45)',
  },
  fg: {
    default:    palette.slate[900],
    strong:     palette.slate[1000],
    muted:      palette.slate[600],
    subtle:     palette.slate[500],
    disabled:   palette.slate[400],
    inverse:    palette.slate[0],
    link:       palette.blue[700],
    onAccent:   palette.slate[0],
  },
  border: {
    default:    palette.slate[200],
    strong:     palette.slate[300],
    subtle:     palette.slate[100],
    focus:      palette.blue[500],
    inverse:    palette.slate[700],
  },
  accent: {
    primary:        palette.blue[600],
    primaryHover:   palette.blue[700],
    primaryActive:  palette.blue[800],
    primarySubtle:  palette.blue[50],
    primaryFg:      palette.blue[700],
  },
  success: { default: palette.emerald[600], subtle: palette.emerald[50],  fg: palette.emerald[700], border: palette.emerald[200] },
  warning: { default: palette.amber[500],   subtle: palette.amber[50],    fg: palette.amber[700],   border: palette.amber[200]   },
  danger:  { default: palette.red[600],     subtle: palette.red[50],      fg: palette.red[700],     border: palette.red[200]     },
  info:    { default: palette.blue[600],    subtle: palette.blue[50],     fg: palette.blue[700],    border: palette.blue[200]    },
  neutral: { default: palette.slate[600],   subtle: palette.slate[100],   fg: palette.slate[700],   border: palette.slate[200]   },
} as const;

// ---------------------------------------------------------------------------
// Semantic colors — dark theme (first-class)
// ---------------------------------------------------------------------------

export const darkTheme = {
  bg: {
    app:        palette.slate[950],
    surface:    palette.slate[900],
    surfaceAlt: '#16202f',
    sunken:     palette.slate[1000],
    inverse:    palette.slate[50],
    overlay:    'rgba(0, 0, 0, 0.65)',
  },
  fg: {
    default:    palette.slate[100],
    strong:     palette.slate[0],
    muted:      palette.slate[400],
    subtle:     palette.slate[500],
    disabled:   palette.slate[600],
    inverse:    palette.slate[900],
    link:       palette.blue[300],
    onAccent:   palette.slate[0],
  },
  border: {
    default:    palette.slate[800],
    strong:     palette.slate[700],
    subtle:     '#1a2333',
    focus:      palette.blue[400],
    inverse:    palette.slate[200],
  },
  accent: {
    primary:        palette.blue[500],
    primaryHover:   palette.blue[400],
    primaryActive:  palette.blue[300],
    primarySubtle:  'rgba(59, 130, 246, 0.15)',
    primaryFg:      palette.blue[300],
  },
  success: { default: palette.emerald[400], subtle: 'rgba(16,185,129,0.12)', fg: palette.emerald[300], border: 'rgba(16,185,129,0.30)' },
  warning: { default: palette.amber[400],   subtle: 'rgba(245,158,11,0.12)', fg: palette.amber[300],   border: 'rgba(245,158,11,0.30)' },
  danger:  { default: palette.red[400],     subtle: 'rgba(239,68,68,0.12)',  fg: palette.red[300],     border: 'rgba(239,68,68,0.30)'  },
  info:    { default: palette.blue[400],    subtle: 'rgba(59,130,246,0.12)', fg: palette.blue[300],    border: 'rgba(59,130,246,0.30)' },
  neutral: { default: palette.slate[400],   subtle: 'rgba(148,163,184,0.10)',fg: palette.slate[300],   border: palette.slate[700]     },
} as const;

// ---------------------------------------------------------------------------
// Spacing — 4px grid
// ---------------------------------------------------------------------------

export const spacing = {
  0:    '0px',
  px:   '1px',
  0.5:  '2px',
  1:    '4px',
  1.5:  '6px',
  2:    '8px',
  2.5:  '10px',
  3:    '12px',
  3.5:  '14px',
  4:    '16px',
  5:    '20px',
  6:    '24px',
  7:    '28px',
  8:    '32px',
  10:   '40px',
  12:   '48px',
  16:   '64px',
  20:   '80px',
  24:   '96px',
} as const;

// ---------------------------------------------------------------------------
// Radius — capped at 8px for normal surfaces.
// ---------------------------------------------------------------------------

export const radius = {
  none: '0px',
  xs:   '2px',  // hairline pills, very small chips
  sm:   '4px',  // inputs, buttons, badges
  md:   '6px',  // cards, panels (default)
  lg:   '8px',  // dialogs, prominent surfaces (max for normal use)
  xl:   '12px', // exceptional cases only — flag in review
  full: '9999px',
} as const;

// ---------------------------------------------------------------------------
// Typography
// ---------------------------------------------------------------------------

export const fontFamily = {
  sans: '"Inter var", "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
  mono: '"JetBrains Mono", "Fira Code", "SF Mono", Menlo, Consolas, "Liberation Mono", monospace',
} as const;

export const fontWeight = {
  regular:  400,
  medium:   500,
  semibold: 600,
  bold:     700,
} as const;

/** font-size / line-height pairs. Operational density — body sits at 13px. */
export const typography = {
  // Display — sparing, used for empty states and dashboard-level headlines.
  displayLg: { size: '32px', line: '40px', weight: 600, tracking: '0' },
  displayMd: { size: '24px', line: '32px', weight: 600, tracking: '0' },

  // Headings
  h1:        { size: '20px', line: '28px', weight: 600, tracking: '0' },
  h2:        { size: '16px', line: '24px', weight: 600, tracking: '0' },
  h3:        { size: '14px', line: '20px', weight: 600, tracking: '0' },

  // Body
  bodyLg:    { size: '14px', line: '20px', weight: 400, tracking: '0' },
  body:      { size: '13px', line: '18px', weight: 400, tracking: '0' },
  bodySm:    { size: '12px', line: '16px', weight: 400, tracking: '0' },

  // Utility
  label:     { size: '12px', line: '16px', weight: 500, tracking: '0' },
  caption:   { size: '11px', line: '14px', weight: 400, tracking: '0' },
  overline:  { size: '11px', line: '14px', weight: 600, tracking: '0' /* uppercase */ },

  // Mono — for code, IDs, hashes, JSON.
  code:      { size: '12px', line: '18px', weight: 400, tracking: '0' },
  codeLg:    { size: '13px', line: '20px', weight: 400, tracking: '0' },
} as const;

// ---------------------------------------------------------------------------
// Elevation — prefer borders + subtle shadows over heavy drop shadows.
// ---------------------------------------------------------------------------

export const shadow = {
  none:    'none',
  // Hairline shadow for raised rows (e.g. sticky table header).
  xs:      '0 1px 0 0 rgba(15, 23, 42, 0.04)',
  // Card / panel resting shadow.
  sm:      '0 1px 2px 0 rgba(15, 23, 42, 0.04), 0 1px 3px 0 rgba(15, 23, 42, 0.06)',
  // Popovers, dropdowns.
  md:      '0 4px 8px -2px rgba(15, 23, 42, 0.08), 0 2px 4px -2px rgba(15, 23, 42, 0.06)',
  // Dialogs.
  lg:      '0 12px 24px -6px rgba(15, 23, 42, 0.12), 0 4px 8px -4px rgba(15, 23, 42, 0.08)',
  // Toasts / floating.
  xl:      '0 20px 40px -12px rgba(15, 23, 42, 0.18), 0 8px 16px -8px rgba(15, 23, 42, 0.10)',
  // Inset — for sunken inputs / wells.
  inset:   'inset 0 1px 2px 0 rgba(15, 23, 42, 0.06)',
} as const;

// Dark-theme shadows are softer (rely on borders for separation).
export const shadowDark = {
  none:  'none',
  xs:    '0 1px 0 0 rgba(0, 0, 0, 0.3)',
  sm:    '0 1px 2px 0 rgba(0, 0, 0, 0.4)',
  md:    '0 4px 8px -2px rgba(0, 0, 0, 0.5)',
  lg:    '0 12px 24px -6px rgba(0, 0, 0, 0.6)',
  xl:    '0 20px 40px -12px rgba(0, 0, 0, 0.7)',
  inset: 'inset 0 1px 2px 0 rgba(0, 0, 0, 0.4)',
} as const;

// ---------------------------------------------------------------------------
// Z-index — discrete layers, no ad-hoc values.
// ---------------------------------------------------------------------------

export const zIndex = {
  base:        0,
  raised:      10,
  sticky:      100,    // sticky table headers, action bars
  dropdown:    1000,
  overlay:     1100,   // modal scrim
  modal:       1200,
  popover:     1300,
  tooltip:     1400,
  toast:       1500,   // above everything
} as const;

// ---------------------------------------------------------------------------
// Focus ring — explicit, visible, never removed.
// ---------------------------------------------------------------------------

export const focusRing = {
  width:  '2px',
  offset: '2px',
  color:  palette.blue[500],
  colorDark: palette.blue[400],
  // CSS shorthand to drop on any focusable surface:
  //   outline: var(--focus-outline);
  //   outline-offset: var(--focus-offset);
  outline:    `2px solid ${palette.blue[500]}`,
  outlineDark:`2px solid ${palette.blue[400]}`,
  // Inset variant for items inside compact lists / table rows.
  inset:      `inset 0 0 0 2px ${palette.blue[500]}`,
} as const;

// ---------------------------------------------------------------------------
// Layout — page widths, container caps.
// ---------------------------------------------------------------------------

export const layout = {
  contentMax:    '1280px', // primary content area cap
  contentNarrow: '720px',  // forms, settings, single-column reads
  contentWide:   '1536px', // tables, dashboards
  sidebarWidth:  '240px',
  rightPanelWidth: '360px',
  pageGutter:    '24px',
  pageGutterSm:  '16px',
  rowHeightSm:   '32px',
  rowHeightMd:   '40px',
  rowHeightLg:   '48px',
  headerHeight:  '52px',
} as const;

// ---------------------------------------------------------------------------
// Motion — restrained, functional.
// ---------------------------------------------------------------------------

export const motion = {
  duration: {
    instant: '0ms',
    fast:    '120ms',
    base:    '180ms',
    slow:    '260ms',
  },
  easing: {
    standard: 'cubic-bezier(0.2, 0, 0, 1)',     // most UI
    enter:    'cubic-bezier(0, 0, 0.2, 1)',     // things appearing
    exit:     'cubic-bezier(0.4, 0, 1, 1)',     // things leaving
  },
} as const;

// ---------------------------------------------------------------------------
// Breakpoints
// ---------------------------------------------------------------------------

export const breakpoint = {
  sm:  '640px',
  md:  '768px',
  lg:  '1024px',
  xl:  '1280px',
  '2xl': '1536px',
} as const;

// ---------------------------------------------------------------------------
// Density — apps run in either "compact" (default for tables/lists)
// or "comfortable". Components read this from a `density` prop.
// ---------------------------------------------------------------------------

export type Density = 'compact' | 'comfortable';

export const density = {
  compact: {
    rowHeight:  '32px',
    paddingX:   '12px',
    paddingY:   '6px',
    fontSize:   '13px',
    gap:        '8px',
  },
  comfortable: {
    rowHeight:  '40px',
    paddingX:   '16px',
    paddingY:   '10px',
    fontSize:   '14px',
    gap:        '12px',
  },
} as const;

// ---------------------------------------------------------------------------
// Type exports
// ---------------------------------------------------------------------------

export type Palette = typeof palette;
export type Theme = typeof lightTheme;
export type Spacing = keyof typeof spacing;
export type Radius = keyof typeof radius;
export type FontWeight = keyof typeof fontWeight;
export type TypeScale = keyof typeof typography;
export type Shadow = keyof typeof shadow;
export type ZIndex = keyof typeof zIndex;

export const tokens = {
  palette,
  lightTheme,
  darkTheme,
  spacing,
  radius,
  fontFamily,
  fontWeight,
  typography,
  shadow,
  shadowDark,
  zIndex,
  focusRing,
  layout,
  motion,
  breakpoint,
  density,
} as const;

export default tokens;
