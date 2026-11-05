import { ApiError } from '@/api/client';

/** Money arrives as a decimal string; format it for display only (never for arithmetic sent anywhere). */
export function formatMoney(amount: string, currency: string): string {
  const value = Number(amount);
  if (Number.isNaN(value)) return `${amount} ${currency}`;
  return value.toLocaleString('en-US', { style: 'currency', currency });
}

export function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' });
}

/** A message safe to show: the backend's generic text, never an upstream body. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'rate_limited' && error.retryAfterSeconds) {
      return `Too many requests. Try again in ${error.retryAfterSeconds} seconds.`;
    }
    return error.message;
  }
  return 'Something went wrong. Please try again.';
}
