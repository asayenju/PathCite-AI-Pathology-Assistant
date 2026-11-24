import { afterEach, describe, expect, it, vi } from 'vitest'
import { useTranscriptImport } from './useTranscriptImport'
import { transcriptMessageFor } from '@/lib/transcripts'

const json = (name = 'visit.json') => new File(['{}'], name, { type: 'application/json' })
const body = { source: 'whisperx', language: null, duration_s: null, speakers: [], turns: [], warnings: [], notice: '' }

afterEach(() => vi.unstubAllGlobals())

/** A fetch that waits until the test resolves it, and rejects with AbortError when its signal aborts. */
function deferredFetch() {
  const pending: { resolve: (r: Response) => void; signal: AbortSignal }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((resolve, reject) => {
          const signal = init.signal!
          signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
          pending.push({ resolve, signal })
        }),
    ),
  )
  return pending
}

describe('useTranscriptImport', () => {
  it('starts idle', () => {
    const t = useTranscriptImport()
    expect(t.status.value).toBe('idle')
    expect(t.transcript.value).toBeNull()
    expect(t.fileName.value).toBeNull()
  })

  it('goes idle -> loading -> review', async () => {
    const pending = deferredFetch()
    const t = useTranscriptImport()
    const done = t.readFile(json())
    expect(t.status.value).toBe('loading')
    expect(t.fileName.value).toBe('visit.json')
    pending[0]!.resolve(new Response(JSON.stringify(body)))
    await done
    expect(t.status.value).toBe('review')
    expect(t.transcript.value).toEqual(body)
  })

  it('goes idle -> loading -> error with the mapped message', async () => {
    const pending = deferredFetch()
    const t = useTranscriptImport()
    const done = t.readFile(json())
    expect(t.status.value).toBe('loading')
    pending[0]!.resolve(
      new Response(JSON.stringify({ detail: 'x', code: 'undiarized_transcript' }), { status: 422 }),
    )
    await done
    expect(t.status.value).toBe('error')
    expect(t.errorMessage.value).toBe(transcriptMessageFor('undiarized_transcript'))
    expect(t.transcript.value).toBeNull()
  })

  it('refuses a non-JSON file client-side', async () => {
    deferredFetch()
    const t = useTranscriptImport()
    await t.readFile(new File(['x'], 'scan.png', { type: 'image/png' }))
    expect(t.status.value).toBe('error')
    expect(t.errorMessage.value).toBe(transcriptMessageFor('unsupported_transcript_format'))
    expect(fetch).not.toHaveBeenCalled()
  })

  it('reset clears everything and aborts the request in flight', async () => {
    const pending = deferredFetch()
    const t = useTranscriptImport()
    const done = t.readFile(json())
    t.reset()
    await done
    expect(pending[0]!.signal.aborted).toBe(true)
    expect(t.status.value).toBe('idle')
    expect(t.transcript.value).toBeNull()
    expect(t.errorMessage.value).toBe('')
    expect(t.fileName.value).toBeNull()
  })

  it('reset after review returns to idle', async () => {
    const pending = deferredFetch()
    const t = useTranscriptImport()
    const done = t.readFile(json())
    pending[0]!.resolve(new Response(JSON.stringify(body)))
    await done
    t.reset()
    expect(t.status.value).toBe('idle')
    expect(t.transcript.value).toBeNull()
  })

  it('a new file aborts the previous request, and only the new one lands', async () => {
    const pending = deferredFetch()
    const t = useTranscriptImport()
    const first = t.readFile(json('first.json'))
    const second = t.readFile(json('second.json'))
    expect(pending[0]!.signal.aborted).toBe(true)
    await first
    expect(t.status.value).toBe('loading') // the aborted first request must not flip state to error
    pending[1]!.resolve(new Response(JSON.stringify(body)))
    await second
    expect(t.status.value).toBe('review')
    expect(t.fileName.value).toBe('second.json')
  })
})

const speaker = (id: string, suggested_role: 'doctor' | 'patient' | 'other' | null) => ({
  id,
  source_label: id.toLowerCase(),
  word_count: 10,
  talk_time_s: 30,
  preview: [],
  suggested_role,
})
const turn = (id: string, spk: string) => ({
  id,
  speaker: spk,
  start: 0,
  end: 1,
  text: 'x',
  is_final: true,
  words: [],
  min_confidence: null,
})
const twoSpeakers = {
  ...body,
  speakers: [speaker('S1', 'doctor'), speaker('S2', 'patient'), speaker('S3', null)],
  turns: [turn('t0001', 'S1'), turn('t0002', 'S2'), turn('t0003', 'S3')],
}

async function imported(payload: object = twoSpeakers) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(payload))))
  const t = useTranscriptImport()
  await t.readFile(json())
  return t
}

describe('useTranscriptImport roles', () => {
  it('seeds roles from the suggestions', async () => {
    const t = await imported()
    expect(t.roles.value).toEqual({ S1: 'doctor', S2: 'patient', S3: null })
    expect(t.canConfirm.value).toBe(true)
  })

  it('setRole changes one speaker', async () => {
    const t = await imported()
    t.setRole('S3', 'other')
    expect(t.roles.value).toEqual({ S1: 'doctor', S2: 'patient', S3: 'other' })
  })

  it('canConfirm needs at least one doctor and allows several', async () => {
    const t = await imported()
    t.setRole('S1', 'patient')
    expect(t.canConfirm.value).toBe(false)
    t.setRole('S2', 'doctor')
    expect(t.canConfirm.value).toBe(true)
    t.setRole('S1', 'doctor')
    expect(t.canConfirm.value).toBe(true)
    t.confirm()
    expect(t.status.value).toBe('confirmed')
  })

  it('confirm is blocked without a doctor', async () => {
    const t = await imported()
    t.setRole('S1', null)
    t.confirm()
    expect(t.status.value).toBe('review')
    expect(t.confirmedTranscript.value).toBeNull()
  })

  it('confirmedTranscript is null until confirmed, then carries roles', async () => {
    const t = await imported()
    expect(t.confirmedTranscript.value).toBeNull()
    t.confirm()
    expect(t.confirmedTranscript.value!.map((x) => [x.id, x.role])).toEqual([
      ['t0001', 'doctor'],
      ['t0002', 'patient'],
      ['t0003', null],
    ])
    expect(t.confirmedTranscript.value![0]!.text).toBe('x')
  })

  it('editRoles goes back to review and allows changes again', async () => {
    const t = await imported()
    t.confirm()
    t.setRole('S1', 'other') // ignored while confirmed
    expect(t.roles.value.S1).toBe('doctor')
    t.editRoles()
    expect(t.status.value).toBe('review')
    expect(t.confirmedTranscript.value).toBeNull()
    t.setRole('S3', 'doctor')
    expect(t.roles.value.S3).toBe('doctor')
  })

  it('reset clears roles', async () => {
    const t = await imported()
    t.confirm()
    t.reset()
    expect(t.roles.value).toEqual({})
    expect(t.canConfirm.value).toBe(false)
    expect(t.confirmedTranscript.value).toBeNull()
  })

  it('a new import reseeds roles', async () => {
    const t = await imported()
    t.setRole('S2', 'doctor')
    t.confirm()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ ...body, speakers: [speaker('S1', 'patient'), speaker('S2', 'doctor')] })),
      ),
    )
    await t.readFile(json('next.json'))
    expect(t.status.value).toBe('review')
    expect(t.roles.value).toEqual({ S1: 'patient', S2: 'doctor' })
  })
})
