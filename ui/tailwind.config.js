/**
 * Tailwind config — exposes design tokens as utility classes.
 *
 * Strategy:
 *   - Keep Tailwind's default scales available (don't `replace`, `extend`).
 *   - Add semantic color aliases (bg-surface, text-fg-default, border-default…)
 *     that resolve via CSS custom properties so dark mode flips for free.
 *   - Add a `sidebar` (sb) namespace for the dark shell chrome.
 *   - Radius / shadow / spacing aliases match `src/design/colors_and_type.css`.
 */

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class', '[data-theme="dark"]'],
  content: [
    './index.html',
    './src/**/*.{vue,js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // Semantic — backed by CSS vars (auto dark mode).
        bg: {
          app:        'var(--color-bg-app)',
          surface:    'var(--color-bg-surface)',
          'surface-alt':'var(--color-bg-surface-alt)',
          sunken:     'var(--color-bg-sunken)',
          inverse:    'var(--color-bg-inverse)',
          overlay:    'var(--color-bg-overlay)',
        },
        fg: {
          DEFAULT:  'var(--color-fg-default)',
          default:  'var(--color-fg-default)',
          strong:   'var(--color-fg-strong)',
          muted:    'var(--color-fg-muted)',
          subtle:   'var(--color-fg-subtle)',
          disabled: 'var(--color-fg-disabled)',
          inverse:  'var(--color-fg-inverse)',
          link:     'var(--color-fg-link)',
          'on-accent': 'var(--color-fg-on-accent)',
        },
        border: {
          DEFAULT:  'var(--color-border-default)',
          default:  'var(--color-border-default)',
          strong:   'var(--color-border-strong)',
          subtle:   'var(--color-border-subtle)',
          focus:    'var(--color-border-focus)',
          inverse:  'var(--color-border-inverse)',
        },
        accent: {
          DEFAULT: 'var(--color-accent-primary)',
          primary: 'var(--color-accent-primary)',
          hover:   'var(--color-accent-primary-hover)',
          active:  'var(--color-accent-primary-active)',
          subtle:  'var(--color-accent-primary-subtle)',
          fg:      'var(--color-accent-primary-fg)',
        },
        success: {
          DEFAULT: 'var(--color-success-default)',
          subtle:  'var(--color-success-subtle)',
          fg:      'var(--color-success-fg)',
          border:  'var(--color-success-border)',
        },
        warning: {
          DEFAULT: 'var(--color-warning-default)',
          subtle:  'var(--color-warning-subtle)',
          fg:      'var(--color-warning-fg)',
          border:  'var(--color-warning-border)',
        },
        danger: {
          DEFAULT: 'var(--color-danger-default)',
          subtle:  'var(--color-danger-subtle)',
          fg:      'var(--color-danger-fg)',
          border:  'var(--color-danger-border)',
        },
        info: {
          DEFAULT: 'var(--color-info-default)',
          subtle:  'var(--color-info-subtle)',
          fg:      'var(--color-info-fg)',
          border:  'var(--color-info-border)',
        },
        neutral: {
          DEFAULT: 'var(--color-neutral-default)',
          subtle:  'var(--color-neutral-subtle)',
          fg:      'var(--color-neutral-fg)',
          border:  'var(--color-neutral-border)',
        },
        // Sidebar shell — dark in both themes.
        sb: {
          bg:        'var(--sb-bg)',
          elevated:  'var(--sb-bg-elevated)',
          hover:     'var(--sb-bg-hover)',
          active:    'var(--sb-bg-active)',
          fg:        'var(--sb-fg)',
          strong:    'var(--sb-fg-strong)',
          muted:     'var(--sb-fg-muted)',
          border:    'var(--sb-border)',
          accent:    'var(--sb-accent)',
          ring:      'var(--sb-ring)',
        },
      },
      // Border-color utilities (.border-default / -subtle / -strong / -focus /
      // -inverse) MUST live here under `borderColor`, NOT under `colors.border`.
      // A color nested in a namespace literally named `border` flattens to the
      // color name `border-default`, which Tailwind then re-prefixes into the
      // unusable class `.border-border-default`. Authored markup writes
      // `.border-default`, so without this the class matches nothing and every
      // themed border silently falls back to the preflight gray-200 — which
      // happens to read fine in light mode but is a glaring near-white line in
      // dark mode. Defining `DEFAULT` also retints the preflight border reset so
      // even bare `border` / inherited borders flip with the theme.
      borderColor: {
        DEFAULT: 'var(--color-border-default)',
        default: 'var(--color-border-default)',
        strong:  'var(--color-border-strong)',
        subtle:  'var(--color-border-subtle)',
        focus:   'var(--color-border-focus)',
        inverse: 'var(--color-border-inverse)',
      },
      borderRadius: {
        xs:  '4px',
        sm:  '6px',
        md:  '8px',
        lg:  '10px',
        xl:  '14px',
      },
      boxShadow: {
        xs:    'var(--shadow-xs)',
        sm:    'var(--shadow-sm)',
        md:    'var(--shadow-md)',
        lg:    'var(--shadow-lg)',
        xl:    'var(--shadow-xl)',
        inset: 'var(--shadow-inset)',
        focus: '0 0 0 2px var(--color-bg-app), 0 0 0 4px var(--color-border-focus)',
      },
      fontFamily: {
        sans: ['"Inter Variable"', 'Inter', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto', 'sans-serif'],
        mono: ['"JetBrains Mono Variable"', '"JetBrains Mono"', '"SF Mono"', 'Menlo', 'Consolas', 'monospace'],
      },
      fontSize: {
        // Operational scale — body sits at 13px.
        '2xs': ['11px', { lineHeight: '16px' }],
        'xs':  ['12px', { lineHeight: '18px' }],
        'sm':  ['13px', { lineHeight: '20px' }],
        'base':['14px', { lineHeight: '22px' }],
        'lg':  ['16px', { lineHeight: '24px' }],
        'xl':  ['19px', { lineHeight: '28px' }],
        '2xl': ['24px', { lineHeight: '32px' }],
        '3xl': ['30px', { lineHeight: '38px' }],
      },
      letterSpacing: {
        tight: '-0.011em',
        wide:  '0.05em',
      },
      spacing: {
        // 4px grid is already Tailwind default (1=4px, 2=8px…).
        // Add named layout aliases.
        'gutter':    '24px',
        'gutter-sm': '16px',
        'row-sm':    '32px',
        'row-md':    '36px',
        'row-lg':    '44px',
        'header':    '56px',
        'sidebar':   '248px',
        'panel':     '360px',
      },
      maxWidth: {
        'content':        '1280px',
        'content-narrow': '720px',
        'content-wide':   '1480px',
      },
      zIndex: {
        sticky:   '100',
        dropdown: '1000',
        overlay:  '1100',
        modal:    '1200',
        popover:  '1300',
        tooltip:  '1400',
        toast:    '1500',
      },
      transitionDuration: {
        fast: '120ms',
        base: '160ms',
        slow: '240ms',
      },
      transitionTimingFunction: {
        standard: 'cubic-bezier(0.2, 0, 0, 1)',
        enter:    'cubic-bezier(0, 0, 0.2, 1)',
        exit:     'cubic-bezier(0.4, 0, 1, 1)',
      },
      ringColor: {
        focus: 'var(--color-border-focus)',
      },
      ringWidth: {
        focus: '2px',
      },
      outlineWidth: {
        focus: '2px',
      },
      outlineColor: {
        focus: 'var(--color-border-focus)',
      },
      outlineOffset: {
        focus: '2px',
      },
    },
  },
  plugins: [
    // Reusable focus-visible mixin — apply with `class="focus-ring"`.
    function ({ addUtilities }) {
      addUtilities({
        '.focus-ring': {
          outline: '2px solid transparent',
          outlineOffset: '2px',
          '&:focus-visible': {
            outline: '2px solid var(--color-border-focus)',
            outlineOffset: '2px',
          },
        },
        '.focus-ring-inset': {
          '&:focus-visible': {
            outline: '2px solid var(--color-border-focus)',
            outlineOffset: '-2px',
          },
        },
        // Sidebar variant — lighter ring against the dark shell.
        '.focus-ring-sb': {
          outline: '2px solid transparent',
          outlineOffset: '2px',
          '&:focus-visible': {
            outline: '2px solid var(--sb-ring)',
            outlineOffset: '2px',
          },
        },
      });
    },
  ],
};
