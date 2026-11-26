/**
 * Test helper: jsdom has no window.matchMedia. Stands one in that answers `prefers-reduced-motion: reduce` with
 * `reduce` and every other query with false. unstubMatchMedia removes it again.
 */
export function stubReducedMotion(reduce: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: reduce && query.includes('prefers-reduced-motion: reduce'),
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}

export function unstubMatchMedia() {
  delete (window as { matchMedia?: unknown }).matchMedia
}
