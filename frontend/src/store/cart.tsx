// The buyer's cart: product IDs and quantities only. Prices shown here are the catalog's, for
// display; the backend computes the real total when it creates the PayPal order.

import { createContext, useContext, useEffect, useReducer, type ReactNode } from 'react';

export const MAX_LINES = 10;
export const MAX_QUANTITY = 10;
const STORAGE_KEY = 'proofcheck:cart:v1';

export type Cart = Record<string, number>; // product_id -> quantity

export type CartAction =
  | { type: 'add'; productId: string }
  | { type: 'set'; productId: string; quantity: number }
  | { type: 'clear' };

export function cartReducer(cart: Cart, action: CartAction): Cart {
  switch (action.type) {
    case 'add': {
      const current = cart[action.productId] ?? 0;
      if (current === 0 && Object.keys(cart).length >= MAX_LINES) return cart;
      return { ...cart, [action.productId]: Math.min(current + 1, MAX_QUANTITY) };
    }
    case 'set': {
      const quantity = Math.min(Math.max(Math.trunc(action.quantity) || 0, 0), MAX_QUANTITY);
      const rest = Object.fromEntries(Object.entries(cart).filter(([id]) => id !== action.productId));
      if (quantity === 0) return rest;
      if (!(action.productId in cart) && Object.keys(cart).length >= MAX_LINES) return cart;
      return { ...rest, [action.productId]: quantity };
    }
    case 'clear':
      return {};
  }
}

export const cartCount = (cart: Cart) => Object.values(cart).reduce((sum, q) => sum + q, 0);

function loadCart(): Cart {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    if (typeof parsed !== 'object' || parsed === null) return {};
    let cart: Cart = {};
    for (const [id, q] of Object.entries(parsed)) {
      if (typeof q === 'number') cart = cartReducer(cart, { type: 'set', productId: id, quantity: q });
    }
    return cart;
  } catch {
    return {};
  }
}

const CartContext = createContext<{ cart: Cart; dispatch: (action: CartAction) => void } | null>(null);

export function CartProvider({ children }: { children: ReactNode }) {
  const [cart, dispatch] = useReducer(cartReducer, undefined, loadCart);
  useEffect(() => {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(cart));
    } catch {
      // Storage blocked: the cart still works for this page view.
    }
  }, [cart]);
  return <CartContext value={{ cart, dispatch }}>{children}</CartContext>;
}

export function useCart() {
  const value = useContext(CartContext);
  if (!value) throw new Error('useCart must be used inside CartProvider');
  return value;
}
