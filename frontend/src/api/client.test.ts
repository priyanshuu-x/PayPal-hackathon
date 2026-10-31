import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, apiUrl, setCsrfToken } from './client';
import { login, logout, submitRefund } from './endpoints';

const fetchMock = vi.fn<typeof fetch>();

function respond(status: number, body?: unknown) {
  fetchMock.mockResolvedValueOnce(
    new Response(body === undefined ? null : typeof body === 'string' ? body : JSON.stringify(body), { status }),
  );
}

function lastInit(): RequestInit {
  const call = fetchMock.mock.calls.at(-1);
  if (!call?.[1]) throw new Error('fetch was not called with init');
  return call[1];
}

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  setCsrfToken(null);
});

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe('apiUrl', () => {
  it('prefixes /api and drops empty query values', () => {
    expect(apiUrl('/cases', { status: 'ready', verdict: undefined, cursor: '', limit: 50 })).toBe(
      '/api/cases?status=ready&limit=50',
    );
  });
});

describe('request', () => {
  it('sends cookies with every request', async () => {
    respond(200, { status: 'ok' });
    await api.get('/health');
    expect(lastInit().credentials).toBe('include');
  });

  it('sends the CSRF token on POST once logged in, and not on GET', async () => {
    respond(200, { merchant_id: 'm1', csrf_token: 'tok' });
    await login('merchant@example.test', 'pw');

    respond(200, {});
    await api.post('/cases/c1/approve', { action: 'approve', version: 1 });
    expect((lastInit().headers as Record<string, string>)['X-CSRF-Token']).toBe('tok');

    respond(200, {});
    await api.get('/cases');
    expect((lastInit().headers as Record<string, string>)['X-CSRF-Token']).toBeUndefined();
  });

  it('forgets the CSRF token on logout', async () => {
    setCsrfToken('tok');
    respond(204);
    await logout();
    respond(200, {});
    await api.post('/auth/login', {});
    expect((lastInit().headers as Record<string, string>)['X-CSRF-Token']).toBeUndefined();
  });

  it('turns the contract error format into an ApiError', async () => {
    respond(409, { error: { code: 'conflict', message: 'This case changed. Reload it.', case_id: 'c1' } });
    await expect(api.post('/cases/c1/approve', {})).rejects.toMatchObject({
      status: 409,
      code: 'conflict',
      message: 'This case changed. Reload it.',
      caseId: 'c1',
    });
  });

  it('never surfaces a non-contract error body', async () => {
    respond(502, '<html>upstream stack trace</html>');
    const error = await api.get('/cases').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).not.toContain('stack trace');
    expect((error as ApiError).code).toBe('internal_error');
  });

  it('reports network failures as network_error', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await expect(api.get('/health')).rejects.toMatchObject({ status: 0, code: 'network_error' });
  });
});

describe('submitRefund', () => {
  it('posts multipart form data without forcing a Content-Type', async () => {
    respond(202, { case_id: 'c1' });
    const file = new File(['x'], 'receipt.png', { type: 'image/png' });
    await expect(submitRefund({ orderId: 'O1', reason: 'Damaged', file })).resolves.toEqual({ case_id: 'c1' });

    const init = lastInit();
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get('order_id')).toBe('O1');
    expect((init.headers as Record<string, string>)['Content-Type']).toBeUndefined();
  });
});
