// One function per endpoint in docs/api-contract.md. Pages import from here, never call fetch.

import { api, apiUrl, setCsrfToken } from './client';
import type {
  BuyerOrder,
  CapturedOrder,
  CaseDecision,
  CaseDetail,
  CaseFilters,
  CasePage,
  CopilotAnswer,
  CopilotTurn,
  CreateOrderRequest,
  InsightsSummary,
  Product,
  SdkConfig,
  Session,
} from './types';

const id = (value: string) => encodeURIComponent(value);

export const health = () => api.get<{ status: string }>('/health');

// --- Auth (merchant) --------------------------------------------------------------

export async function login(email: string, password: string): Promise<Session> {
  const session = await api.post<Session>('/auth/login', { email, password });
  setCsrfToken(session.csrf_token);
  return session;
}

export async function logout(): Promise<void> {
  await api.post<undefined>('/auth/logout');
  setCsrfToken(null);
}

export async function me(): Promise<Session> {
  const session = await api.get<Session>('/auth/me');
  setCsrfToken(session.csrf_token);
  return session;
}

// --- Demo store (buyer) -----------------------------------------------------------

export const listProducts = () => api.get<Product[]>('/store/products');
export const getSdkConfig = () => api.get<SdkConfig>('/store/sdk-config');
export const createOrder = (order: CreateOrderRequest) => api.post<{ order_id: string }>('/store/orders', order);
export const captureOrder = (orderId: string) => api.post<CapturedOrder>(`/store/orders/${id(orderId)}/capture`);
export const listMyOrders = () => api.get<BuyerOrder[]>('/store/orders');

// --- Refund requests (buyer) ------------------------------------------------------

export function submitRefund(input: { orderId: string; reason: string; file: File; email?: string }) {
  const form = new FormData();
  form.set('order_id', input.orderId);
  form.set('reason', input.reason);
  if (input.email) form.set('email', input.email);
  form.set('file', input.file);
  return api.postForm<{ case_id: string }>('/refunds', form);
}

// --- Cases (merchant) -------------------------------------------------------------

export const listCases = (filters: CaseFilters = {}) => api.get<CasePage>('/cases', { ...filters });
export const getCase = (caseId: string) => api.get<CaseDetail>(`/cases/${id(caseId)}`);
/** For an <img src>: the authenticated, re-encoded evidence preview. */
export const evidenceUrl = (caseId: string) => apiUrl(`/cases/${id(caseId)}/evidence`);
/** The only call that moves money (approve) or closes a case (decline). */
export const decideCase = (caseId: string, decision: CaseDecision) =>
  api.post<CaseDetail>(`/cases/${id(caseId)}/approve`, decision);

// --- Case Copilot (merchant, read-only) -------------------------------------------

export const askCopilot = (caseId: string, question: string) =>
  api.post<CopilotAnswer>(`/cases/${id(caseId)}/copilot`, { question });
export const copilotHistory = (caseId: string) => api.get<CopilotTurn[]>(`/cases/${id(caseId)}/copilot`);

// --- Insights and eval (merchant) -------------------------------------------------

export const insightsSummary = (range: { from?: string; to?: string } = {}) =>
  api.get<InsightsSummary>('/insights/summary', { ...range });
/** Shape is defined by eval/ (B5); typed there. */
export const latestEval = () => api.get<unknown>('/eval/latest');
