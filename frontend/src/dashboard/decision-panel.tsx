// The draft editor and the merchant's decision. This is the only place in the UI that calls
// `decideCase`, and approve always needs a second, explicit click: POST /cases/{id}/approve is
// the one endpoint that moves money or sends evidence.

import { useEffect, useState } from 'react';
import { ApiError } from '@/api/client';
import { decideCase } from '@/api/endpoints';
import type { CaseDecision, CaseDetail } from '@/api/types';
import { errorMessage, formatMoney } from '@/store/format';
import { DISPUTE_NOTE_LIMIT, REFUND_NOTE_LIMIT } from './labels';

type Pending = 'approve' | 'decline' | null;

export function DecisionPanel({ detail, onChanged }: { detail: CaseDetail; onChanged: (updated?: CaseDetail) => void }) {
  const isDispute = detail.source === 'dispute';
  const original = detail.draft?.text ?? '';
  const limit = isDispute ? DISPUTE_NOTE_LIMIT : REFUND_NOTE_LIMIT;
  const [text, setText] = useState(original);
  const [pending, setPending] = useState<Pending>(null);
  const [sending, setSending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => setText(original), [original]);

  const amount = detail.amount && detail.currency ? formatMoney(detail.amount, detail.currency) : 'the full amount';
  const approveLabel = isDispute ? 'Send evidence to PayPal' : `Approve refund of ${amount}`;
  const consequence =
    pending === 'decline'
      ? 'This closes the case. Nothing is sent to PayPal.'
      : isDispute
        ? 'This sends the note and any attached documents to PayPal as your dispute response. It cannot be undone.'
        : `This refunds ${amount} to the buyer through PayPal, with the note above. It cannot be undone.`;

  const edited = text.trim() !== original.trim();
  const tooLong = text.length > limit;

  const decide = async (action: CaseDecision['action']) => {
    setSending(true);
    setMessage(null);
    try {
      const updated = await decideCase(detail.id, {
        action,
        version: detail.version,
        ...(edited ? { draft_text: text.trim() } : {}),
      });
      setPending(null);
      onChanged(updated);
    } catch (e) {
      setMessage(errorMessage(e));
      setPending(null);
      // 409: someone else changed it. 502: PayPal failed and the case has a new version.
      if (e instanceof ApiError && (e.status === 409 || e.status === 502)) onChanged();
    } finally {
      setSending(false);
    }
  };

  if (detail.status !== 'ready') return null;

  return (
    <section className="rounded-lg border border-pp-border bg-white p-5">
      <h2 className="font-semibold text-pp-navy">{isDispute ? 'Dispute response' : 'Refund note to the buyer'}</h2>
      <p className="mt-1 text-xs text-pp-muted">
        Every number, date and ID must exist in PayPal's record. Edits are checked when you approve.
      </p>
      <textarea
        aria-label={isDispute ? 'Dispute response' : 'Refund note'}
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={isDispute ? 8 : 4}
        disabled={sending}
        className="mt-3 w-full rounded border border-pp-border px-3 py-2 text-sm outline-none focus:border-pp-blue"
      />
      <p className={`text-xs ${tooLong ? 'text-pp-danger' : 'text-pp-muted'}`}>
        {text.length}/{limit}
        {edited && ' · edited'}
      </p>

      {message && <p role="alert" className="mt-2 text-sm text-pp-danger">{message}</p>}

      {pending ? (
        <div className="mt-4 rounded border border-pp-border bg-pp-canvas p-3">
          <p className="text-sm">{consequence}</p>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              disabled={sending}
              onClick={() => void decide(pending)}
              className="rounded-full bg-pp-blue px-4 py-2 text-sm font-semibold text-white hover:bg-pp-blue-dark disabled:bg-pp-border disabled:text-pp-muted"
            >
              {sending ? 'Working…' : pending === 'approve' ? 'Yes, confirm' : 'Yes, decline'}
            </button>
            <button type="button" disabled={sending} onClick={() => setPending(null)} className="rounded-full border border-pp-border px-4 py-2 text-sm font-semibold">
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={tooLong || !text.trim()}
            onClick={() => setPending('approve')}
            className="rounded-full bg-pp-blue px-4 py-2 text-sm font-semibold text-white hover:bg-pp-blue-dark disabled:bg-pp-border disabled:text-pp-muted"
          >
            {approveLabel}
          </button>
          <button type="button" onClick={() => setPending('decline')} className="rounded-full border border-pp-border px-4 py-2 text-sm font-semibold text-pp-navy hover:bg-pp-canvas">
            Decline
          </button>
        </div>
      )}
    </section>
  );
}
