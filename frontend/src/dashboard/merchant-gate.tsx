// Everything under /merchant needs a merchant session. The session cookie is HttpOnly, so
// GET /api/auth/me is how the page finds out; it also restores the CSRF token after a reload.

import { useEffect, useState, type FormEvent } from 'react';
import { Outlet } from 'react-router';
import { ApiError } from '@/api/client';
import { login, logout, me } from '@/api/endpoints';
import { errorMessage } from '@/store/format';

type State = 'checking' | 'signed-out' | 'signed-in';

export function MerchantGate() {
  const [state, setState] = useState<State>('checking');
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    me().then(
      () => setState('signed-in'),
      (e: unknown) => {
        if (!(e instanceof ApiError && e.status === 401)) setMessage(errorMessage(e));
        setState('signed-out');
      },
    );
  }, []);

  if (state === 'checking') return <p className="text-sm text-pp-muted">Loading…</p>;
  if (state === 'signed-out') {
    return <LoginForm notice={message} onSignedIn={() => setState('signed-in')} />;
  }

  const onSignOut = async () => {
    try {
      await logout();
    } finally {
      setMessage(null);
      setState('signed-out');
    }
  };

  return (
    <>
      <div className="mb-2 flex justify-end">
        <button type="button" onClick={() => void onSignOut()} className="text-sm font-semibold text-pp-blue hover:underline">
          Sign out
        </button>
      </div>
      <Outlet />
    </>
  );
}

export function LoginForm({ notice, onSignedIn }: { notice?: string | null; onSignedIn: () => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [sending, setSending] = useState(false);
  const [message, setMessage] = useState<string | null>(notice ?? null);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSending(true);
    setMessage(null);
    try {
      await login(email.trim(), password);
      onSignedIn();
    } catch (err) {
      setMessage(errorMessage(err));
    } finally {
      setPassword('');
      setSending(false);
    }
  };

  return (
    <section className="mx-auto w-full max-w-sm">
      <h1 className="text-[28px] font-semibold text-pp-navy">Merchant sign in</h1>
      <form onSubmit={(e) => void onSubmit(e)} className="mt-6 flex flex-col gap-4 rounded-lg border border-pp-border bg-white p-5">
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-semibold">Email</span>
          <input
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="h-10 rounded border border-pp-border px-3 outline-none focus:border-pp-blue"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-semibold">Password</span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="h-10 rounded border border-pp-border px-3 outline-none focus:border-pp-blue"
          />
        </label>
        {message && <p role="alert" className="text-sm text-pp-danger">{message}</p>}
        <button
          type="submit"
          disabled={sending}
          className="rounded-full bg-pp-blue px-5 py-2 text-sm font-semibold text-white hover:bg-pp-blue-dark disabled:bg-pp-border disabled:text-pp-muted"
        >
          {sending ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </section>
  );
}
