// Shared test setup; browser cleanup applies only to jsdom specs.

import { afterAll, afterEach } from 'vitest'

// jsdom 25 doesn't ship a `window.matchMedia` shim — Tailwind's dark-mode
// detection isn't actually used in tests but the App.vue mount path can
// touch localStorage; no polyfill needed for that.

const originalConsoleWarn = console.warn.bind(console)

console.warn = (...args: unknown[]) => {
  const [first] = args
  if (
    typeof first === 'string'
    && first.includes('[Vue Router warn]: No match found for location')
  ) {
    return
  }
  originalConsoleWarn(...args)
}

if (typeof document !== 'undefined') {
  afterEach(() => {
    // Reset any DOM state between browser tests (Vitest unmounts the test
    // wrapper already, but if anything leaked into <head>, scrub here).
    document.head.querySelectorAll('style[data-vitest]').forEach((el) => el.remove())
  })
}

afterAll(() => {
  console.warn = originalConsoleWarn
})
