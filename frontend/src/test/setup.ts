import '@testing-library/jest-dom/vitest'

/**
 * jsdom implements no media queries, and Mantine asks for them on mount. Without
 * this every component test fails with `window.matchMedia is not a function`, which
 * reads like a bug in the component rather than a gap in the environment.
 *
 * Always "does not match": the tests assert behaviour, not which breakpoint fired.
 */
window.matchMedia ??= ((query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addListener: () => {},
  removeListener: () => {},
  addEventListener: () => {},
  removeEventListener: () => {},
  dispatchEvent: () => false,
})) as typeof window.matchMedia

/** Mantine measures elements; jsdom has no layout engine. */
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
}

/**
 * A viewport for anything that measures one.
 *
 * jsdom reports every element as 0x0, which does not mean "small" - it means no
 * layout has happened. A virtualised list asks its scroll container how tall it is
 * (via clientHeight), is told zero, concludes nothing is visible and renders no
 * rows. The component works in a browser and appears empty under test, so the test
 * that would catch a real regression cannot run at all.
 *
 * clientWidth/clientHeight specifically, because that is what the virtualiser reads
 * - getBoundingClientRect is not involved.
 */
for (const [property, value] of [
  ['clientWidth', 1200],
  ['clientHeight', 600],
] as const) {
  Object.defineProperty(Element.prototype, property, {
    configurable: true,
    get() {
      return value
    },
  })
}

/**
 * Mantine keeps notifications in a module-level store, so one test's toast is still
 * on screen during the next. That showed up as a test passing alone and failing in
 * the suite - the worst kind, because the failure points at the wrong test.
 */
import { notifications } from '@mantine/notifications'
import { afterEach } from 'vitest'

afterEach(() => {
  notifications.clean()
})
