import type { ReactNode } from 'react';
import { NavLink } from 'react-router';

// The app frame, from the AG Grid + PayPal boilerplate: a white top bar and a light canvas.
// The store and the merchant pages share it; each area passes its own nav links.

export type NavItem = { to: string; label: string };

export function AppShell({ area, nav, children }: { area: string; nav: NavItem[]; children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col bg-pp-canvas font-paypal text-pp-ink">
      <header className="flex h-16 shrink-0 items-center gap-6 border-b border-pp-border bg-white px-6">
        <span className="text-lg font-semibold text-pp-navy">ProofCheck</span>
        <span className="text-sm text-pp-muted">{area}</span>
        <nav className="ml-auto flex items-center gap-1" aria-label={area}>
          {nav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end
              className={({ isActive }) =>
                `rounded px-3 py-2 text-sm font-semibold ${isActive ? 'bg-pp-highlight text-pp-blue' : 'text-pp-navy hover:text-pp-blue'}`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="flex min-h-0 flex-1 flex-col px-8 py-6">{children}</main>
    </div>
  );
}

// Placeholder body for pages a later PR fills in.
export function ComingSoon({ title, pr }: { title: string; pr: string }) {
  return (
    <section>
      <h1 className="text-[28px] font-semibold text-pp-navy">{title}</h1>
      <p className="mt-2 text-sm text-pp-muted">Built in {pr}.</p>
    </section>
  );
}
