import { createBrowserRouter, Outlet, RouterProvider } from 'react-router';
import { AppShell, ComingSoon, type NavItem } from './components/app-shell';
import { CaseDetailPage } from './dashboard/case-detail-page';
import { CasesPage } from './dashboard/cases-page';
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
      { index: true, element: <CasesPage /> },
      { path: 'cases/:caseId', element: <CaseDetailPage /> },
      { path: 'insights', element: <ComingSoon title="Insights" pr="B8 (AG Studio dashboard)" /> },
      { path: 'eval', element: <ComingSoon title="Eval report" pr="B8 (eval report page)" /> },
    ],
  },
  { path: '*', element: <ComingSoon title="Page not found" pr="no PR: check the address" /> },
]);

export function App() {
  return <RouterProvider router={router} />;
}
