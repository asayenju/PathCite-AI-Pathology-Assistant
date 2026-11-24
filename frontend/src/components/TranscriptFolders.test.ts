import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import TranscriptFolders from './TranscriptFolders.vue'
import { TRANSCRIPT_MIME } from '@/lib/transcriptSamples'
import type { TranscriptManifest } from '@/types'

const manifest: TranscriptManifest = {
  notice: 'Synthetic, AI-written sample conversations. Not real patients.',
  folders: [
    {
      name: 'GI',
      slug: 'gi',
      items: [
        { id: 'crc', title: 'Colorectal results', vendor: 'deepgram', file: 'transcripts/gi/crc.json' },
        { id: 'polyp', title: 'Benign polyp', vendor: 'aws_medical', file: 'transcripts/gi/polyp.json' },
      ],
    },
    {
      name: 'Breast',
      slug: 'breast',
      items: [{ id: 'idc', title: 'Ductal carcinoma', vendor: 'openai_diarized', file: 'transcripts/breast/idc.json' }],
    },
  ],
}
const crc = manifest.folders[0]!.items[0]!
const polyp = manifest.folders[0]!.items[1]!

const mountFolders = (activeFile: string | null = null) =>
  mount(TranscriptFolders, { props: { manifest, activeFile } })

const folderButton = (w: ReturnType<typeof mountFolders>, slug: string) =>
  w.get(`button[aria-controls="tfolder-${slug}"]`)
const items = (w: ReturnType<typeof mountFolders>, slug = 'gi') => w.findAll(`#tfolder-${slug} button`)

describe('TranscriptFolders', () => {
  it('shows each folder with its count, and the synthetic notice', () => {
    const wrapper = mountFolders()
    expect(wrapper.findAll('button[aria-controls^="tfolder-"]')).toHaveLength(2)
    expect(folderButton(wrapper, 'gi').text()).toContain('GI')
    expect(folderButton(wrapper, 'gi').text()).toContain('2')
    expect(wrapper.get('[data-testid="samples-notice"]').text()).toBe(manifest.notice)
  })

  it('opens the first folder only, and the others on click', async () => {
    const wrapper = mountFolders()
    expect(folderButton(wrapper, 'gi').attributes('aria-expanded')).toBe('true')
    expect(folderButton(wrapper, 'breast').attributes('aria-expanded')).toBe('false')
    expect(items(wrapper)).toHaveLength(2)
    expect(items(wrapper, 'breast')).toHaveLength(0)

    await folderButton(wrapper, 'breast').trigger('click')
    expect(items(wrapper, 'breast')).toHaveLength(1)
    await folderButton(wrapper, 'breast').trigger('click')
    expect(items(wrapper, 'breast')).toHaveLength(0)
  })

  it('shows each sample title with a vendor badge and a synthetic label', async () => {
    const wrapper = mountFolders()
    await folderButton(wrapper, 'breast').trigger('click')
    const [first, second] = items(wrapper)
    expect(first!.text()).toContain('Colorectal results')
    expect(first!.get('[data-testid="vendor-badge"]').text()).toBe('Deepgram')
    expect(first!.text()).toContain('Synthetic sample')
    expect(second!.get('[data-testid="vendor-badge"]').text()).toBe('AWS Medical')
    expect(items(wrapper, 'breast')[0]!.get('[data-testid="vendor-badge"]').text()).toBe('OpenAI')
    expect(first!.attributes('aria-label')).toContain('Synthetic sample')
  })

  it('emits the sample that was clicked', async () => {
    const wrapper = mountFolders()
    await items(wrapper)[1]!.trigger('click')
    expect(wrapper.emitted('pick')).toEqual([[polyp]])
  })

  it('selects with Enter and Space, once each', async () => {
    const wrapper = mountFolders()
    await items(wrapper)[0]!.trigger('keydown', { key: 'Enter' })
    await items(wrapper)[1]!.trigger('keydown', { key: ' ' })
    expect(wrapper.emitted('pick')).toEqual([[crc], [polyp]])
  })

  it('marks the loaded sample as pressed', () => {
    const wrapper = mountFolders(polyp.file)
    expect(items(wrapper).map((b) => b.attributes('aria-pressed'))).toEqual(['false', 'true'])
  })

  it('drags a sample with the custom type and its file path', async () => {
    const wrapper = mountFolders()
    const setData = vi.fn()
    expect(items(wrapper)[0]!.attributes('draggable')).toBe('true')
    await items(wrapper)[0]!.trigger('dragstart', { dataTransfer: { setData } })
    expect(setData).toHaveBeenCalledWith(TRANSCRIPT_MIME, crc.file)
    expect(TRANSCRIPT_MIME).toBe('application/x-pathcite-transcript')
  })
})
