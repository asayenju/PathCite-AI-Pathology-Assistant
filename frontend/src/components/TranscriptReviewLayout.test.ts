import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, type PropType } from 'vue'
import TranscriptReviewLayout, { type FocusRequest } from './TranscriptReviewLayout.vue'

const rect = (top: number, height: number) =>
  ({ top, bottom: top + height, left: 0, right: 0, width: 0, height, x: 0, y: top, toJSON: () => ({}) }) as DOMRect

/** Stands in for the transcript: a turn and a mark per id, at `tops[id]` px in the viewport. */
const FakeTranscript = defineComponent({
  props: { tops: { type: Object as PropType<Record<string, number>>, required: true } },
  setup(props) {
    return () =>
      h(
        'ol',
        Object.keys(props.tops).map((id) =>
          h('li', { 'data-turn-id': `turn-${id}` }, [h('mark', { 'data-anchor-for': id }, id)]),
        ),
      )
  },
})

/** Viewport (the scroller) is 0–400 px. Positioned cards rest at parent top (500) + their data-top. */
function mountLayout(tops: Record<string, number>, cards: Record<string, number> = {}) {
  const scrollTo = vi.fn()
  Element.prototype.scrollTo = scrollTo as unknown as typeof Element.prototype.scrollTo
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    if (this.getAttribute('data-testid') === 'review-scroll') return rect(0, 400)
    if (this.getAttribute('data-testid') === 'fake-margin') return rect(500, 0)
    const id = this.getAttribute('data-anchor-for')
    if (id) return rect(tops[id]!, 20)
    const turn = this.getAttribute('data-turn-id')
    if (turn) return rect(tops[turn.replace('turn-', '')]!, 40)
    return rect(0, 0)
  })
  const wrapper = mount(TranscriptReviewLayout, {
    props: { focusRequest: null, followTurnId: null },
    slots: {
      transcript: (scope: { container: HTMLElement | null }) => [
        h(FakeTranscript, { tops }),
        h('span', { 'data-testid': 'has-container' }, String(scope.container !== null)),
      ],
      margin: () =>
        h(
          'div',
          { 'data-testid': 'fake-margin' },
          Object.entries(cards).map(([id, top]) => h('div', { 'data-card-for': id, 'data-top': top }, id)),
        ),
    },
  })
  return { wrapper, scrollTo }
}

/** Lets the layout's nextTick + two frames (setTimeout fallback in jsdom) pass. */
const settle = async () => {
  await flushPromises()
  await new Promise((resolve) => setTimeout(resolve, 60))
}

let n = 0
const request = (id: string, target: FocusRequest['target']): FocusRequest => ({ id, target, n: ++n })

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('TranscriptReviewLayout', () => {
  it('is one focusable, labelled scroll container holding both columns', async () => {
    const { wrapper } = mountLayout({ a: 10 }, { a: 0 })
    const scroll = wrapper.get('[data-testid="review-scroll"]')
    expect(scroll.attributes('tabindex')).toBe('0')
    expect(scroll.attributes('aria-label')).toBe('Transcript and review comments')
    expect(scroll.classes()).toEqual(expect.arrayContaining(['max-h-[85vh]', 'overflow-y-auto']))
    expect(scroll.find('mark').exists()).toBe(true)
    expect(scroll.find('[data-card-for="a"]').exists()).toBe(true)
    await flushPromises()
    expect(wrapper.get('[data-testid="has-container"]').text()).toBe('true')
  })

  it('clicking a highlight smoothly scrolls to where its comment card will rest', async () => {
    const { wrapper, scrollTo } = mountLayout({ a: 10 }, { a: 300 })
    await wrapper.setProps({ focusRequest: request('a', 'card') })
    await settle()
    // Card rests at 500 + 300 = 800, outside the 0–400 viewport.
    expect(scrollTo).toHaveBeenCalledTimes(1)
    expect(scrollTo).toHaveBeenCalledWith({ top: 800, behavior: 'smooth' })
  })

  it('clicking a card smoothly scrolls to its highlight', async () => {
    const { wrapper, scrollTo } = mountLayout({ a: 10, b: 700 }, { b: 0 })
    await wrapper.setProps({ focusRequest: request('b', 'highlight') })
    await settle()
    expect(scrollTo).toHaveBeenCalledWith({ top: 700, behavior: 'smooth' })
  })

  it('leaves the scroll alone when the target is already visible, and never moves focus', async () => {
    const { wrapper, scrollTo } = mountLayout({ a: 10 }, { a: 0 })
    const before = document.activeElement
    await wrapper.setProps({ focusRequest: request('a', 'highlight') })
    await settle()
    expect(scrollTo).not.toHaveBeenCalled()
    expect(document.activeElement).toBe(before)
  })

  it('a repeat request for the same id scrolls again', async () => {
    const { wrapper, scrollTo } = mountLayout({ b: 700 })
    await wrapper.setProps({ focusRequest: request('b', 'highlight') })
    await settle()
    await wrapper.setProps({ focusRequest: request('b', 'highlight') })
    await settle()
    expect(scrollTo).toHaveBeenCalledTimes(2)
  })

  it('an orphan (no highlight) falls back to its card', async () => {
    const { wrapper, scrollTo } = mountLayout({}, { orphan: 0 })
    await wrapper.setProps({ focusRequest: request('orphan', 'highlight') })
    await settle()
    expect(scrollTo).toHaveBeenCalledWith({ top: 500, behavior: 'smooth' })
  })

  it('follows the turn being reviewed, but not right after the user scrolled', async () => {
    const { wrapper, scrollTo } = mountLayout({ a: 10, b: 900, c: 1200 })
    await wrapper.setProps({ followTurnId: 'turn-b' })
    await settle()
    expect(scrollTo).toHaveBeenCalledTimes(1)
    await wrapper.get('[data-testid="review-scroll"]').trigger('wheel')
    await wrapper.setProps({ followTurnId: 'turn-c' })
    await settle()
    expect(scrollTo).toHaveBeenCalledTimes(1)
  })

  it('jumps instead of animating under prefers-reduced-motion', async () => {
    vi.stubGlobal(
      'matchMedia',
      vi.fn((query: string) => ({
        matches: query.includes('reduce'),
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      })),
    )
    const { wrapper, scrollTo } = mountLayout({ b: 700 })
    await wrapper.setProps({ focusRequest: request('b', 'highlight') })
    await settle()
    expect(scrollTo).toHaveBeenCalledWith({ top: 700, behavior: 'auto' })
  })
})
