import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router';
import { listMyOrders } from '@/api/endpoints';
import type { BuyerOrder } from '@/api/types';
import { errorMessage, formatDate, formatMoney } from './format';

export function OrdersPage() {
  const location = useLocation();
  const paidOrderId = (location.state as { paidOrderId?: string } | null)?.paidOrderId;
  const [orders, setOrders] = useState<BuyerOrder[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listMyOrders().then(setOrders, (e: unknown) => setError(errorMessage(e)));
  }, []);

  return (
    <section className="mx-auto w-full max-w-4xl">
      <h1 className="text-[28px] font-semibold text-pp-navy">My orders</h1>
      <p className="mt-1 text-sm text-pp-muted">Orders paid from this browser. To ask for a refund, open the order and attach your receipt or a photo.</p>

      {paidOrderId && (
        <p role="status" className="mt-4 rounded border border-pp-success bg-white px-4 py-3 text-sm">
          Payment complete for order <span className="font-mono">{paidOrderId}</span>. Thank you!
        </p>
      )}
      {error && <p role="alert" className="mt-6 text-sm text-pp-danger">{error}</p>}
      {!orders && !error && <p className="mt-6 text-sm text-pp-muted">Loading orders…</p>}
      {orders?.length === 0 && (
        <p className="mt-6 text-sm text-pp-muted">
          No orders yet. <Link to="/" className="font-semibold text-pp-blue hover:underline">Go to the shop</Link>
        </p>
      )}

      {orders && orders.length > 0 && (
        <ul className="mt-6 space-y-4">
          {orders.map((order) => (
            <li key={order.order_id} className="rounded-lg border border-pp-border bg-white p-5">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div>
                  <p className="font-mono text-sm text-pp-navy">{order.order_id}</p>
                  <p className="text-xs text-pp-muted">{formatDate(order.created_at)}</p>
                </div>
                <div className="flex items-center gap-3">
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-semibold ${order.status === 'captured' ? 'bg-pp-highlight text-pp-success' : 'bg-pp-canvas text-pp-muted'}`}
                  >
                    {order.status === 'captured' ? 'Paid' : 'Not paid'}
                  </span>
                  <span className="font-semibold">{formatMoney(order.amount, order.currency)}</span>
                </div>
              </div>
              <ul className="mt-3 text-sm text-pp-ink">
                {order.items.map((item) => (
                  <li key={item.name}>
                    {item.quantity} × {item.name} <span className="text-pp-muted">at {formatMoney(item.unit_price, order.currency)}</span>
                  </li>
                ))}
              </ul>
              {order.status === 'captured' && (
                <Link
                  to={`/orders/${encodeURIComponent(order.order_id)}/refund`}
                  className="mt-4 inline-block rounded-full border border-pp-blue px-4 py-2 text-sm font-semibold text-pp-blue hover:bg-pp-highlight"
                >
                  Request a refund
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
