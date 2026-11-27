import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import CheckSources from './CheckSources.vue'
import type { CheckSource } from '@/types'

let nextId = 1
function source(over: Partial<CheckSource> = {}): CheckSource {
  return {
    chunk_id: nextId++,
    title: 'Colorectal cancer (NG151)',
    section: 'Recommendations > Staging',
    url: 'https://www.nice.org.uk/guidance/ng151',
    trust: 'high',
    year: 2021,
    quote: 'Grade does not determine stage.',
    stance: 'supports',
    luna_stance: 'supports',
    ...over,
  }
}

const mountSources = (sources: CheckSource[]) => mount(CheckSources, { props: { sources } })
const group = (w: ReturnType<typeof mountSources>, stance: string) => w.find(`[data-testid="sources-${stance}"]`)

describe('CheckSources', () => {
  it('groups sources as Supports, Contradicts, Unclear in that order', () => {
    const w = mountSources([
      source({ stance: 'unclear', luna_stance: 'supports' }),
      source({ stance: 'contradicts', title: 'B' }),
      source({ stance: 'supports', title: 'A' }),
      source({ stance: 'supports', title: 'C' }),
    ])
    const headings = w.findAll('[data-testid^="sources-"] h4').map((h) => h.text())
    expect(headings).toEqual(['Supports (2)', 'Contradicts (1)', 'Unclear (1)'])
    expect(group(w, 'supports').findAll('[data-testid="check-source"]')).toHaveLength(2)
  })

  it('omits empty groups', () => {
    const w = mountSources([source({ stance: 'contradicts' })])
    expect(group(w, 'supports').exists()).toBe(false)
    expect(group(w, 'unclear').exists()).toBe(false)
    expect(group(w, 'contradicts').exists()).toBe(true)
  })

  it('shows title, section path, trust, year, the quote and a safe external link', () => {
    const w = mountSources([source({ trust: 'medium', year: 2019 })])
    const s = w.get('[data-testid="check-source"]')
    expect(s.get('[data-testid="source-title"]').text()).toBe('Colorectal cancer (NG151)')
    expect(s.get('[data-testid="source-section"]').text()).toBe('Recommendations > Staging')
    expect(s.get('[data-testid="source-trust"]').text()).toBe('Medium trust')
    expect(s.get('[data-testid="source-year"]').text()).toBe('2019')
    expect(s.get('blockquote').text()).toBe('“Grade does not determine stage.”')
    const link = s.get('a')
    expect(link.attributes('href')).toBe('https://www.nice.org.uk/guidance/ng151')
    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toBe('noopener noreferrer')
    expect(link.text()).toContain('Open source')
  })

  it.each([
    ['high', 'High trust'],
    ['medium', 'Medium trust'],
    ['low', 'Low trust'],
  ] as const)('labels %s trust', (trust, label) => {
    expect(mountSources([source({ trust })]).get('[data-testid="source-trust"]').text()).toBe(label)
  })

  it('uses the section as the title when there is none, and hides a missing year', () => {
    const w = mountSources([source({ title: null, year: null })])
    expect(w.get('[data-testid="source-title"]').text()).toBe('Recommendations > Staging')
    expect(w.find('[data-testid="source-section"]').exists()).toBe(false)
    expect(w.find('[data-testid="source-year"]').exists()).toBe(false)
  })

  it('never links a non-http(s) url', () => {
    const w = mountSources([source({ url: 'javascript:alert(1)' })])
    expect(w.find('a').exists()).toBe(false)
  })

  it("says an unclear source's quote was not found in the passage", () => {
    const w = mountSources([
      source({ stance: 'unclear', luna_stance: 'supports' }),
      source({ stance: 'unclear', luna_stance: 'contradicts' }),
    ])
    const opinions = w.findAll('[data-testid="source-opinions"]').map((o) => o.text())
    expect(opinions).toEqual(['Quote not found in the passage', 'Quote not found in the passage'])
    expect(w.text()).not.toMatch(/Luna:|Jev:/)
  })

  it('shows no model opinions on supporting or contradicting sources', () => {
    const w = mountSources([source({ stance: 'supports' }), source({ stance: 'contradicts' })])
    expect(w.find('[data-testid="source-opinions"]').exists()).toBe(false)
  })

  it('collapses irrelevant sources under "Not relevant (n)"', () => {
    const w = mountSources([source({ stance: 'supports' }), source({ stance: 'irrelevant' }), source({ stance: 'irrelevant' })])
    const irrelevant = w.get('[data-testid="sources-irrelevant"]')
    expect(irrelevant.element.tagName).toBe('DETAILS')
    expect((irrelevant.element as HTMLDetailsElement).open).toBe(false)
    expect(irrelevant.get('summary').text()).toBe('Not relevant (2)')
    expect(irrelevant.findAll('[data-testid="check-source"]')).toHaveLength(2)
  })

  it('says so when there are no sources at all', () => {
    const w = mountSources([])
    expect(w.get('[data-testid="sources-empty"]').text()).toBe('No passages were found in the corpus.')
  })

  it('says so when every source is irrelevant', () => {
    const w = mountSources([source({ stance: 'irrelevant' })])
    expect(w.get('[data-testid="sources-empty"]').text()).toBe('None of the passages found are about this claim.')
  })
})
