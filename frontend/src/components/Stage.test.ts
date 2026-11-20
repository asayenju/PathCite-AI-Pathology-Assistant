import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import Stage from './Stage.vue'
import { SLIDE_MIME } from '@/lib/slide'

const png = () => new File([new Uint8Array([1])], 'a.png', { type: 'image/png' })

function dropOf(payload: { slide?: string; file?: File }) {
  return {
    dataTransfer: {
      types: payload.slide ? [SLIDE_MIME] : ['Files'],
      getData: (type: string) => (type === SLIDE_MIME ? (payload.slide ?? '') : ''),
      files: payload.file ? [payload.file] : [],
    },
  }
}

const mountStage = (previewUrl: string | null) =>
  mount(Stage, { props: { status: previewUrl ? 'result' : 'idle', previewUrl, scaleKnown: false } })

const stageEl = (w: ReturnType<typeof mountStage>) => w.get('[aria-label^="Stage."]')

describe('Stage with nothing on it', () => {
  it('is a button that accepts a dropped file', async () => {
    const wrapper = mountStage(null)
    expect(stageEl(wrapper).element.tagName).toBe('BUTTON')
    await stageEl(wrapper).trigger('drop', dropOf({ file: png() }))
    expect(wrapper.emitted('file')).toHaveLength(1)
  })

  it('accepts a dropped slide', async () => {
    const wrapper = mountStage(null)
    await stageEl(wrapper).trigger('drop', dropOf({ slide: 'TUM' }))
    expect(wrapper.emitted('slide')).toEqual([['TUM']])
  })
})

describe('Stage with an image in place', () => {
  it('is not a button, so it cannot open the file picker', async () => {
    const wrapper = mountStage('blob:x')
    expect(stageEl(wrapper).element.tagName).toBe('DIV')
    expect(wrapper.find('button[aria-label^="Stage."]').exists()).toBe(false)

    const input = wrapper.get('input[type=file]').element as HTMLInputElement
    let opened = false
    input.click = () => {
      opened = true
    }
    await stageEl(wrapper).trigger('click')
    expect(opened).toBe(false)
  })

  it('refuses a dropped file and says to clear the stage first', async () => {
    const wrapper = mountStage('blob:x')
    await stageEl(wrapper).trigger('drop', dropOf({ file: png() }))
    expect(wrapper.emitted('file')).toBeUndefined()
    expect(wrapper.get('[role=status]').text()).toBe('Clear the stage before adding another image.')
  })

  it('still lets a slide from the rack replace the image, and drops the notice', async () => {
    const wrapper = mountStage('blob:x')
    await stageEl(wrapper).trigger('drop', dropOf({ file: png() }))
    await stageEl(wrapper).trigger('drop', dropOf({ slide: 'STR' }))
    expect(wrapper.emitted('slide')).toEqual([['STR']])
    expect(wrapper.find('[role=status]').exists()).toBe(false)
  })

  it('clearing the stage emits clear and removes the notice', async () => {
    const wrapper = mountStage('blob:x')
    await stageEl(wrapper).trigger('drop', dropOf({ file: png() }))
    await wrapper.findAll('button').find((b) => b.text() === 'Clear stage')!.trigger('click')
    expect(wrapper.emitted('clear')).toHaveLength(1)
    expect(wrapper.find('[role=status]').exists()).toBe(false)
  })
})
