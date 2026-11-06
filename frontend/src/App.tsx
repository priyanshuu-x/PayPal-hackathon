import { createBrowserRouter, Outlet, RouterProvider } from 'react-router';
import { AppShell, ComingSoon, type NavItem } from './components/app-shell';
import { MerchantGate } from './dashboard/merchant-gate';
import { CartProvider } from './store/cart';
import { CheckoutPage } from './store/checkout-page';
import { OrdersPage } from './store/orders-page';
import { RefundPage } from './store/refund-page';
import { ShopPage } from './store/shop-page';

// Two areas: the fictional demo store for sandbox buyers, and the merchant app under /merchant.
const storeNav: NavItem[] = [
  { to: '/', label: 'Shop' },
  { to: '/checkout', label: 'Checkout' },
  { to: '/orders', label: 'My orders' },
];

const merchantNav: NavItem[] = [
  { to: '/merchant', label: 'Cases' },
  { to: '/merchant/insights', label: 'Insights' },
  { to: '/merchant/eval', label: 'Eval report' },
];

const router = createBrowserRouter([
  {
    element: (
      <CartProvider>
        <AppShell area="Lotus & Loom Demo Store" nav={storeNav}>
          <Outlet />
        </AppShell>
      </CartProvider>
    ),
    children: [
      { path: '/', element: <ShopPage /> },
      { path: '/checkout', element: <CheckoutPage /> },
      { path: '/orders', element: <OrdersPage /> },
      { path: '/orders/:orderId/refund', element: <RefundPage /> },
    ],
  },
  {
    path: '/merchant',
    element: (
      <AppShell area="Merchant" nav={merchantNav}>
        <MerchantGate />
      </AppShell>
    ),
    children: [
      // Loaded on demand, so store buyers never download AG Grid or AG Studio.
      { index: true, lazy: async () => ({ Component: (await import('./dashboard/cases-page')).CasesPage }) },
      { path: 'cases/:caseId', lazy: async () => ({ Component: (await import('./dashboard/case-detail-page')).CaseDetailPage }) },
      { path: 'insights', lazy: async () => ({ Component: (await import('./insights/insights-page')).InsightsPage }) },
      { path: 'eval', lazy: async () => ({ Component: (await import('./insights/eval-page')).EvalPage }) },
    ],
  },
  { path: '*', element: <ComingSoon title="Page not found" pr="no PR: check the address" /> },
]);

export function App() {
  return <RouterProvider router={router} />;
}
