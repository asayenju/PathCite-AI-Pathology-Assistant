import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { defineComponent, h } from 'vue'
import SpeakerRoles from './SpeakerRoles.vue'
import { useTranscriptImport } from '@/composables/useTranscriptImport'
import type { Role, TranscriptSpeaker } from '@/types'

const speakers: TranscriptSpeaker[] = [
  {
    id: 'S1',
    source_label: 'spk_0',
    word_count: 140,
    talk_time_s: 95,
    preview: ['Your biopsy results are back.', 'Let me explain what we found.'],
    suggested_role: null,
  },
  { id: 'S2', source_label: 'spk_1', word_count: 30, talk_time_s: 20, preview: ['Okay.'], suggested_role: 'patient' },
]

function mountRoles(initial: Record<string, Role | null>) {
  // Drive the component from the real composable state so emits round-trip.
  const t = useTranscriptImport()
  t.roles.value = { ...initial }
  t.status.value = 'review'
  const Host = defineComponent(() => () =>
    h(SpeakerRoles, {
      speakers,
      roles: t.roles.value,
      onSetRole: t.setRole,
    }),
  )
  return { wrapper: mount(Host), t }
}

describe('SpeakerRoles', () => {
  it('renders a row per speaker with label, talk time, word count and quoted preview', () => {
    const { wrapper } = mountRoles({ S1: null, S2: 'patient' })
    const row = wrapper.get('[data-testid="speaker-S1"]').text()
    expect(row).toContain('S1')
    expect(row).toContain('spk_0')
    expect(row).toContain('1:35')
    expect(row).toContain('140 words')
    expect(row).toContain('“Your biopsy results are back.”')
    expect(wrapper.get('[data-testid="speaker-S2"]').text()).toContain('0:20')
  })

  it('labels each select and pre-fills it', () => {
    const { wrapper } = mountRoles({ S1: null, S2: 'patient' })
    const label = wrapper.findAll('label').find((l) => l.text() === 'Role for S1')!
    const select = wrapper.get(`#${label.attributes('for')}`).element as HTMLSelectElement
    expect(select.value).toBe('')
    expect((wrapper.get('#role-S2').element as HTMLSelectElement).value).toBe('patient')
    expect([...select.options].map((o) => o.text)).toEqual(['Doctor', 'Patient', 'Other', 'Unassigned'])
  })

  it('shows the suggested badge only while the value equals the suggestion', async () => {
    const { wrapper } = mountRoles({ S1: null, S2: 'patient' })
    expect(wrapper.get('[data-testid="speaker-S2"]').find('[data-testid="suggested-badge"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="speaker-S1"]').find('[data-testid="suggested-badge"]').exists()).toBe(false)
    await wrapper.get('#role-S2').setValue('other')
    expect(wrapper.get('[data-testid="speaker-S2"]').find('[data-testid="suggested-badge"]').exists()).toBe(false)
  })

  it('has no confirm step: roles change directly and stay editable', async () => {
    const { wrapper, t } = mountRoles({ S1: null, S2: 'patient' })
    expect(wrapper.findAll('button').some((b) => /confirm|edit roles/i.test(b.text()))).toBe(false)
    await wrapper.get('#role-S1').setValue('doctor')
    expect(t.roles.value.S1).toBe('doctor')
    expect((wrapper.get('#role-S1').element as HTMLSelectElement).value).toBe('doctor')
    expect(wrapper.get('#role-S1').attributes('disabled')).toBeUndefined()
  })

  it('snaps back when the parent refuses the change', async () => {
    const wrapper = mount(SpeakerRoles, { props: { speakers, roles: { S1: 'doctor', S2: 'patient' } } })
    await wrapper.get('#role-S1').setValue('patient')
    expect(wrapper.emitted('setRole')).toEqual([['S1', 'patient']])
    expect((wrapper.get('#role-S1').element as HTMLSelectElement).value).toBe('doctor')
  })
})
