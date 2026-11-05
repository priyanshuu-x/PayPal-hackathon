import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { listProducts } from '@/api/endpoints';
import type { Product } from '@/api/types';
import { cartCount, MAX_QUANTITY, useCart } from './cart';
import { errorMessage, formatMoney } from './format';

// Product art for the fictional store: a colour tile with the product's initials, so no real
// brand imagery or third-party image host is involved.
const TILE_COLOURS = ['var(--pp-chart-1)', 'var(--pp-chart-3)', 'var(--pp-chart-4)', 'var(--pp-chart-5)', 'var(--pp-chart-6)', 'var(--pp-chart-8)'];

export function ProductTile({ product, index }: { product: Pick<Product, 'name'>; index: number }) {
  const initials = product.name
    .split(/\s+/)
    .filter((w) => /^[A-Za-z]/.test(w))
    .slice(0, 2)
    .map((w) => w[0])
    .join('');
  return (
    <div
      aria-hidden
      className="flex aspect-[4/3] items-center justify-center rounded-t-lg text-3xl font-semibold text-white"
      style={{ background: TILE_COLOURS[index % TILE_COLOURS.length] }}
    >
      {initials}
    </div>
  );
}

export function ShopPage() {
  const { cart, dispatch } = useCart();
  const [products, setProducts] = useState<Product[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listProducts().then(setProducts, (e: unknown) => setError(errorMessage(e)));
  }, []);

  const count = cartCount(cart);

  return (
    <section className="mx-auto w-full max-w-5xl">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[28px] font-semibold text-pp-navy">Shop</h1>
          <p className="mt-1 text-sm text-pp-muted">A fictional store for trying ProofCheck. Payments use PayPal's sandbox, so no real money moves.</p>
        </div>
        <Link
          to="/checkout"
          className={`rounded-full px-5 py-2 text-sm font-semibold ${count ? 'bg-pp-blue text-white hover:bg-pp-blue-dark' : 'pointer-events-none bg-pp-border text-pp-muted'}`}
          aria-disabled={!count}
        >
          Checkout{count ? ` (${count})` : ''}
        </Link>
      </div>

      {error && <p role="alert" className="mt-6 text-sm text-pp-danger">{error}</p>}
      {!products && !error && <p className="mt-6 text-sm text-pp-muted">Loading products…</p>}

      {products && (
        <ul className="mt-6 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {products.map((product, i) => {
            const inCart = cart[product.id] ?? 0;
            return (
              <li key={product.id} className="flex flex-col rounded-lg border border-pp-border bg-white">
                <ProductTile product={product} index={i} />
                <div className="flex flex-1 flex-col gap-3 p-4">
                  <div className="flex items-baseline justify-between gap-2">
                    <h2 className="font-semibold text-pp-navy">{product.name}</h2>
                    <span className="text-sm font-semibold">{formatMoney(product.price, product.currency)}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => dispatch({ type: 'add', productId: product.id })}
                    disabled={inCart >= MAX_QUANTITY}
                    className="mt-auto rounded-full border border-pp-blue px-4 py-2 text-sm font-semibold text-pp-blue hover:bg-pp-highlight disabled:border-pp-border disabled:text-pp-muted"
                  >
                    {inCart ? `Add another (${inCart} in cart)` : 'Add to cart'}
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
