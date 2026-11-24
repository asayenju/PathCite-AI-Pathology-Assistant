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
