import { useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router';
import { submitRefund } from '@/api/endpoints';
import { errorMessage } from './format';
import { ACCEPTED_TYPES, MAX_REASON, validateRefund, type RefundInput } from './validation';

export function RefundPage() {
  const { orderId = '' } = useParams();
  const [input, setInput] = useState<RefundInput>({ reason: '', email: '', file: null });
  const [errors, setErrors] = useState<Partial<Record<keyof RefundInput, string>>>({});
  const [sending, setSending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [caseId, setCaseId] = useState<string | null>(null);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const found = validateRefund(input);
    setErrors(found);
    if (Object.keys(found).length || !input.file) return;
    setSending(true);
    setMessage(null);
    try {
      const email = input.email.trim();
      const result = await submitRefund({ orderId, reason: input.reason.trim(), file: input.file, ...(email ? { email } : {}) });
      setCaseId(result.case_id);
    } catch (err) {
      setMessage(errorMessage(err));
    } finally {
      setSending(false);
    }
  };

  if (caseId) {
    return (
      <section className="mx-auto w-full max-w-2xl">
        <h1 className="text-[28px] font-semibold text-pp-navy">Refund request sent</h1>
        <p className="mt-3 text-sm">
          The store will review your request and reply to you. Your reference is <span className="font-mono">{caseId}</span>.
        </p>
        <Link to="/orders" className="mt-6 inline-block text-sm font-semibold text-pp-blue hover:underline">
          Back to my orders
        </Link>
      </section>
    );
  }

  return (
    <section className="mx-auto w-full max-w-2xl">
      <Link to="/orders" className="text-sm font-semibold text-pp-blue hover:underline">← My orders</Link>
      <h1 className="mt-3 text-[28px] font-semibold text-pp-navy">Request a refund</h1>
      <p className="mt-1 text-sm text-pp-muted">
        Order <span className="font-mono">{orderId}</span>
      </p>

      <form onSubmit={(e) => void onSubmit(e)} noValidate className="mt-6 flex flex-col gap-5 rounded-lg border border-pp-border bg-white p-5">
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-semibold">What went wrong?</span>
          <textarea
            value={input.reason}
            onChange={(e) => setInput((s) => ({ ...s, reason: e.target.value }))}
            maxLength={MAX_REASON}
            rows={4}
            aria-invalid={Boolean(errors.reason)}
            className="rounded border border-pp-border px-3 py-2 outline-none focus:border-pp-blue aria-[invalid=true]:border-pp-danger"
          />
          <span className="text-xs text-pp-muted">{input.reason.length}/{MAX_REASON}</span>
          {errors.reason && <span className="text-xs text-pp-danger">{errors.reason}</span>}
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-semibold">Receipt, screenshot or photo</span>
          <input
            type="file"
            accept={ACCEPTED_TYPES.join(',')}
            onChange={(e) => setInput((s) => ({ ...s, file: e.target.files?.[0] ?? null }))}
            aria-invalid={Boolean(errors.file)}
          />
          <span className="text-xs text-pp-muted">PNG, JPEG, WebP or PDF, up to 10 MB.</span>
          {errors.file && <span className="text-xs text-pp-danger">{errors.file}</span>}
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-semibold">
            Email <span className="font-normal text-pp-muted">(optional, for the store's reply)</span>
          </span>
          <input
            type="email"
            value={input.email}
            onChange={(e) => setInput((s) => ({ ...s, email: e.target.value }))}
            aria-invalid={Boolean(errors.email)}
            className="h-10 rounded border border-pp-border px-3 outline-none focus:border-pp-blue aria-[invalid=true]:border-pp-danger"
          />
          {errors.email && <span className="text-xs text-pp-danger">{errors.email}</span>}
        </label>

        {message && <p role="alert" className="text-sm text-pp-danger">{message}</p>}
        <button
          type="submit"
          disabled={sending}
          className="self-start rounded-full bg-pp-blue px-5 py-2 text-sm font-semibold text-white hover:bg-pp-blue-dark disabled:bg-pp-border disabled:text-pp-muted"
        >
          {sending ? 'Sending…' : 'Send refund request'}
        </button>
      </form>
    </section>
  );
}
