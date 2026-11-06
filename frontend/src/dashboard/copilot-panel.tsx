// The Case Copilot chat. Questions and answers are shown as plain text only (CLAUDE.md rule 12):
// answers are model output and can quote buyer-written dispute text from PayPal. The Copilot can
// only read; approving or refunding stays with the decision panel.

import { useEffect, useRef, useState, type FormEvent } from 'react';
import { askCopilot, copilotHistory } from '@/api/endpoints';
import type { CopilotTool, CopilotTurn } from '@/api/types';
import { errorMessage, formatDate } from '@/store/format';

const MAX_QUESTION = 500;
const SUGGESTIONS = ['Show the tracking for this order', 'What was the original order total?', 'Are there open disputes on this order?'];

const TOOL_LABELS: Record<CopilotTool, string> = {
  get_order_details: 'Order lookup',
  list_disputes: 'Dispute list',
  get_dispute: 'Dispute lookup',
  get_shipment_tracking: 'Tracking lookup',
  list_transactions: 'Transaction list',
};

export function CopilotPanel({ caseId }: { caseId: string }) {
  const [turns, setTurns] = useState<CopilotTurn[]>([]);
  const [question, setQuestion] = useState('');
  const [asking, setAsking] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLOListElement>(null);

  useEffect(() => {
    let active = true;
    copilotHistory(caseId).then(
      (history) => {
        if (active) setTurns(history);
      },
      () => undefined, // the panel still works without past turns
    );
    return () => {
      active = false;
    };
  }, [caseId]);

  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [turns, asking]);

  const ask = async (text: string) => {
    const q = text.trim();
    if (!q || q.length > MAX_QUESTION || asking) return;
    setAsking(q);
    setError(null);
    try {
      const result = await askCopilot(caseId, q);
      setTurns((current) => [...current, { ...result, question: q, created_at: new Date().toISOString() }]);
      setQuestion('');
    } catch (e) {
      setError(errorMessage(e));
      setQuestion(q); // keep it so the merchant can try again
    } finally {
      setAsking(null);
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void ask(question);
  };

  return (
    <section className="rounded-lg border border-pp-border bg-white p-5">
      <h2 className="font-semibold text-pp-navy">Case Copilot</h2>
      <p className="mt-1 text-xs text-pp-muted">
        Answers from live PayPal data. It can only read: it cannot approve, refund or change the verdict.
      </p>

      {(turns.length > 0 || asking) && (
        <ol ref={logRef} aria-label="Copilot conversation" className="mt-3 flex max-h-96 flex-col gap-3 overflow-y-auto text-sm">
          {turns.map((t, i) => (
            <li key={`${t.created_at}-${i}`} className="flex flex-col gap-1">
              <p className="self-end rounded-lg bg-pp-highlight px-3 py-2 whitespace-pre-wrap break-words">{t.question}</p>
              <p className="rounded-lg bg-pp-canvas px-3 py-2 whitespace-pre-wrap break-words">{t.answer}</p>
              <p className="text-xs text-pp-muted">
                {t.tools_called.length > 0 ? `Checked: ${[...new Set(t.tools_called)].map((name) => TOOL_LABELS[name] ?? 'PayPal lookup').join(', ')}` : 'No PayPal lookup'}
                {' · '}
                {formatDate(t.created_at)}
              </p>
            </li>
          ))}
          {asking && (
            <li className="flex flex-col gap-1">
              <p className="self-end rounded-lg bg-pp-highlight px-3 py-2 whitespace-pre-wrap break-words">{asking}</p>
              <p role="status" className="px-3 py-2 text-pp-muted">Checking PayPal…</p>
            </li>
          )}
        </ol>
      )}

      {turns.length === 0 && !asking && (
        <div className="mt-3 flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <button key={s} type="button" onClick={() => void ask(s)} className="rounded-full border border-pp-border px-3 py-1 text-xs text-pp-navy hover:bg-pp-highlight">
              {s}
            </button>
          ))}
        </div>
      )}

      {error && <p role="alert" className="mt-3 text-sm text-pp-danger">{error}</p>}

      <form onSubmit={onSubmit} className="mt-3 flex gap-2">
        <input
          aria-label="Ask the Copilot"
          value={question}
          maxLength={MAX_QUESTION}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask about this case"
          className="h-10 min-w-0 flex-1 rounded border border-pp-border px-3 text-sm outline-none focus:border-pp-blue"
        />
        <button
          type="submit"
          disabled={!question.trim() || asking !== null}
          className="rounded-full bg-pp-blue px-4 py-2 text-sm font-semibold text-white hover:bg-pp-blue-dark disabled:bg-pp-border disabled:text-pp-muted"
        >
          Ask
        </button>
      </form>
    </section>
  );
}
