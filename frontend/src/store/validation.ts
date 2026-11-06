// Client-side checks that mirror the backend's limits, so the buyer gets a clear message before
// a request. The backend re-checks everything; these are for usability only.

import type { ShippingAddress } from '@/api/types';

export type ShippingErrors = Partial<Record<keyof ShippingAddress, string>>;

const LIMITS: Record<keyof ShippingAddress, number> = {
  name: 100,
  address_line_1: 100,
  address_line_2: 100,
  admin_area_2: 60,
  admin_area_1: 60,
  postal_code: 20,
  country_code: 2,
};

const REQUIRED: (keyof ShippingAddress)[] = ['name', 'address_line_1', 'admin_area_2', 'admin_area_1', 'postal_code', 'country_code'];

export function validateShipping(address: ShippingAddress): ShippingErrors {
  const errors: ShippingErrors = {};
  for (const key of Object.keys(LIMITS) as (keyof ShippingAddress)[]) {
    const value = (address[key] ?? '').trim();
    if (REQUIRED.includes(key) && !value) errors[key] = 'Required';
    else if (value.length > LIMITS[key]) errors[key] = `At most ${LIMITS[key]} characters`;
  }
  if (!errors.country_code && !/^[A-Za-z]{2}$/.test(address.country_code.trim())) {
    errors.country_code = 'Two-letter country code, e.g. US';
  }
  return errors;
}

/** Trimmed copy without an empty optional line, ready to send. */
export function cleanShipping(address: ShippingAddress): ShippingAddress {
  const line2 = address.address_line_2?.trim();
  return {
    name: address.name.trim(),
    address_line_1: address.address_line_1.trim(),
    ...(line2 ? { address_line_2: line2 } : {}),
    admin_area_2: address.admin_area_2.trim(),
    admin_area_1: address.admin_area_1.trim(),
    postal_code: address.postal_code.trim(),
    country_code: address.country_code.trim().toUpperCase(),
  };
}

export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
export const ACCEPTED_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'application/pdf'];
export const MAX_REASON = 1000;

export interface RefundInput {
  reason: string;
  email: string;
  file: File | null;
}

export function validateRefund({ reason, email, file }: RefundInput): Partial<Record<keyof RefundInput, string>> {
  const errors: Partial<Record<keyof RefundInput, string>> = {};
  if (!reason.trim()) errors.reason = 'Tell the store why you want a refund';
  else if (reason.length > MAX_REASON) errors.reason = `At most ${MAX_REASON} characters`;
  if (email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) errors.email = 'Enter a valid email or leave it empty';
  if (!file) errors.file = 'Attach a receipt, screenshot or photo';
  else if (file.size > MAX_UPLOAD_BYTES) errors.file = 'The file is larger than 10 MB';
  else if (file.type && !ACCEPTED_TYPES.includes(file.type)) errors.file = 'Use a PNG, JPEG, WebP or PDF file';
  return errors;
}
