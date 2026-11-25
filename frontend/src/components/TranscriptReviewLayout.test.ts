import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, type PropType } from 'vue'
import TranscriptReviewLayout from './TranscriptReviewLayout.vue'

const rect = (top: number, height: number) =>
  ({ top, bottom: top + height, left: 0, right: 0, width: 0, height, x: 0, y: top, toJSON: () => ({}) }) as DOMRect

/** Stands in for the transcript: a mark per id, at `tops[id]` px in the viewport. */
const FakeTranscript = defineComponent({
  props: { tops: { type: Object as PropType<Record<string, number>>, required: true } },
  setup(props) {
    return () =>
      h(
        'p',
        Object.keys(props.tops).map((id) => h('mark', { 'data-anchor-for': id, 'aria-describedby': `comment-${id}` }, id)),
      )
  },
})

function mountLayout(tops: Record<string, number>, cards: string[] = []) {
  const scrollIntoView = vi.fn()
  Element.prototype.scrollIntoView = scrollIntoView
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    if (this.getAttribute('data-testid') === 'review-scroll') return rect(0, 400)
    const id = this.getAttribute('data-anchor-for')
    if (id) return rect(tops[id], 20)
    if (this.id.startsWith('comment-')) return rect(900, 50)
    return rect(0, 0)
  })
  const wrapper = mount(TranscriptReviewLayout, {
    props: { activeCommentId: null },
    slots: {
      transcript: (scope: { container: HTMLElement | null }) => [
        h(FakeTranscript, { tops }),
        h('span', { 'data-testid': 'has-container' }, String(scope.container !== null)),
      ],
      margin: () => cards.map((id) => h('article', { id: `comment-${id}` }, id)),
    },
  })
  return { wrapper, scrollIntoView }
}

const originalScrollIntoView = Element.prototype.scrollIntoView

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  Element.prototype.scrollIntoView = originalScrollIntoView
})

describe('TranscriptReviewLayout', () => {
  it('is one focusable, labelled scroll container holding both columns', async () => {
    const { wrapper } = mountLayout({ a: 10 }, ['a'])
    const scroll = wrapper.get('[data-testid="review-scroll"]')
    expect(scroll.attributes('tabindex')).toBe('0')
    expect(scroll.attributes('aria-label')).toBe('Transcript and review comments')
    expect(scroll.classes()).toEqual(expect.arrayContaining(['max-h-[70vh]', 'overflow-y-auto']))
    expect(scroll.find('mark').exists()).toBe(true)
    expect(scroll.find('#comment-a').exists()).toBe(true)
    await flushPromises()
    expect(wrapper.get('[data-testid="has-container"]').text()).toBe('true')
  })

  it('scrolls the active highlight into view when it is off screen', async () => {
    const { wrapper, scrollIntoView } = mountLayout({ a: 10, b: 700 })
    await wrapper.setProps({ activeCommentId: 'b' })
    await flushPromises()
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
    expect(scrollIntoView.mock.contexts[0]).toBe(wrapper.get('[data-anchor-for="b"]').element)
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'center', behavior: 'smooth' })
  })

  it('leaves the scroll alone when the highlight is already visible, and never moves focus', async () => {
    const { wrapper, scrollIntoView } = mountLayout({ a: 10, b: 700 })
    const before = document.activeElement
    await wrapper.setProps({ activeCommentId: 'a' })
    await flushPromises()
    expect(scrollIntoView).not.toHaveBeenCalled()
    expect(document.activeElement).toBe(before)
  })

  it('scrolls an orphan card into view (it has no highlight)', async () => {
    const { wrapper, scrollIntoView } = mountLayout({ a: 10 }, ['orphan'])
    await wrapper.setProps({ activeCommentId: 'orphan' })
    await flushPromises()
    expect(scrollIntoView.mock.contexts[0]).toBe(wrapper.get('#comment-orphan').element)
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest', behavior: 'smooth' })
  })

  it('does nothing when the active comment is cleared', async () => {
    const { wrapper, scrollIntoView } = mountLayout({ b: 700 })
    await wrapper.setProps({ activeCommentId: 'b' })
    await wrapper.setProps({ activeCommentId: null })
    await flushPromises()
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
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
    const { wrapper, scrollIntoView } = mountLayout({ b: 700 })
    await wrapper.setProps({ activeCommentId: 'b' })
    await flushPromises()
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'center', behavior: 'auto' })
  })
})
