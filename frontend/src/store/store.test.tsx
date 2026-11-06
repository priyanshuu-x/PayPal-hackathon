import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/api/client';
import * as endpoints from '@/api/endpoints';
import type { BuyerOrder } from '@/api/types';
import { cartReducer, MAX_LINES, MAX_QUANTITY, type Cart } from './cart';
import { errorMessage } from './format';
import { OrdersPage } from './orders-page';
import { RefundPage } from './refund-page';
import { cleanShipping, MAX_UPLOAD_BYTES, validateRefund, validateShipping } from './validation';

afterEach(() => vi.restoreAllMocks());

describe('cartReducer', () => {
  it('adds up to the per-product limit', () => {
    let cart: Cart = {};
    for (let i = 0; i < MAX_QUANTITY + 3; i++) cart = cartReducer(cart, { type: 'add', productId: 'mug' });
    expect(cart).toEqual({ mug: MAX_QUANTITY });
  });

  it('caps the number of lines', () => {
    let cart: Cart = {};
    for (let i = 0; i < MAX_LINES + 2; i++) cart = cartReducer(cart, { type: 'add', productId: `p${i}` });
    expect(Object.keys(cart)).toHaveLength(MAX_LINES);
  });

  it('removes a line set to zero and clamps bad quantities', () => {
    expect(cartReducer({ mug: 2 }, { type: 'set', productId: 'mug', quantity: 0 })).toEqual({});
    expect(cartReducer({ mug: 2 }, { type: 'set', productId: 'mug', quantity: 99 })).toEqual({ mug: MAX_QUANTITY });
    expect(cartReducer({ mug: 2 }, { type: 'set', productId: 'mug', quantity: -4 })).toEqual({});
  });
});

describe('shipping validation', () => {
  const address = { name: 'A', address_line_1: '1 St', admin_area_2: 'City', admin_area_1: 'CA', postal_code: '1', country_code: 'us' };

  it('accepts a complete address and normalises it', () => {
    expect(validateShipping(address)).toEqual({});
    expect(cleanShipping({ ...address, address_line_2: '  ' })).toEqual({ ...address, country_code: 'US' });
  });

  it('flags missing, long and malformed fields', () => {
    const errors = validateShipping({ ...address, name: ' ', postal_code: 'x'.repeat(21), country_code: 'USA' });
    expect(Object.keys(errors).sort()).toEqual(['country_code', 'name', 'postal_code']);
  });
});

describe('refund validation', () => {
  const png = new File(['x'], 'r.png', { type: 'image/png' });

  it('needs a reason and a file', () => {
    expect(validateRefund({ reason: '', email: '', file: null })).toMatchObject({ reason: expect.any(String), file: expect.any(String) });
    expect(validateRefund({ reason: 'Damaged', email: '', file: png })).toEqual({});
  });

  it('rejects wrong types, big files and bad emails', () => {
    const exe = new File(['x'], 'a.exe', { type: 'application/x-msdownload' });
    const big = new File(['x'], 'big.png', { type: 'image/png' });
    Object.defineProperty(big, 'size', { value: MAX_UPLOAD_BYTES + 1 });
    expect(validateRefund({ reason: 'r', email: '', file: exe }).file).toBeDefined();
    expect(validateRefund({ reason: 'r', email: '', file: big }).file).toBeDefined();
    expect(validateRefund({ reason: 'r', email: 'nope', file: png }).email).toBeDefined();
  });
});

describe('errorMessage', () => {
  it('shows the wait time on 429 and hides unknown errors', () => {
    expect(errorMessage(new ApiError(429, 'rate_limited', 'Too many', undefined, 30))).toContain('30 seconds');
    expect(errorMessage(new Error('stack trace'))).not.toContain('stack trace');
  });
});

const orders: BuyerOrder[] = [
  { order_id: 'PAID1', capture_id: 'CAP1', status: 'captured', amount: '29.24', currency: 'USD', created_at: '2026-11-05T10:00:00Z', items: [{ name: 'Indigo Stoneware Mug', quantity: 1, unit_price: '16.25' }] },
  { order_id: 'OPEN1', capture_id: null, status: 'created', amount: '28.99', currency: 'USD', created_at: '2026-11-05T09:00:00Z', items: [{ name: 'Lotus Linen Tote', quantity: 1, unit_price: '24.00' }] },
];

describe('OrdersPage', () => {
  it('offers a refund only for paid orders', async () => {
    vi.spyOn(endpoints, 'listMyOrders').mockResolvedValue(orders);
    render(
      <MemoryRouter>
        <OrdersPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText('PAID1')).toBeInTheDocument();
    const links = screen.getAllByRole('link', { name: 'Request a refund' });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute('href', '/orders/PAID1/refund');
  });
});

describe('RefundPage', () => {
  const renderPage = () =>
    render(
      <MemoryRouter initialEntries={['/orders/PAID1/refund']}>
        <Routes>
          <Route path="/orders/:orderId/refund" element={<RefundPage />} />
        </Routes>
      </MemoryRouter>,
    );

  it('does not send an incomplete form', () => {
    const spy = vi.spyOn(endpoints, 'submitRefund');
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'Send refund request' }));
    expect(spy).not.toHaveBeenCalled();
    expect(screen.getByText('Attach a receipt, screenshot or photo')).toBeInTheDocument();
  });

  it('sends the request and shows the reference', async () => {
    const spy = vi.spyOn(endpoints, 'submitRefund').mockResolvedValue({ case_id: 'case-123' });
    renderPage();
    fireEvent.change(screen.getByRole('textbox', { name: /what went wrong/i }), { target: { value: 'Arrived broken' } });
    const file = new File(['x'], 'receipt.png', { type: 'image/png' });
    fireEvent.change(screen.getByLabelText(/receipt, screenshot or photo/i), { target: { files: [file] } });
    fireEvent.click(screen.getByRole('button', { name: 'Send refund request' }));
    expect(await screen.findByText('case-123')).toBeInTheDocument();
    expect(spy).toHaveBeenCalledWith({ orderId: 'PAID1', reason: 'Arrived broken', file });
  });

  it('shows the server message, as text, when the request is refused', async () => {
    vi.spyOn(endpoints, 'submitRefund').mockRejectedValue(new ApiError(409, 'conflict', '<b>A refund request is already open</b>'));
    renderPage();
    fireEvent.change(screen.getByRole('textbox', { name: /what went wrong/i }), { target: { value: 'x' } });
    fireEvent.change(screen.getByLabelText(/receipt, screenshot or photo/i), { target: { files: [new File(['x'], 'r.png', { type: 'image/png' })] } });
    fireEvent.click(screen.getByRole('button', { name: 'Send refund request' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('<b>A refund request is already open</b>'));
  });
});
