import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import SlideFolders from './SlideFolders.vue'
import { CLASS_CODES } from '@/lib/classes'
import { SLIDE_MIME } from '@/lib/slide'
import type { Sample } from '@/types'

const samples: Sample[] = CLASS_CODES.flatMap((code, i) =>
  [0, 1, 2].map((n) => ({ code, file: `samples/${code}/${i * 10 + n}.jpg`, sourceRow: i * 10 + n })),
)

const mountFolders = (activeFile: string | null = null) =>
  mount(SlideFolders, { props: { samples, activeFile } })

const folderButton = (w: ReturnType<typeof mountFolders>, code: string) =>
  w.get(`button[aria-controls="folder-${code}"]`)

describe('SlideFolders', () => {
  it('shows one folder per class with its slide count', () => {
    const wrapper = mountFolders()
    expect(wrapper.findAll('button[aria-controls^="folder-"]')).toHaveLength(9)
    expect(folderButton(wrapper, 'TUM').text()).toContain('3')
  })

  it('opens the first folder only, and the others on click', async () => {
    const wrapper = mountFolders()
    expect(folderButton(wrapper, 'ADI').attributes('aria-expanded')).toBe('true')
    expect(folderButton(wrapper, 'TUM').attributes('aria-expanded')).toBe('false')
    expect(wrapper.findAll('img')).toHaveLength(3) // only ADI's slides are rendered

    await folderButton(wrapper, 'TUM').trigger('click')
    expect(folderButton(wrapper, 'TUM').attributes('aria-expanded')).toBe('true')
    expect(wrapper.findAll('img')).toHaveLength(6)

    await folderButton(wrapper, 'TUM').trigger('click')
    expect(wrapper.findAll('img')).toHaveLength(3)
  })

  it('emits the exact slide that was clicked', async () => {
    const wrapper = mountFolders()
    await wrapper.findAll('#folder-ADI button')[1].trigger('click')
    expect(wrapper.emitted('pick')).toEqual([[samples[1]]])
  })

  it('marks the slide on the stage as pressed', () => {
    const wrapper = mountFolders(samples[2].file)
    const pressed = wrapper.findAll('#folder-ADI button').map((b) => b.attributes('aria-pressed'))
    expect(pressed).toEqual(['false', 'false', 'true'])
  })

  it('drags a slide by its unique file path', async () => {
    const wrapper = mountFolders()
    const setData = vi.fn()
    await wrapper.findAll('#folder-ADI button')[2].trigger('dragstart', { dataTransfer: { setData } })
    expect(setData).toHaveBeenCalledWith(SLIDE_MIME, samples[2].file)
  })
})
