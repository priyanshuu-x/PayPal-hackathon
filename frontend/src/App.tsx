import { createBrowserRouter, Outlet, RouterProvider } from 'react-router';
import { AppShell, ComingSoon, type NavItem } from './components/app-shell';

// Two areas: the fictional demo store for sandbox buyers, and the merchant app under /merchant.
const storeNav: NavItem[] = [
  { to: '/', label: 'Shop' },
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
      <AppShell area="Lotus & Loom Demo Store" nav={storeNav}>
        <Outlet />
      </AppShell>
    ),
    children: [
      { path: '/', element: <ComingSoon title="Shop" pr="B3 (demo store, JS SDK v6 checkout)" /> },
      { path: '/orders', element: <ComingSoon title="My orders" pr="B3 (orders and refund form)" /> },
    ],
  },
  {
    path: '/merchant',
    element: (
      <AppShell area="Merchant" nav={merchantNav}>
        <Outlet />
      </AppShell>
    ),
    children: [
      { index: true, element: <ComingSoon title="Cases" pr="B6 (AG Grid case list and case detail)" /> },
      { path: 'insights', element: <ComingSoon title="Insights" pr="B8 (AG Studio dashboard)" /> },
      { path: 'eval', element: <ComingSoon title="Eval report" pr="B8 (eval report page)" /> },
    ],
  },
  { path: '*', element: <ComingSoon title="Page not found" pr="no PR: check the address" /> },
]);

export function App() {
  return <RouterProvider router={router} />;
}
