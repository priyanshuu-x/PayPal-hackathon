// Checkout with PayPal JS SDK v6. The backend creates and captures the order (prices are computed
// server-side); the browser only sends product IDs, quantities and the shipping address.
// Flow: GET /api/store/sdk-config -> createInstance({ clientToken }) -> eligibility ->
// <paypal-button> click -> session.start(..., createOrder()) -> onApprove -> capture.

import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import { Link, useNavigate } from 'react-router';
import { ApiError } from '@/api/client';
import { captureOrder, createOrder, getSdkConfig, listProducts } from '@/api/endpoints';
import type { Product, ShippingAddress } from '@/api/types';
import { useCart } from './cart';
import { errorMessage, formatMoney } from './format';
import { createCheckoutInstance, type OneTimePaymentSession } from './paypal-sdk';
import { cleanShipping, validateShipping, type ShippingErrors } from './validation';

const SHIPPING_FLAT_CENTS = 499; // display only; the backend adds the real fee (store_catalog.SHIPPING_FLAT)

// A fictional address so the sandbox flow is quick to try. The buyer can change it.
const DEMO_ADDRESS: ShippingAddress = {
  name: 'Test Buyer',
  address_line_1: '1 Main St',
  address_line_2: '',
  admin_area_2: 'San Jose',
  admin_area_1: 'CA',
  postal_code: '95131',
  country_code: 'US',
};

const FIELDS: { key: keyof ShippingAddress; label: string; optional?: boolean }[] = [
  { key: 'name', label: 'Full name' },
  { key: 'address_line_1', label: 'Address line 1' },
  { key: 'address_line_2', label: 'Address line 2', optional: true },
  { key: 'admin_area_2', label: 'City' },
  { key: 'admin_area_1', label: 'State / region' },
  { key: 'postal_code', label: 'Postal code' },
  { key: 'country_code', label: 'Country code' },
];

type PayState =
  | { kind: 'loading' }
  | { kind: 'ready' }
  | { kind: 'unavailable'; message: string }
  | { kind: 'paying' }
  | { kind: 'capturing' };

const toCents = (amount: string) => Math.round(Number(amount) * 100);
const fromCents = (cents: number) => (cents / 100).toFixed(2);

export function CheckoutPage() {
  const { cart, dispatch } = useCart();
  const navigate = useNavigate();
  const [products, setProducts] = useState<Product[] | null>(null);
  const [shipping, setShipping] = useState<ShippingAddress>(DEMO_ADDRESS);
  const [errors, setErrors] = useState<ShippingErrors>({});
  const [pay, setPay] = useState<PayState>({ kind: 'loading' });
  const [message, setMessage] = useState<string | null>(null);
  const sessionRef = useRef<OneTimePaymentSession | null>(null);

  // Latest values for the SDK callbacks, which are registered once.
  const latest = useRef({ navigate, dispatch });
  latest.current = { navigate, dispatch };

  useEffect(() => {
    listProducts().then(setProducts, (e: unknown) => setMessage(errorMessage(e)));
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const config = await getSdkConfig();
        const instance = await createCheckoutInstance(config.client_token);
        if (cancelled) return;
        if (!instance) {
          setPay({ kind: 'unavailable', message: 'PayPal is not available for this browser or region.' });
          return;
        }
        sessionRef.current = instance.createPayPalOneTimePaymentSession({
          onApprove: async ({ orderId }) => {
            setPay({ kind: 'capturing' });
            try {
              await captureOrder(orderId);
              latest.current.dispatch({ type: 'clear' });
              void latest.current.navigate('/orders', { state: { paidOrderId: orderId } });
            } catch (e) {
              setMessage(errorMessage(e));
              setPay({ kind: 'ready' });
            }
          },
          onCancel: () => {
            setMessage('Payment cancelled. Your cart is still here.');
            setPay({ kind: 'ready' });
          },
          onError: () => {
            setMessage('PayPal could not complete the payment. Please try again.');
            setPay({ kind: 'ready' });
          },
        });
        setPay({ kind: 'ready' });
      } catch (e) {
        if (cancelled) return;
        const message = e instanceof ApiError ? errorMessage(e) : 'PayPal could not be loaded. Check your connection and reload.';
        setPay({ kind: 'unavailable', message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const lines = Object.entries(cart).flatMap(([id, quantity]) => {
    const product = products?.find((p) => p.id === id);
    return product ? [{ product, quantity }] : [];
  });
  const subtotalCents = lines.reduce((sum, l) => sum + toCents(l.product.price) * l.quantity, 0);
  const currency = lines[0]?.product.currency ?? 'USD';

  const onField = (key: keyof ShippingAddress) => (e: ChangeEvent<HTMLInputElement>) =>
    setShipping((s) => ({ ...s, [key]: e.target.value }));

  // Runs inside the button's click, so session.start keeps the browser's user activation:
  // createOrder() is passed as a promise and must not be awaited first.
  const onPayClick = () => {
    const session = sessionRef.current;
    if (!session || pay.kind !== 'ready' || lines.length === 0) return;
    const found = validateShipping(shipping);
    setErrors(found);
    if (Object.keys(found).length) {
      setMessage('Check the shipping address.');
      return;
    }
    setMessage(null);
    setPay({ kind: 'paying' });
    const orderPromise = createOrder({
      items: lines.map((l) => ({ product_id: l.product.id, quantity: l.quantity })),
      shipping: cleanShipping(shipping),
    }).then((order) => ({ orderId: order.order_id }));
    orderPromise.catch((e: unknown) => {
      setMessage(errorMessage(e));
      setPay({ kind: 'ready' });
    });
    session.start({ presentationMode: 'auto' }, orderPromise).catch(() => {
      setPay((p) => (p.kind === 'paying' ? { kind: 'ready' } : p));
    });
  };

  if (products && lines.length === 0 && pay.kind !== 'capturing') {
    return (
      <section className="mx-auto w-full max-w-3xl">
        <h1 className="text-[28px] font-semibold text-pp-navy">Checkout</h1>
        <p className="mt-2 text-sm text-pp-muted">
          Your cart is empty. <Link to="/" className="font-semibold text-pp-blue hover:underline">Go to the shop</Link>
        </p>
      </section>
    );
  }

  return (
    <section className="mx-auto grid w-full max-w-5xl gap-8 lg:grid-cols-[1fr_360px]">
      <div>
        <h1 className="text-[28px] font-semibold text-pp-navy">Checkout</h1>
        <h2 className="mt-6 text-sm font-semibold uppercase tracking-wider text-pp-muted">Shipping address</h2>
        <form className="mt-3 grid gap-4 sm:grid-cols-2" onSubmit={(e) => e.preventDefault()} noValidate>
          {FIELDS.map(({ key, label, optional }) => (
            <label key={key} className={`flex flex-col gap-1 text-sm ${key === 'name' || key.startsWith('address') ? 'sm:col-span-2' : ''}`}>
              <span className="font-semibold">
                {label}
                {optional && <span className="font-normal text-pp-muted"> (optional)</span>}
              </span>
              <input
                value={shipping[key] ?? ''}
                onChange={onField(key)}
                aria-invalid={Boolean(errors[key])}
                className="h-10 rounded border border-pp-border bg-white px-3 outline-none focus:border-pp-blue aria-[invalid=true]:border-pp-danger"
              />
              {errors[key] && <span className="text-xs text-pp-danger">{errors[key]}</span>}
            </label>
          ))}
        </form>
      </div>

      <aside className="h-fit rounded-lg border border-pp-border bg-white p-5">
        <h2 className="font-semibold text-pp-navy">Order summary</h2>
        <ul className="mt-3 divide-y divide-pp-border text-sm">
          {lines.map(({ product, quantity }) => (
            <li key={product.id} className="flex items-center justify-between gap-3 py-2">
              <span className="min-w-0 flex-1">{product.name}</span>
              <input
                type="number"
                min={0}
                max={10}
                value={quantity}
                onChange={(e) => dispatch({ type: 'set', productId: product.id, quantity: Number(e.target.value) })}
                aria-label={`Quantity of ${product.name}`}
                className="h-8 w-14 rounded border border-pp-border px-2"
                disabled={pay.kind === 'paying' || pay.kind === 'capturing'}
              />
              <span className="w-20 text-right">{formatMoney(fromCents(toCents(product.price) * quantity), currency)}</span>
            </li>
          ))}
        </ul>
        <dl className="mt-3 space-y-1 border-t border-pp-border pt-3 text-sm">
          <div className="flex justify-between"><dt className="text-pp-muted">Shipping</dt><dd>{formatMoney(fromCents(SHIPPING_FLAT_CENTS), currency)}</dd></div>
          <div className="flex justify-between font-semibold"><dt>Total</dt><dd>{formatMoney(fromCents(subtotalCents + SHIPPING_FLAT_CENTS), currency)}</dd></div>
        </dl>
        <p className="mt-1 text-xs text-pp-muted">The store's server sets the final total.</p>

        <div className="mt-5 min-h-12">
          {pay.kind === 'loading' && <p className="text-sm text-pp-muted">Loading PayPal…</p>}
          {pay.kind === 'unavailable' && <p role="alert" className="text-sm text-pp-danger">{pay.message}</p>}
          {pay.kind === 'capturing' && <p className="text-sm text-pp-muted">Completing your payment…</p>}
          {(pay.kind === 'ready' || pay.kind === 'paying') && (
            <paypal-button type="pay" class="paypal-gold" onClick={onPayClick} aria-disabled={pay.kind === 'paying'} />
          )}
        </div>
        {message && <p role="status" className="mt-3 text-sm text-pp-danger">{message}</p>}
        <p className="mt-4 text-xs text-pp-muted">Sandbox only: sign in with a PayPal sandbox buyer account.</p>
      </aside>
    </section>
  );
}
