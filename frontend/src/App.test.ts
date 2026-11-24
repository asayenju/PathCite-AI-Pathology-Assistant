import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.vue'

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ samples: [] }))))
})
afterEach(() => vi.unstubAllGlobals())

const mountApp = async () => {
  const wrapper = mount(App, { attachTo: document.body })
  await flushPromises()
  return wrapper
}

const tab = (w: Awaited<ReturnType<typeof mountApp>>, name: string) =>
  w.findAll('[role=tab]').find((t) => t.text() === name)!

describe('App view switch', () => {
  it('keeps the disclaimer in the header', async () => {
    const wrapper = await mountApp()
    expect(wrapper.get('header').text()).toContain('Research model. Not a diagnosis.')
    wrapper.unmount()
  })

  it('shows the slide view by default', async () => {
    const wrapper = await mountApp()
    expect(tab(wrapper, 'Slides').attributes('aria-selected')).toBe('true')
    expect(tab(wrapper, 'Transcripts').attributes('aria-selected')).toBe('false')
    expect(wrapper.get('#panel-slides').isVisible()).toBe(true)
    expect(wrapper.get('#panel-transcripts').isVisible()).toBe(false)
    expect(wrapper.get('[aria-label^="Stage."]').isVisible()).toBe(true)
    wrapper.unmount()
  })

  it('switches to the transcript drop zone and back', async () => {
    const wrapper = await mountApp()
    await tab(wrapper, 'Transcripts').trigger('click')
    expect(tab(wrapper, 'Transcripts').attributes('aria-selected')).toBe('true')
    expect(wrapper.get('#panel-slides').isVisible()).toBe(false)
    expect(wrapper.get('[aria-label^="Drop zone."]').isVisible()).toBe(true)

    await tab(wrapper, 'Slides').trigger('click')
    expect(wrapper.get('#panel-slides').isVisible()).toBe(true)
    wrapper.unmount()
  })

  it('moves between tabs with the arrow keys', async () => {
    const wrapper = await mountApp()
    await tab(wrapper, 'Slides').trigger('keydown', { key: 'ArrowRight' })
    expect(tab(wrapper, 'Transcripts').attributes('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(tab(wrapper, 'Transcripts').element)
    await tab(wrapper, 'Transcripts').trigger('keydown', { key: 'ArrowRight' })
    expect(tab(wrapper, 'Slides').attributes('aria-selected')).toBe('true')
    wrapper.unmount()
  })
})

describe('App sample transcripts', () => {
  const manifest = {
    notice: 'Synthetic, AI-written sample conversations.',
    folders: [{ name: 'GI', slug: 'gi', items: [{ id: 'a', title: 'A visit', vendor: 'whisperx', file: 'transcripts/gi/a.json' }] }],
  }

  it('shows the sample folders beside the drop zone when the manifest loads', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        new Response(JSON.stringify(url === '/transcripts/index.json' ? manifest : { samples: [] })),
      ),
    )
    const wrapper = await mountApp()
    await tab(wrapper, 'Transcripts').trigger('click')
    expect(wrapper.get('#panel-transcripts [data-testid="transcript-folders"]').text()).toContain('A visit')
    expect(wrapper.get('[aria-label^="Drop zone."]').isVisible()).toBe(true)
    expect(wrapper.get('header').text()).toContain('Research model. Not a diagnosis.')
    wrapper.unmount()
  })

  it('hides the sample folders when the manifest fails, and the slides still load', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url === '/transcripts/index.json'
          ? Promise.reject(new TypeError('offline'))
          : new Response(JSON.stringify({ samples: [] })),
      ),
    )
    const wrapper = await mountApp()
    await tab(wrapper, 'Transcripts').trigger('click')
    expect(wrapper.find('[data-testid="transcript-folders"]').exists()).toBe(false)
    expect(wrapper.get('[aria-label^="Drop zone."]').isVisible()).toBe(true)
    wrapper.unmount()
  })
})

