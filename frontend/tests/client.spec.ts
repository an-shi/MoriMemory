import { computed } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { MemoryApiClient, MemoryApiError, resolveErrorMessage } from '../src/api/client'
import { normalizeLocale, translate, useMessages } from '../src/i18n'

describe('i18n', () => {
  it('translates nested keys with interpolation', () => {
    expect(translate('zh-CN', 'table.showing', { start: 1, end: 20, total: 36 })).toBe(
      '第 1-20 条，共 36 条',
    )
    expect(translate('en-US', 'table.showing', { start: 1, end: 20, total: 36 })).toBe(
      '1-20 of 36',
    )
  })

  it('falls back to Chinese then the key', () => {
    expect(translate('en-US', 'definitely.missing')).toBe('definitely.missing')
    expect(translate('zh-CN', 'type.episodic')).toBe('情景记忆')
    expect(translate('en-US', 'type.episodic')).toBe('Episodic')
  })

  it('normalizes locale and supports getter input', () => {
    expect(normalizeLocale('en-GB')).toBe('en-US')
    expect(normalizeLocale('zh-TW')).toBe('zh-CN')
    const locale = computed(() => 'en-US' as const)
    const { t } = useMessages(locale)
    expect(t('type.long_term')).toBe('Long-term')
  })
})

describe('MemoryApiClient', () => {
  it('builds repeated query parameters and sends the bearer token', async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            status: 'ok',
            message: '',
            data: { items: [], total: 0, page: 2, per_page: 20 },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
    )
    const client = new MemoryApiClient({
      baseUrl: '/api/v1/memory/',
      token: 'test-token',
      fetch: fetchMock as unknown as typeof fetch,
    })

    const result = await client.listMemories({
      page: 2,
      memory_type: ['long_term', 'episodic'],
      status: ['active'],
    })

    expect(result.page).toBe(2)
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    const parsed = new URL(url, 'http://localhost')
    expect(parsed.pathname).toBe('/api/v1/memory/memories')
    expect(parsed.searchParams.getAll('memory_type')).toEqual(['long_term', 'episodic'])
    expect(parsed.searchParams.getAll('status')).toEqual(['active'])
    expect(parsed.searchParams.get('page')).toBe('2')
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer test-token')
  })

  it('maps the error envelope to MemoryApiError', async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            status: 'error',
            message: 'memory not found',
            code: 'not_found',
            data: { code: 'not_found' },
          }),
          { status: 404, headers: { 'Content-Type': 'application/json' } },
        ),
    )
    const client = new MemoryApiClient({
      token: 'test-token',
      fetch: fetchMock as unknown as typeof fetch,
    })

    await expect(client.getMemory('missing')).rejects.toMatchObject({
      name: 'MemoryApiError',
      status: 404,
      code: 'not_found',
      message: 'memory not found',
    } satisfies Partial<MemoryApiError>)
    try {
      await client.getMemory('missing')
      throw new Error('expected a failure')
    } catch (error) {
      expect(resolveErrorMessage(error)).toBe('memory not found')
    }
  })

  it('supports async token providers and custom headers', async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ status: 'ok', data: { status: 'ok' } }), { status: 200 }),
    )
    const client = new MemoryApiClient({
      token: async () => 'async-token',
      headers: () => ({ 'X-Mori-User-Id': 'local' }),
      fetch: fetchMock as unknown as typeof fetch,
    })
    await client.health()
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    const headers = init.headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer async-token')
    expect(headers['X-Mori-User-Id']).toBe('local')
  })
})
