// The one place the frontend talks to the backend. Every request is same-origin `/api/...`:
// in dev Vite proxies it to the backend (vite.config.ts), and on Render the static site rewrites
// it to the web service, so the SameSite=Lax session cookie is always sent.

import type { ErrorBody, ErrorCode } from './types';

export const API_BASE = '/api';

const GENERIC_MESSAGE = 'Something went wrong. Please try again.';

export class ApiError extends Error {
  readonly status: number;
  readonly code: ErrorCode | 'network_error';
  readonly caseId: string | undefined;

  constructor(status: number, code: ApiError['code'], message: string, caseId?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.caseId = caseId;
  }
}

// Set from POST /api/auth/login or GET /api/auth/me; sent on every POST as X-CSRF-Token.
let csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

type Query = Record<string, string | number | undefined>;

interface RequestOptions {
  json?: unknown;
  form?: FormData;
  query?: Query;
}

export function apiUrl(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== '') params.set(key, String(value));
  }
  const qs = params.toString();
  return `${API_BASE}${path}${qs ? `?${qs}` : ''}`;
}

function isErrorBody(body: unknown): body is ErrorBody {
  if (typeof body !== 'object' || body === null || !('error' in body)) return false;
  const error = (body as { error: unknown }).error;
  return typeof error === 'object' && error !== null && 'code' in error && 'message' in error;
}

async function request<T>(method: 'GET' | 'POST', path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  let body: BodyInit | undefined;
  if (options.form) {
    body = options.form; // the browser sets the multipart boundary
  } else if (options.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(options.json);
  }
  if (method === 'POST' && csrfToken) headers['X-CSRF-Token'] = csrfToken;

  let response: Response;
  try {
    response = await fetch(apiUrl(path, options.query), {
      method,
      headers,
      credentials: 'include',
      ...(body !== undefined && { body }),
    });
  } catch {
    throw new ApiError(0, 'network_error', 'Could not reach ProofCheck. Check your connection.');
  }

  if (response.status === 204) return undefined as T;

  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    if (isErrorBody(data)) {
      throw new ApiError(response.status, data.error.code, data.error.message, data.error.case_id);
    }
    throw new ApiError(response.status, 'internal_error', GENERIC_MESSAGE);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, query?: Query) => request<T>('GET', path, query ? { query } : {}),
  post: <T>(path: string, json?: unknown) => request<T>('POST', path, json === undefined ? {} : { json }),
  postForm: <T>(path: string, form: FormData) => request<T>('POST', path, { form }),
};
